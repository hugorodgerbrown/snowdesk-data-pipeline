"""
tests/bulletins/management/commands/test_rebuild_source_render_models.py — The command.

rebuild_source_render_models (SNOW-1270): read-only by default, walks the
stale rows (or every row with ``--all``), tallies what it rebuilt, and exits
non-zero when a build fails. The rebuild itself is tested in
tests/ingest/test_store.py.
"""

from __future__ import annotations

from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import CommandRun
from apps.ingest.public import RENDER_MODEL_VERSION, SourceBulletin
from tests.factories import SourceBulletinFactory

pytestmark = pytest.mark.django_db

_MODEL = {"version": RENDER_MODEL_VERSION, "marker": "built"}
_BUILD = "apps.ingest.store.build_render_model"


def _run(*args: str) -> str:
    """Call the command and return its stdout."""
    out = StringIO()
    call_command("rebuild_source_render_models", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


def test_runs_with_no_arguments_and_writes_nothing() -> None:
    """The bare invocation counts the stale rows and saves none."""
    row = SourceBulletinFactory.create()
    with patch(_BUILD, return_value=_MODEL):
        out = _run()
    assert "READ-ONLY" in out
    assert "Would rebuild 1" in out
    row.refresh_from_db()
    assert row.render_model == {}


def test_commit_rebuilds_the_stale_rows_only() -> None:
    """A current row is not walked; a stale one is rebuilt."""
    stale = SourceBulletinFactory.create(render_model_version=1)
    current = SourceBulletinFactory.create(
        render_model={"version": RENDER_MODEL_VERSION},
        render_model_version=RENDER_MODEL_VERSION,
        target_date=date(2026, 1, 2),
    )
    with patch(_BUILD, return_value=_MODEL):
        out = _run("--commit")
    assert "Rebuilt 1" in out
    stale.refresh_from_db()
    current.refresh_from_db()
    assert stale.render_model == _MODEL
    assert current.render_model == {"version": RENDER_MODEL_VERSION}


def test_all_walks_current_rows_too() -> None:
    """--all is for a builder fix that did not bump the version."""
    current = SourceBulletinFactory.create(
        render_model={"version": RENDER_MODEL_VERSION},
        render_model_version=RENDER_MODEL_VERSION,
    )
    with patch(_BUILD, return_value=_MODEL):
        _run("--all", "--commit")
    current.refresh_from_db()
    assert current.render_model == _MODEL


def test_the_run_is_registered_with_its_tallies() -> None:
    """Every outcome is tallied, zeros included."""
    SourceBulletinFactory.create()
    with patch(_BUILD, return_value=_MODEL):
        _run("--commit")
    run = CommandRun.objects.get(command="rebuild_source_render_models")
    assert run.counts == {
        "rebuilt": 1,
        "unchanged": 0,
        "failed": 0,
        "day_ratings": 0,
    }


def test_a_failed_build_exits_non_zero() -> None:
    """A bulletin the builder cannot read fails the run, so cron sees it."""
    SourceBulletinFactory.create()
    with pytest.raises(CommandError, match="1 render model build"):
        _run("--commit")
    assert SourceBulletin.objects.get().render_model_version == 0
