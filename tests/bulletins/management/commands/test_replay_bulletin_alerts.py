"""
tests/bulletins/management/commands/test_replay_bulletin_alerts.py — The
season replay command (SNOW-949).
"""

from __future__ import annotations

import datetime
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.bulletins.services.replay import ReplayedAlert
from apps.core.models import CommandRun

WHEN = datetime.datetime(2026, 1, 16, 16, 0, tzinfo=datetime.UTC)
ALERTS = [
    ReplayedAlert(
        WHEN,
        WHEN,
        datetime.date(2026, 1, 17),
        "new",
        "SENT",
        "Verbier · Saturday: Moderate (2)",
        "Same as Friday.",
        "/map/",
    ),
    ReplayedAlert(
        WHEN,
        WHEN,
        datetime.date(2026, 1, 17),
        "update",
        "SKIPPED",
        "Skipped",
        "Nothing.",
        "/map/",
    ),
]


def run(*args: str) -> str:
    """Call the command with the engine patched; return stdout."""
    out = StringIO()
    with patch(
        "apps.bulletins.management.commands.replay_bulletin_alerts.replay_archive",
        return_value=ALERTS,
    ) as engine:
        call_command("replay_bulletin_alerts", *args, stdout=out)
    run.engine = engine  # type: ignore[attr-defined]
    return out.getvalue()


@pytest.mark.django_db
def test_prints_the_sent_schedule() -> None:
    """Sent alerts print with their body; skipped ones only with --all."""
    out = run()
    assert "Fri 16 Jan 17:00  Verbier · Saturday: Moderate (2)" in out
    assert "Same as Friday." in out
    assert "Skipped" not in out
    assert "1 sent, 1 skipped" in out
    assert CommandRun.objects.latest("id").counts == {"sent": 1, "skipped": 1}


@pytest.mark.django_db
def test_all_and_arguments_pass_through() -> None:
    """--all shows skips; --region/--source/--mode reach the engine."""
    out = run(
        "--all", "--region", "AT-07-14", "--source", "albina", "--mode", "CHANGES"
    )
    assert "[SKIPPED]  Skipped" in out
    run.engine.assert_called_once_with(  # type: ignore[attr-defined]
        source="ALBINA", region_id="AT-07-14", mode="CHANGES"
    )


@pytest.mark.django_db
def test_unknown_source() -> None:
    """An unknown provider fails."""
    with pytest.raises(CommandError):
        call_command("replay_bulletin_alerts", "--source", "NOPE", stdout=StringIO())
