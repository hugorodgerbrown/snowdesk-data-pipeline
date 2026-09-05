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

  RouteShare (SNOW-764) to_string / __str__, including the deleted-route
    form; Meta.ordering; RouteShareQuerySet.active() against the two ways
    a link dies (expiry, and the owner deleting the route); is_claimable
    agreeing with active() row for row; the FK rules — SET_NULL on the
    route, CASCADE on the sharer.
"""

from __future__ import annotations

import datetime
from datetime import UTC, timedelta

import pytest
from django.utils import timezone

from apps.routes.models import Route, RouteShare
from tests.factories import RouteFactory, RouteShareFactory, UserFactory


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

    def test_duration_hm_rounds_to_whole_minutes_and_pads_them(self) -> None:
        """Whole minutes, padded: "4h05m" must not be misread as "4h5m".

        Rounding rather than truncating, so 59.6 minutes does not read as
        59 — the same rule static/js/map.js's formatDuration follows, and
        the reason the two must match is that the popup and the panel row
        show the same figure for the same route.
        """
        route = RouteFactory.create(
            started_at=datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC),
            finished_at=datetime.datetime(2026, 3, 13, 13, 5, 40, tzinfo=UTC),
        )
        assert route.duration_hm == {"hours": "4", "minutes": "06"}

    def test_duration_hm_breaks_a_half_minute_tie_upwards(self) -> None:
        """An exact half-minute rounds UP, as JavaScript's Math.round does.

        This is the one input class where the builtin ``round`` would not
        agree with static/js/map.js's formatDuration: it is banker's
        rounding, so it breaks a .5 tie to the EVEN number and
        ``round(270.5)`` is 270 where ``Math.round(270.5)`` is 271. The
        popup and the panel row would then disagree by a minute about the
        same route.

        4h30m30s is 270.5 minutes and is not a contrived span — a GPX
        carries whole-second stamps, so an exact half-minute remainder
        turns up on ordinary recordings.
        """
        route = RouteFactory.create(
            started_at=datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC),
            finished_at=datetime.datetime(2026, 3, 13, 13, 30, 30, tzinfo=UTC),
        )
        assert route.duration_hm == {"hours": "4", "minutes": "31"}

    def test_duration_hm_states_no_hours_figure_under_an_hour(self) -> None:
        """Under an hour there is no hours figure: "0h41m" states one.

        And the minutes go unpadded there: an hour count is not a leading
        zero on a minute count, so there is nothing to align them to.
        """
        route = RouteFactory.create(
            started_at=datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC),
            finished_at=datetime.datetime(2026, 3, 13, 9, 41, tzinfo=UTC),
        )
        assert route.duration_hm == {"hours": "", "minutes": "41"}

    def test_duration_hm_is_none_when_the_route_is_untimed(self) -> None:
        """The caller omits the figure; it never renders a zero."""
        route = RouteFactory.create(started_at=None, finished_at=None)
        assert route.duration_hm is None

    def test_duration_hm_is_none_for_a_non_positive_span(self) -> None:
        """Two identical stamps are a recording artefact, not a tour.

        Matches formatDuration's own ``seconds <= 0`` guard, so a row the
        popup shows nothing for does not grow a "0m" in the panel.
        """
        route = RouteFactory.create(
            started_at=datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC),
            finished_at=datetime.datetime(2026, 3, 13, 9, 0, tzinfo=UTC),
        )
        assert route.duration_hm is None

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


# ---------------------------------------------------------------------------
# RouteShare (SNOW-764)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRouteShareToString:
    """to_string and __str__ coverage, including the routeless form."""

    def test_to_string_names_the_token_and_the_route(self) -> None:
        """A live share identifies itself by token and by what it hands out."""
        share = RouteShareFactory.create(
            token="abc123", route=RouteFactory.create(name="Col de Balme")
        )
        assert "abc123" in share.to_string()
        assert "Col de Balme" in share.to_string()

    def test_to_string_says_deleted_when_the_route_is_gone(self) -> None:
        """The row outlives its route, so to_string must not dereference a null."""
        route = RouteFactory.create()
        share = RouteShareFactory.create(token="gone123", route=route)
        route.delete()
        share.refresh_from_db()

        assert share.to_string() == "RouteShare(gone123, deleted)"

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ is to_string, as on every model here."""
        share = RouteShareFactory.create()
        assert str(share) == share.to_string()


@pytest.mark.django_db
class TestRouteShareOrdering:
    """Meta.ordering is newest-first."""

    def test_shares_are_ordered_newest_first(self) -> None:
        """The default queryset order is -created_at."""
        first = RouteShareFactory.create()
        second = RouteShareFactory.create()

        assert list(RouteShare.objects.all()) == [second, first]


@pytest.mark.django_db
class TestRouteShareActive:
    """RouteShareQuerySet.active() — the one definition of "claimable"."""

    def test_a_live_share_is_active(self) -> None:
        """A route that exists and a window still open."""
        share = RouteShareFactory.create()
        assert list(RouteShare.objects.active()) == [share]

    def test_an_expired_share_is_not_active(self) -> None:
        """Past expires_at, the link stops working."""
        RouteShareFactory.create(
            expires_at=timezone.now() - datetime.timedelta(seconds=1)
        )
        assert not RouteShare.objects.active().exists()

    def test_a_share_expiring_exactly_now_is_not_active(self) -> None:
        """The bound is strict — ``expires_at`` is the first moment it is dead."""
        RouteShareFactory.create(expires_at=timezone.now())
        assert not RouteShare.objects.active().exists()

    def test_a_share_whose_route_was_deleted_is_not_active(self) -> None:
        """Deleting the route revokes the link immediately, not at expiry."""
        route = RouteFactory.create()
        share = RouteShareFactory.create(route=route)
        route.delete()

        assert not RouteShare.objects.active().exists()
        # The row itself survives — SET_NULL, not CASCADE.
        assert RouteShare.objects.filter(pk=share.pk).exists()

    def test_is_claimable_agrees_with_active_row_for_row(self) -> None:
        """The Python predicate and the SQL one answer the same question.

        They are written twice because Django cannot share a predicate
        between the two; this is what keeps them from drifting apart.
        """
        live = RouteShareFactory.create()
        expired = RouteShareFactory.create(
            expires_at=timezone.now() - datetime.timedelta(days=1)
        )
        revoked_route = RouteFactory.create()
        revoked = RouteShareFactory.create(route=revoked_route)
        revoked_route.delete()
        revoked.refresh_from_db()

        active_ids = set(RouteShare.objects.active().values_list("pk", flat=True))
        for share in (live, expired, revoked):
            assert share.is_claimable == (share.pk in active_ids)


@pytest.mark.django_db
class TestRouteShareForeignKeys:
    """SET_NULL on the route, CASCADE on the sharer."""

    def test_deleting_the_route_nulls_the_fk_and_keeps_the_row(self) -> None:
        """The audit of who a route was shared with outlives the route."""
        route = RouteFactory.create()
        share = RouteShareFactory.create(route=route)

        route.delete()
        share.refresh_from_db()

        assert share.route is None

    def test_deleting_the_sharer_deletes_their_shares(self) -> None:
        """A deleted account's grants have nobody to account to."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)
        share = RouteShareFactory.create(route=route, created_by=user)

        user.delete()

        assert not RouteShare.objects.filter(pk=share.pk).exists()


@pytest.mark.django_db
class TestRouteShareDefaults:
    """The claim counters start at "never claimed"."""

    def test_a_new_share_has_no_claims(self) -> None:
        """claim_count starts at zero and last_claimed_at at null."""
        share = RouteShareFactory.create()
        assert share.claim_count == 0
        assert share.last_claimed_at is None
