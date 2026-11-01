"""
tests/routes/management/commands/test_backfill_crossed_regions.py — the SNOW-945 backfill.

Covers ``backfill_crossed_regions``: read-only by default, ``--commit``
fills routes and trips, a filled row is not selected again, a row outside
coverage is stored as ``[]`` and tallied, a failure exits non-zero, and the
run is registered with its tallies, zeros included.
"""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import CommandError, call_command

from apps.core.models import CommandRun
from tests.factories import MicroRegionFactory, RouteFactory, TripFactory

COMMAND = "backfill_crossed_regions"

# Covers the factories' default track (7.40–7.42 E, 46.10–46.12 N).
_BOX = {
    "type": "Polygon",
    "coordinates": [[[7.3, 46.0], [7.5, 46.0], [7.5, 46.2], [7.3, 46.2], [7.3, 46.0]]],
}


def _run(*args: Any) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command(COMMAND, *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
class TestBackfillCrossedRegions:
    """``backfill_crossed_regions``."""

    def test_read_only_by_default(self) -> None:
        """Nothing is written without --commit."""
        MicroRegionFactory.create(region_id="CH-8001", boundary=_BOX)
        route = RouteFactory.create()

        output = _run()

        route.refresh_from_db()
        assert route.crossed_regions is None
        assert "Would fill 1 route(s)" in output
        assert "Read-only run" in output

    def test_commit_fills_routes_and_trips(self) -> None:
        """Each row resolved from its own points."""
        MicroRegionFactory.create(region_id="CH-8001", boundary=_BOX)
        route = RouteFactory.create()
        trip = TripFactory.create()

        _run("--commit")

        route.refresh_from_db()
        trip.refresh_from_db()
        assert route.crossed_regions == ["CH-8001"]
        assert trip.crossed_regions == ["CH-8001"]

    def test_a_filled_row_is_skipped(self) -> None:
        """A stored list, even an empty one, is never selected."""
        RouteFactory.create(crossed_regions=["CH-1"])
        RouteFactory.create(crossed_regions=[])

        _run("--commit")

        run = CommandRun.objects.get(command=COMMAND)
        assert run.counts == {
            "routes": 0,
            "trips": 0,
            "outside_coverage": 0,
            "failed": 0,
        }

    def test_outside_coverage_is_stored_empty_and_tallied(self) -> None:
        """No region is an answer, stored as []."""
        route = RouteFactory.create()

        _run("--commit")

        route.refresh_from_db()
        assert route.crossed_regions == []
        run = CommandRun.objects.get(command=COMMAND)
        assert run.counts == {
            "routes": 1,
            "trips": 0,
            "outside_coverage": 1,
            "failed": 0,
        }

    def test_a_failure_exits_non_zero(self) -> None:
        """One bad row fails the run, and the others are still attempted."""
        RouteFactory.create()
        trip = TripFactory.create()
        calls = {"n": 0}

        def flaky(points: Any) -> list[str]:
            """Fail on the first row only."""
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("bad track")
            return []

        with (
            patch(
                "apps.routes.management.commands.backfill_crossed_regions."
                "resolve_track_regions",
                side_effect=flaky,
            ),
            pytest.raises(CommandError),
        ):
            _run("--commit")

        trip.refresh_from_db()
        assert trip.crossed_regions == []
        run = CommandRun.objects.get(command=COMMAND)
        assert run.counts["failed"] == 1
