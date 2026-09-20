"""
tests/trips/test_migrations.py — Tests for apps.trips data migrations.

trips.0004_trip_duration.fill_duration_from_source_route:
  a trip whose route is still there and still matches gets the route's
    elapsed time;
  a trip whose route was deleted keeps its null;
  a trip whose route is untimed keeps its null;
  a trip whose snapshot no longer matches the route's geometry keeps its
    null, because a route that has moved on is a different track and its
    recording time does not describe this trip's day;
  a non-positive span is skipped rather than written as a zero;
  a re-run is idempotent.

The migration's own function is called directly with a stand-in ``apps``
registry rather than run through Django's migration executor, matching
tests/routes/test_migrations.py: the behaviour under test is the guard
logic, and the executor would add a schema rebuild per test for nothing.
``schema_editor`` is unused by the function and passed as None.
"""

from __future__ import annotations

import datetime
from importlib import import_module
from typing import Any

import pytest

from apps.trips.models import Trip
from tests.factories import RouteFactory, TripFactory

# ``import_module`` rather than a plain import: the module name starts with
# a digit, so it is not a legal Python identifier.
_MIGRATION = import_module("apps.trips.migrations.0004_trip_duration")

_STARTED = datetime.datetime(2026, 3, 13, 9, 0, tzinfo=datetime.UTC)
_FINISHED = datetime.datetime(2026, 3, 13, 15, 0, tzinfo=datetime.UTC)


class _StubApps:
    """Minimal stand-in for the historical app registry.

    ``fill_duration_from_source_route`` only ever asks for the ``Trip``
    model, and the concrete one is a faithful stand-in: the migration
    reads ``route``, ``points`` and ``duration``, none of which has moved
    since this migration was written.
    """

    def get_model(self, app_label: str, model_name: str) -> Any:
        """Return the concrete model for the requested label.

        Args:
            app_label: The app label being asked for.
            model_name: The model name being asked for.

        Returns:
            The ``Trip`` model.

        """
        assert (app_label, model_name) == ("trips", "Trip")
        return Trip


def _run_backfill() -> None:
    """Invoke the migration's backfill against the test database."""
    _MIGRATION.fill_duration_from_source_route(_StubApps(), None)


@pytest.mark.django_db
class TestFillDurationFromSourceRoute:
    """The one-off backfill of Trip.duration onto pre-existing rows."""

    def test_fills_from_a_route_that_still_matches(self) -> None:
        """The ordinary case: route present, timed, geometry unchanged."""
        route = RouteFactory.create(started_at=_STARTED, finished_at=_FINISHED)
        trip = TripFactory.create(route=route, points=route.points, duration=None)

        _run_backfill()

        trip.refresh_from_db()
        assert trip.duration == datetime.timedelta(hours=6)

    def test_a_deleted_route_leaves_the_null(self) -> None:
        """``route`` is SET_NULL, so there is nothing left to read."""
        trip = TripFactory.create(route=None, duration=None)

        _run_backfill()

        trip.refresh_from_db()
        assert trip.duration is None

    def test_an_untimed_route_leaves_the_null(self) -> None:
        """No per-point times in the source file, so no figure to copy."""
        route = RouteFactory.create(started_at=None, finished_at=None)
        trip = TripFactory.create(route=route, points=route.points, duration=None)

        _run_backfill()

        trip.refresh_from_db()
        assert trip.duration is None

    def test_a_diverged_snapshot_leaves_the_null(self) -> None:
        """A route whose geometry has moved on is a different track.

        The same pairing guard ``_inherited_record`` uses for the slope
        record. Filling from it would put one track's recording time
        against another track's day.
        """
        route = RouteFactory.create(started_at=_STARTED, finished_at=_FINISHED)
        trip = TripFactory.create(
            route=route,
            points=[[7.9, 46.9, 2000.0], [7.91, 46.91, 2100.0]],
            duration=None,
        )

        _run_backfill()

        trip.refresh_from_db()
        assert trip.duration is None

    def test_a_non_positive_span_is_skipped(self) -> None:
        """Two identical stamps are a recording artefact, not a day."""
        route = RouteFactory.create(started_at=_STARTED, finished_at=_STARTED)
        trip = TripFactory.create(route=route, points=route.points, duration=None)

        _run_backfill()

        trip.refresh_from_db()
        assert trip.duration is None

    def test_a_second_run_changes_nothing(self) -> None:
        """Idempotent: a re-run writes the same figure it already wrote."""
        route = RouteFactory.create(started_at=_STARTED, finished_at=_FINISHED)
        trip = TripFactory.create(route=route, points=route.points, duration=None)

        _run_backfill()
        _run_backfill()

        trip.refresh_from_db()
        assert trip.duration == datetime.timedelta(hours=6)
