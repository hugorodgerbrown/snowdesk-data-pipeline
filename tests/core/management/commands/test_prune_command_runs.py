"""
tests/core/management/commands/test_prune_command_runs.py — SNOW-1189.

``prune_command_runs`` does two things to the command run register: it
marks rows a killed process left ``RUNNING`` as ``INTERRUPTED``, and it
deletes ``SCHEDULER`` runs past their retention. Pinned: both windows from
both sides, nothing written without ``--commit``, every non-scheduled run
kept for good, and a run never sweeping its own row.
"""

from __future__ import annotations

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.core.management.commands.prune_command_runs import (
    SCHEDULED_RETENTION_DAYS,
    STALE_HOURS,
)
from apps.core.models import CommandRun
from tests.factories import CommandRunFactory

pytestmark = pytest.mark.django_db


def _running(hours_ago: float) -> CommandRun:
    """Create a RUNNING row started ``hours_ago`` hours ago."""
    return CommandRunFactory.create(
        status=CommandRun.STATUS.RUNNING,
        finished_at=None,
        exit_code=None,
        started_at=timezone.now() - timedelta(hours=hours_ago),
    )


def _scheduled(days_ago: float, **kwargs: object) -> CommandRun:
    """Create a finished SCHEDULER run started ``days_ago`` days ago."""
    started = timezone.now() - timedelta(days=days_ago)
    return CommandRunFactory.create(
        command="fetch_bulletins",
        invoked_via=CommandRun.INVOKED_VIA.SCHEDULER,
        started_at=started,
        finished_at=started,
        **kwargs,
    )


def _prune(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("prune_command_runs", *args, stdout=out)
    return out.getvalue()


def _status(run: CommandRun) -> str:
    """Return the row's status as stored now."""
    run.refresh_from_db()
    return run.status


def test_stale_running_rows_are_interrupted() -> None:
    """Older than the window: INTERRUPTED. Inside it: still RUNNING."""
    stale = _running(STALE_HOURS + 1)
    fresh = _running(STALE_HOURS - 1)
    _prune("--commit")
    assert _status(stale) == CommandRun.STATUS.INTERRUPTED
    assert _status(fresh) == CommandRun.STATUS.RUNNING
    assert stale.finished_at is not None
    assert stale.duration is not None


def test_old_scheduled_runs_are_deleted_and_everything_else_kept() -> None:
    """Only SCHEDULER runs expire; a shell run of any age stays."""
    old = _scheduled(SCHEDULED_RETENTION_DAYS + 1)
    recent = _scheduled(SCHEDULED_RETENTION_DAYS - 1)
    ancient_shell = CommandRunFactory.create(
        started_at=timezone.now() - timedelta(days=1000)
    )
    _prune("--commit")
    remaining = set(CommandRun.objects.exclude(command="prune_command_runs"))
    assert remaining == {recent, ancient_shell}
    assert old not in remaining


def test_nothing_is_written_without_commit() -> None:
    """The read-only run reports and changes nothing."""
    stale = _running(STALE_HOURS + 1)
    old = _scheduled(SCHEDULED_RETENTION_DAYS + 1)
    out = _prune()
    assert "Would mark 1" in out
    assert "would delete 1" in out
    assert "pass --commit" in out
    assert _status(stale) == CommandRun.STATUS.RUNNING
    assert CommandRun.objects.filter(pk=old.pk).exists()


def test_the_counts_land_on_its_own_row() -> None:
    """The prune records what it did, like any registered command."""
    _running(STALE_HOURS + 1)
    _scheduled(SCHEDULED_RETENTION_DAYS + 1)
    _prune("--commit")
    own = CommandRun.objects.get(command="prune_command_runs")
    assert own.counts == {"interrupted": 1, "deleted": 1}
    assert own.status == CommandRun.STATUS.SUCCEEDED


def test_a_quiet_run_says_so() -> None:
    """With nothing to do, it says zero and asks for nothing."""
    out = _prune()
    assert "Would mark 0" in out
    assert "pass --commit" not in out


def test_custom_windows() -> None:
    """--stale-hours and --days move the cut-offs."""
    stale = _running(2)
    _prune("--commit", "--stale-hours", "1")
    assert _status(stale) == CommandRun.STATUS.INTERRUPTED


def test_non_positive_windows_are_refused() -> None:
    """call_command skips argparse's type check; handle() repeats it."""
    with pytest.raises(CommandError):
        call_command("prune_command_runs", days=0, stdout=StringIO())
