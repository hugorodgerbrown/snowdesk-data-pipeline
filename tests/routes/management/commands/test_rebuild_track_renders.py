"""
tests/routes/management/commands/test_rebuild_track_renders.py — SNOW-1309.

``rebuild_track_renders``: read-only by default, enqueues (SNOW-1400) a
rebuild only for rows whose render is missing or stale, leaves a
never-walked tour alone, and lands in the command register with its
tallies. The tasks run inline here (``ImmediateBackend``).
"""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.core.models import CommandRun
from apps.routes.models import Route, SkiTour
from apps.trips.models import Trip
from tests.factories import RouteFactory, SkiTourFactory, TripFactory
from track.render import TRACK_RENDER_VERSION, is_current

pytestmark = pytest.mark.django_db

_KEPT: dict[str, Any] = {"v": 1, "segments": [], "walked_reversed": False}


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("rebuild_track_renders", *args, stdout=out)
    return out.getvalue()


def _stale() -> tuple[Route, Route, Trip, SkiTour, SkiTour]:
    """A current route, a stale route, a stale trip, a walked and an unwalked tour."""
    current = RouteFactory.create(name="Current")
    stale = RouteFactory.create(name="Stale", track_render={"v": 0})
    trip = TripFactory.create(track_render=None)
    walked = SkiTourFactory.create(slope_samples=_KEPT)
    unwalked = SkiTourFactory.create()
    return current, stale, trip, walked, unwalked


def test_a_dry_run_counts_and_writes_nothing() -> None:
    _, stale, trip, walked, _ = _stale()
    out = _run()
    assert "Would enqueue: 1 route(s), 1 trip(s), 1 tour(s)" in out
    assert "Stale" in out
    stale.refresh_from_db()
    trip.refresh_from_db()
    walked.refresh_from_db()
    assert stale.track_render == {"v": 0}
    assert trip.track_render is None
    assert walked.track_render is None
    run = CommandRun.objects.get(command="rebuild_track_renders")
    assert run.counts["routes"] == 1
    assert run.counts["failed"] == 0


def test_commit_rebuilds_only_the_stale_rows() -> None:
    current, stale, trip, walked, unwalked = _stale()
    before = current.updated_at
    _run("--commit")
    for row in (stale, trip, walked):
        row.refresh_from_db()
        assert is_current(row.track_render)
        assert row.track_render["v"] == TRACK_RENDER_VERSION
    current.refresh_from_db()
    assert current.updated_at == before
    unwalked.refresh_from_db()
    assert unwalked.track_render is None


def test_a_walked_tour_held_in_the_other_order_is_left_unwalked() -> None:
    """Its record no longer matches the line: nothing to build from, no walk."""
    tour = SkiTourFactory.create(slope_samples={**_KEPT, "walked_reversed": True})
    _run("--commit")
    tour.refresh_from_db()
    assert tour.track_render is None
    run = CommandRun.objects.get(command="rebuild_track_renders")
    assert run.counts["failed"] == 0


def test_a_second_commit_enqueues_nothing() -> None:
    """Every stale row was rebuilt, so the next run has no candidate."""
    _stale()
    _run("--commit")
    out = _run("--commit")
    assert "Enqueued: 0 route(s), 0 trip(s), 0 tour(s)" in out


def test_a_task_failure_exits_non_zero() -> None:
    """A row whose rebuild raises fails its task, and the run says so."""
    RouteFactory.create(track_render=None)
    with (
        mock.patch(
            "apps.routes.services.render_tasks.store_terrain",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(CommandError, match="1 failure"),
    ):
        _run("--commit")
