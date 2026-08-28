"""
tests/routes/test_models.py — Tests for apps.routes.models.

Covers:
  Route to_string / __str__ format (named, unnamed-with-filename, and
    neither).
  Route.distance_km — the metres-to-kilometres display helper.
  RouteQuerySet.for_user — isolates routes by owner.
  Meta.ordering — newest-first (inherited from BaseModel).
  ascent_m is nullable — a route from a file with no <ele> stores null,
    not zero.
  user FK is CASCADE — deleting the owner removes their routes.
"""

from __future__ import annotations

import datetime
from datetime import UTC, timedelta

import pytest
from django.utils import timezone

from apps.routes.models import Route
from tests.factories import RouteFactory, UserFactory


class TestRouteToString:
    """to_string, __str__ and distance_km coverage."""

    def test_to_string_uses_name_when_set(self) -> None:
        """to_string includes the route name when it has one."""
        route = RouteFactory.build(name="Col de Balme", distance_m=8200.0)
        assert "Col de Balme" in route.to_string()

    def test_to_string_falls_back_to_source_filename(self) -> None:
        """A nameless route is identified by the file it came from."""
        route = RouteFactory.build(name="", source_filename="morning-lap.gpx")
        assert "morning-lap.gpx" in route.to_string()

    def test_to_string_falls_back_again_when_nothing_names_it(self) -> None:
        """With neither a name nor a filename, to_string still reads sensibly."""
        route = RouteFactory.build(name="", source_filename="")
        assert "Untitled route" in route.to_string()

    def test_to_string_includes_distance_in_kilometres(self) -> None:
        """Distance is rendered in km, not the stored metres."""
        route = RouteFactory.build(name="Traverse", distance_m=8200.0)
        assert "8.2 km" in route.to_string()

    def test_distance_km_converts_from_metres(self) -> None:
        """distance_km is distance_m / 1000."""
        route = RouteFactory.build(distance_m=8200.0)
        assert route.distance_km == pytest.approx(8.2)

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string()."""
        route = RouteFactory.build()
        assert str(route) == route.to_string()


@pytest.mark.django_db
class TestRouteQuerySetForUser:
    """RouteQuerySet.for_user — isolates rows by owner."""

    def test_for_user_returns_only_that_users_routes(self) -> None:
        """for_user excludes rows belonging to a different user."""
        user_a = UserFactory.create()
        user_b = UserFactory.create()
        mine = RouteFactory.create(user=user_a)
        RouteFactory.create(user=user_b)

        assert list(Route.objects.for_user(user_a)) == [mine]

    def test_for_user_returns_empty_for_user_with_no_routes(self) -> None:
        """for_user returns an empty queryset when the user has no rows."""
        user = UserFactory.create()
        assert list(Route.objects.for_user(user)) == []


@pytest.mark.django_db
class TestRouteOrdering:
    """Meta.ordering — newest-first (inherited from BaseModel)."""

    def test_ordering_is_newest_first(self) -> None:
        """Queryset is ordered -created_at (newest first)."""
        user = UserFactory.create()
        early = RouteFactory.create(user=user)
        early.created_at = timezone.now() - datetime.timedelta(hours=1)
        early.save(update_fields=["created_at"])
        late = RouteFactory.create(user=user)

        assert list(Route.objects.for_user(user)) == [late, early]


@pytest.mark.django_db
class TestRouteAscentNullability:
    """ascent_m distinguishes "no elevation data" from "flat"."""

    def test_ascent_can_be_null(self) -> None:
        """A route imported from a file with no <ele> stores null ascent."""
        route = RouteFactory.create(ascent_m=None)
        route.refresh_from_db()
        assert route.ascent_m is None

    def test_null_ascent_is_distinct_from_zero(self) -> None:
        """Null and 0.0 are stored as different values, not collapsed."""
        unknown = RouteFactory.create(ascent_m=None)
        flat = RouteFactory.create(ascent_m=0.0)

        unknown.refresh_from_db()
        flat.refresh_from_db()

        assert unknown.ascent_m is None
        assert flat.ascent_m == 0.0


@pytest.mark.django_db
class TestRouteDuration:
    """duration is the elapsed span, and null when the file was untimed."""

    def test_duration_is_the_span_between_the_two_ends(self) -> None:
        """Finish minus start — the figure the popup renders."""
        route = RouteFactory.create(
            started_at=datetime.datetime(2026, 3, 13, 9, 41, 38, tzinfo=UTC),
            finished_at=datetime.datetime(2026, 3, 13, 14, 41, 35, tzinfo=UTC),
        )
        assert route.duration == timedelta(hours=4, minutes=59, seconds=57)

    def test_duration_is_none_when_the_route_is_untimed(self) -> None:
        """A planned <rte> has no duration; it does not take no time."""
        route = RouteFactory.create(started_at=None, finished_at=None)
        assert route.duration is None

    def test_duration_is_none_when_only_one_end_is_known(self) -> None:
        """Half a fact is no duration.

        The parser will not produce this pairing (``_track_span`` returns
        both or neither), but the columns are independently nullable and a
        row can be written by hand, so the property does not assume it.
        """
        started_only = RouteFactory.create(
            started_at=datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC),
            finished_at=None,
        )
        finished_only = RouteFactory.create(
            started_at=None,
            finished_at=datetime.datetime(2026, 3, 13, 11, 0, tzinfo=UTC),
        )

        assert started_only.duration is None
        assert finished_only.duration is None

    def test_the_span_includes_the_stops(self) -> None:
        """Elapsed, not moving: a pause counts (SNOW-751 is moving time)."""
        route = RouteFactory.create(
            started_at=datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC),
            finished_at=datetime.datetime(2026, 3, 14, 16, 0, tzinfo=UTC),
        )
        assert route.duration == timedelta(days=1, hours=7)

    def test_both_ends_survive_a_round_trip(self) -> None:
        """Stored tz-aware, read back tz-aware."""
        route = RouteFactory.create(
            started_at=datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC),
            finished_at=datetime.datetime(2026, 3, 13, 11, 0, tzinfo=UTC),
        )
        route.refresh_from_db()
        assert route.started_at == datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC)
        assert route.finished_at is not None
        assert route.finished_at.tzinfo is not None


@pytest.mark.django_db
class TestRouteUserCascade:
    """The user FK is CASCADE — a deleted account takes its routes with it."""

    def test_deleting_user_deletes_their_routes(self) -> None:
        """Deleting the owner removes the route row."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)

        user.delete()

        assert not Route.objects.filter(pk=route.pk).exists()
