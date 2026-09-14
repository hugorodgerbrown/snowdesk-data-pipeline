"""
tests/trips/management/commands/test_backfill_trip_slope_samples.py

Covers ``backfill_trip_slope_samples`` (SNOW-962), the one path by which a
trip created before the column gains a terrain record — raised by review
on the pull request that added it, because nothing else would ever fill
one in.

The claims worth testing are about WHICH PATH a row takes:

  - a trip whose source route is present, sampled and geometrically
    identical INHERITS, and the tile origin is not asked;
  - a trip whose route is gone, unsampled, or no longer matches WALKS;
  - a read-only run does neither, and still reports the split;
  - a walk that learns nothing leaves the field null, so a later run
    retries it, and that is not a failure;
  - one failure does not abort the batch, and the command still exits
    non-zero.
"""

from __future__ import annotations

from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import RouteFactory, TripFactory, UserFactory

_BUILDER = (
    "apps.trips.management.commands.backfill_trip_slope_samples.build_slope_samples"
)

MERIDIAN_TRACK = [[7.0, 46.0, 1000.0], [7.0, 46.01, 1200.0]]

RECORD: dict[str, Any] = {
    "window_m": 10.0,
    "stride_m": 25.0,
    "grid": "snowdesk-terrain-5m-3035",
    "points": [[7.0, 46.0], [7.0, 46.01]],
    "segments": [{"angle_deg": 34.2, "aspect_deg": 180.0}],
    # A CURRENT record carries ``cruxes`` even when nothing was flagged
    # (SNOW-911) — the key's presence is what marks the row up to date.
    "cruxes": [],
}


def _route_record(angle_deg: float) -> dict[str, Any]:
    """Return a stored record distinguishable by its angle."""
    return {
        **RECORD,
        "segments": [{"angle_deg": angle_deg, "aspect_deg": 180.0}],
        "cruxes": [],
    }


def _run(*args: str) -> str:
    """Run the command and return its stdout."""
    out = StringIO()
    call_command("backfill_trip_slope_samples", *args, stdout=out, stderr=StringIO())
    return out.getvalue()


@pytest.mark.django_db
class TestTheCopyPath:
    """A record already exists for this exact track — take it."""

    def test_a_matching_sampled_route_is_inherited_without_a_walk(self) -> None:
        organiser = UserFactory.create()
        route = RouteFactory.create(
            user=organiser,
            points=MERIDIAN_TRACK,
            slope_samples=_route_record(51.0),
        )
        trip = TripFactory.create(
            created_by=organiser,
            route=route,
            points=MERIDIAN_TRACK,
            point_count=2,
            slope_samples=None,
        )

        with patch(
            _BUILDER, side_effect=AssertionError("the origin must not be asked")
        ):
            _run("--commit")

        trip.refresh_from_db()
        assert trip.slope_samples is not None
        assert trip.slope_samples["segments"][0]["angle_deg"] == 51.0

    def test_a_route_whose_geometry_no_longer_matches_is_not_copied(self) -> None:
        """A trip can outlive the track it was taken from.

        Copying then would paint one track's steepness onto another's,
        which is the failure the whole feature exists to prevent.
        """
        organiser = UserFactory.create()
        route = RouteFactory.create(
            user=organiser,
            points=[[8.0, 47.0, 900.0], [8.0, 47.01, 950.0]],
            slope_samples=_route_record(51.0),
        )
        trip = TripFactory.create(
            created_by=organiser,
            route=route,
            points=MERIDIAN_TRACK,
            point_count=2,
            slope_samples=None,
        )

        with patch(_BUILDER, return_value=RECORD):
            _run("--commit")

        trip.refresh_from_db()
        assert trip.slope_samples is not None
        # The walk's answer, not the route's.
        assert trip.slope_samples["segments"][0]["angle_deg"] == 34.2

    def test_an_unsampled_route_leaves_the_trip_to_the_walk(self) -> None:
        organiser = UserFactory.create()
        route = RouteFactory.create(
            user=organiser, points=MERIDIAN_TRACK, slope_samples=None
        )
        trip = TripFactory.create(
            created_by=organiser,
            route=route,
            points=MERIDIAN_TRACK,
            point_count=2,
            slope_samples=None,
        )

        with patch(_BUILDER, return_value=RECORD) as builder:
            _run("--commit")

        builder.assert_called_once()
        trip.refresh_from_db()
        assert trip.slope_samples is not None


@pytest.mark.django_db
class TestTheWalkPath:
    """No record to inherit — ask the origin."""

    def test_a_walk_that_learned_nothing_leaves_the_field_null(self) -> None:
        """Null is "never sampled", and this command's own candidate set
        selects on it, so a later run picks the row up again.
        """
        trip = TripFactory.create(
            points=MERIDIAN_TRACK, point_count=2, slope_samples=None, route=None
        )

        with patch(_BUILDER, return_value=None):
            output = _run("--commit")

        trip.refresh_from_db()
        assert trip.slope_samples is None
        assert "1 left null" in output

    def test_one_failure_does_not_abort_the_others(self) -> None:
        first = TripFactory.create(
            points=MERIDIAN_TRACK, point_count=2, slope_samples=None, route=None
        )
        second = TripFactory.create(
            points=MERIDIAN_TRACK, point_count=2, slope_samples=None, route=None
        )

        def _explode_once(points: Any, label: str) -> dict[str, Any]:
            """Fail for one trip, succeed for the rest."""
            if label == f"trip pk={second.pk}":
                raise RuntimeError("tile origin misbehaved")
            return RECORD

        with patch(_BUILDER, side_effect=_explode_once):
            with pytest.raises(CommandError, match="1 failure"):
                _run("--commit")

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.slope_samples is not None
        assert second.slope_samples is None


@pytest.mark.django_db
class TestReadOnlyByDefault:
    """The house rule: no writes, and here no requests either."""

    def test_a_bare_run_writes_nothing_and_asks_nothing(self) -> None:
        organiser = UserFactory.create()
        route = RouteFactory.create(
            user=organiser,
            points=MERIDIAN_TRACK,
            slope_samples=_route_record(51.0),
        )
        trip = TripFactory.create(
            created_by=organiser,
            route=route,
            points=MERIDIAN_TRACK,
            point_count=2,
            slope_samples=None,
        )

        with patch(
            _BUILDER, side_effect=AssertionError("a read-only run must not walk")
        ):
            output = _run()

        trip.refresh_from_db()
        assert trip.slope_samples is None
        assert "READ-ONLY" in output
        # The split is the figure an operator is previewing for: how many
        # of these need the origin at all.
        assert "1 trip(s) would inherit" in output

    def test_a_record_written_before_cruxes_is_a_candidate_again(self) -> None:
        """SNOW-911 added a key; the key's presence is what marks a row
        current, and an empty list inside it is an answer.
        """
        legacy = {k: v for k, v in RECORD.items() if k != "cruxes"}
        TripFactory.create(
            points=MERIDIAN_TRACK, point_count=2, slope_samples=legacy, route=None
        )

        output = _run()

        assert "1 trip(s)" in output

    def test_a_stale_route_record_is_not_inherited(self) -> None:
        """Inheriting one would never converge.

        The trip would still be missing the key the candidate queryset
        selects on, so it would be re-copied on every run for ever.
        """
        organiser = UserFactory.create()
        route = RouteFactory.create(
            user=organiser,
            points=MERIDIAN_TRACK,
            slope_samples={k: v for k, v in RECORD.items() if k != "cruxes"},
        )
        trip = TripFactory.create(
            created_by=organiser,
            route=route,
            points=MERIDIAN_TRACK,
            point_count=2,
            slope_samples=None,
        )

        with patch(_BUILDER, return_value=RECORD) as builder:
            _run("--commit")

        builder.assert_called_once()
        trip.refresh_from_db()
        assert trip.slope_samples is not None
        assert "cruxes" in trip.slope_samples

    def test_a_sampled_trip_is_not_a_candidate(self) -> None:
        """Idempotent — a second run selects only what the first missed."""
        TripFactory.create(
            points=MERIDIAN_TRACK, point_count=2, slope_samples=RECORD, route=None
        )

        output = _run()

        assert "0 trip(s)" in output
