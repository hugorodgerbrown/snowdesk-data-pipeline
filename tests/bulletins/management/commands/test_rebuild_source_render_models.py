"""
tests/bulletins/management/commands/test_rebuild_source_render_models.py — The command.

rebuild_source_render_models (SNOW-1270, SNOW-1346): read-only by default;
``--commit`` enqueues one task per stale row (or every row, forced, with
``--all``) and one per stale ``SourceDayRating``, tallies what it enqueued,
and exits non-zero when a task run inline fails. Tests run under
``ImmediateBackend``, so each task runs as it is enqueued; the tasks
themselves are tests/bulletins/services/test_render_rebuild.py's subject,
the rebuild tests/ingest/test_store.py's.
"""

from __future__ import annotations

from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import CommandRun
from apps.ingest.public import (
    DAY_RATING_VERSION,
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    SourceBulletin,
    SourceDayRating,
)
from tests.factories import SourceBulletinFactory, SourceDayRatingFactory

pytestmark = pytest.mark.django_db

_MODEL = {"version": RENDER_MODEL_VERSION, "marker": "built"}
_BUILD = "apps.ingest.store.build_render_model"
_COMMAND = "apps.bulletins.management.commands.rebuild_source_render_models"
_TASK = f"{_COMMAND}.rebuild_source_bulletin_task"
_RATING_TASK = f"{_COMMAND}.refresh_source_day_rating_task"


def _run(*args: str) -> str:
    """Call the command and return its stdout."""
    out = StringIO()
    call_command("rebuild_source_render_models", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def _current(**kwargs: object) -> SourceBulletin:
    """Return a bulletin whose model is current and indexed."""
    return SourceBulletinFactory.create(
        render_model={"version": RENDER_MODEL_VERSION},
        render_model_version=RENDER_MODEL_VERSION,
        target_date=date(2026, 1, 2),
        **kwargs,
    )


def test_runs_with_no_arguments_and_enqueues_nothing() -> None:
    """The bare invocation counts the stale rows and enqueues none."""
    row = SourceBulletinFactory.create()
    with patch(_TASK) as task:
        out = _run()
    task.enqueue.assert_not_called()
    assert "READ-ONLY" in out
    assert "Would enqueue 1 bulletin(s)" in out
    row.refresh_from_db()
    assert row.render_model == {}


def test_commit_rebuilds_the_stale_rows_only() -> None:
    """A current row is not enqueued; a stale one is rebuilt by its task."""
    stale = SourceBulletinFactory.create(render_model_version=1)
    current = _current()
    with patch(_BUILD, return_value=_MODEL):
        out = _run("--commit")
    assert "Enqueued 1 bulletin(s)" in out
    stale.refresh_from_db()
    current.refresh_from_db()
    assert stale.render_model == _MODEL
    assert current.render_model == {"version": RENDER_MODEL_VERSION}


def test_commit_enqueues_one_unforced_task_per_stale_row() -> None:
    """Each stale row is one task, handed its pk, no force, with ratings."""
    rows = SourceBulletinFactory.create_batch(2)
    _current()
    with patch(_TASK) as task:
        _run("--commit")
    calls = sorted(call.args for call in task.enqueue.call_args_list)
    assert calls == sorted((row.pk, False, True) for row in rows)


def test_a_second_commit_enqueues_nothing() -> None:
    """The first run brought every row current."""
    row = SourceBulletinFactory.create(
        caaml={"validTime": {"startTime": "2026-01-02T07:00Z"}, "regions": []}
    )
    with patch(_BUILD, return_value=_MODEL):
        _run("--commit")
    row.refresh_from_db()
    stamp = row.updated_at
    with patch(_TASK) as task:
        _run("--commit")
    task.enqueue.assert_not_called()
    row.refresh_from_db()
    assert row.updated_at == stamp


def test_all_walks_current_rows_too() -> None:
    """--all is for a builder fix that did not bump the version: forced."""
    current = _current()
    with patch(_BUILD, return_value=_MODEL):
        _run("--all", "--commit")
    current.refresh_from_db()
    assert current.render_model == _MODEL


def test_a_stale_day_rating_gets_its_own_task() -> None:
    """A DAY_RATING_VERSION bump with no render bump still reaches the row."""
    SourceDayRatingFactory.create(
        region_code="CH-9", date=date(2026, 1, 2), version=DAY_RATING_VERSION - 1
    )
    SourceDayRatingFactory.create(region_code="CH-8", date=date(2026, 1, 2))
    out = _run("--commit")
    assert "CH-9 2026-01-02" in out
    assert "and 1 stale day rating(s)" in out
    assert not SourceDayRating.objects.stale(DAY_RATING_VERSION).exists()


def test_a_preview_enqueues_no_day_rating_task() -> None:
    """Read-only covers the stale ratings too."""
    SourceDayRatingFactory.create(version=DAY_RATING_VERSION - 1)
    with patch(_RATING_TASK) as task:
        out = _run()
    task.enqueue.assert_not_called()
    assert "and 1 stale day rating(s)" in out


def test_the_run_is_registered_with_its_tallies() -> None:
    """Every count is tallied, zeros included."""
    SourceBulletinFactory.create()
    with patch(_BUILD, return_value=_MODEL):
        _run("--commit")
    run = CommandRun.objects.get(command="rebuild_source_render_models")
    assert run.counts == {"enqueued": 1, "day_ratings_enqueued": 0, "failed": 0}


def test_a_failed_build_exits_non_zero_without_aborting() -> None:
    """A bulletin the builder cannot read fails its task; the next still runs."""
    SourceBulletinFactory.create()
    good = SourceBulletinFactory.create()
    calls = {"n": 0}

    def _flaky(caaml: dict) -> dict:
        calls["n"] += 1
        if caaml["bulletinID"] != good.bulletin_id:
            raise RenderModelBuildError("boom")
        return _MODEL

    with (
        patch(_BUILD, side_effect=_flaky),
        pytest.raises(CommandError, match="1 rebuild task"),
    ):
        _run("--commit")
    assert calls["n"] == 2
    assert SourceBulletin.objects.filter(render_model_version=0).count() == 1
    good.refresh_from_db()
    assert good.render_model == _MODEL
