"""
tests/trips/test_services_routes.py — Tests for saving a trip's route
(SNOW-824).

save_trip_route:
  writes a route owned by the CALLER, from the trip's snapshot;
  works after the organiser has deleted the source route, which is the
    whole reason it reads the snapshot rather than the FK;
  copies null ascent/descent as null, never as zero;
  carries NO timing and NO source filename — a trip is a plan, never a
    recording;
  seeds the name from the trip, falling back to the snapshot's route_name;
  refuses at settings.ROUTES_MAX_PER_USER, through the routes app's own
    cap rather than a second copy of the arithmetic.

already_saved:
  False for a stranger's route and for an anonymous viewer;
  True once the geometry is on the caller's account, which is what makes a
    second save a no-op.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from apps.routes.models import Route
from apps.routes.services.routes import RouteLimitReached
from apps.trips.services.routes import already_saved, save_trip_route
from tests.factories import RouteFactory, TripFactory, UserFactory


@pytest.mark.django_db
class TestSaveTripRoute:
    """The copy, and what it deliberately does not carry."""

    def test_writes_a_route_owned_by_the_caller(self) -> None:
        """Not the organiser's: the whole point is a copy on my account."""
        trip = TripFactory.create()
        viewer = UserFactory.create()

        route = save_trip_route(viewer, trip)

        assert route.user_id == viewer.pk
        assert Route.objects.for_user(viewer).count() == 1

    def test_copies_the_geometry_from_the_snapshot(self) -> None:
        """Every field the page drew, and the same figures beside it."""
        trip = TripFactory.create()
        viewer = UserFactory.create()

        route = save_trip_route(viewer, trip)

        assert route.points == trip.points
        assert route.bounds == trip.bounds
        assert route.distance_m == trip.distance_m
        assert route.point_count == trip.point_count

    def test_survives_the_organiser_deleting_their_source_route(self) -> None:
        """The reason it reads the snapshot rather than Trip.route.

        The FK is SET_NULL, so a save that read through it would fail
        exactly when a copy is most useful.
        """
        source = RouteFactory.create()
        trip = TripFactory.create(route=source)
        source.delete()
        trip.refresh_from_db()
        assert trip.route_id is None

        route = save_trip_route(UserFactory.create(), trip)

        assert route.points == trip.points

    def test_null_elevation_is_copied_as_null(self) -> None:
        """Unknown and flat are different facts, and only one is zero."""
        trip = TripFactory.create(ascent_m=None, descent_m=None)

        route = save_trip_route(UserFactory.create(), trip)

        assert route.ascent_m is None
        assert route.descent_m is None

    def test_the_copy_carries_no_timing_and_no_filename(self) -> None:
        """A trip is a PLAN and was never a recording."""
        trip = TripFactory.create()

        route = save_trip_route(UserFactory.create(), trip)

        assert route.started_at is None
        assert route.finished_at is None
        assert route.source_filename == ""

    def test_the_name_seeds_from_the_trip(self) -> None:
        """The organiser's own words, not ours."""
        trip = TripFactory.create(name="Rosablanche", route_name="track.gpx")

        route = save_trip_route(UserFactory.create(), trip)

        assert route.name == "Rosablanche"

    def test_the_name_falls_back_to_the_snapshots_route_name(self) -> None:
        """Which is why the snapshot carries route_name at all."""
        trip = TripFactory.create(name="", route_name="Mont Fort traverse")

        route = save_trip_route(UserFactory.create(), trip)

        assert route.name == "Mont Fort traverse"

    @override_settings(ROUTES_MAX_PER_USER=1)
    def test_refuses_at_the_routes_cap(self) -> None:
        """The routes app's own cap, reused rather than re-derived."""
        viewer = UserFactory.create()
        RouteFactory.create(user=viewer)
        trip = TripFactory.create()

        with pytest.raises(RouteLimitReached):
            save_trip_route(viewer, trip)

        assert Route.objects.for_user(viewer).count() == 1

    def test_the_organisers_own_route_is_untouched(self) -> None:
        """A copy, never a transfer."""
        source = RouteFactory.create(name="Rosablanche")
        trip = TripFactory.create(route=source, created_by=source.user)

        save_trip_route(UserFactory.create(), trip)

        source.refresh_from_db()
        assert source.user_id == trip.created_by_id
        assert source.name == "Rosablanche"


@pytest.mark.django_db
class TestAlreadySaved:
    """The geometry match that makes a second save a no-op."""

    def test_false_before_the_save(self) -> None:
        """A viewer with no routes has not saved this one."""
        trip = TripFactory.create()
        assert already_saved(UserFactory.create(), trip) is False

    def test_true_after_the_save(self) -> None:
        """Exact geometry, since nothing links the copy back to the trip."""
        trip = TripFactory.create()
        viewer = UserFactory.create()
        save_trip_route(viewer, trip)

        assert already_saved(viewer, trip) is True

    def test_false_for_an_anonymous_viewer_without_a_query(self) -> None:
        """They can hold no routes, so the answer needs no database."""
        trip = TripFactory.create()
        assert already_saved(AnonymousUser(), trip) is False

    def test_another_users_copy_does_not_count(self) -> None:
        """Owner-scoped: my friend saving it is not me saving it."""
        trip = TripFactory.create()
        save_trip_route(UserFactory.create(), trip)

        assert already_saved(UserFactory.create(), trip) is False

    def test_a_different_track_does_not_match(self) -> None:
        """A route with the same point count but different points."""
        trip = TripFactory.create()
        viewer = UserFactory.create()
        RouteFactory.create(
            user=viewer,
            point_count=trip.point_count,
            distance_m=trip.distance_m,
            points=[[1.0, 1.0, 0.0], [1.1, 1.1, 0.0], [1.2, 1.2, 0.0]],
        )

        assert already_saved(viewer, trip) is False
