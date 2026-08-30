"""
tests/routes/test_shares.py — Tests for apps.routes.services.shares (SNOW-764).

create_route_share:
  mints a token and an expiry from settings.ROUTE_SHARE_MAX_AGE_DAYS;
  is owner-scoped — another user's uuid raises Route.DoesNotExist;
  two shares of one route are two rows, not one reused;
  a token collision is retried, and exhausting the retries raises.

claim_route_share:
  copies every geometry and derived field onto a NEW route owned by the
    claimer, leaving the sharer's row untouched;
  bumps claim_count and last_claimed_at;
  works twice — the link is reusable;
  raises RouteLimitReached at settings.ROUTES_MAX_PER_USER, without
    writing a row;
  refuses an expired share, a share whose route was deleted, and an
    unknown token — all as the same DoesNotExist.

The session helpers:
  add/pending/drop round-trip in the order links were followed;
  adding is idempotent and does not reorder;
  the list is capped at settings.ROUTE_SHARE_MAX_PENDING, dropping the
    OLDEST;
  a stale session value of the wrong shape reads as empty;
  pending_shares resolves in session order, prunes dead tokens from the
    session as it reads, and costs one query.

The tests use a real ``SessionStore`` rather than a dict: the helpers set
``session.modified``, which a dict has no notion of, and the pruning
behaviour is specifically about what survives into the stored session.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.routes.models import Route, RouteShare
from apps.routes.services.routes import RouteLimitReached
from apps.routes.services.shares import (
    PENDING_SESSION_KEY,
    RouteShareTokenCollision,
    add_pending_token,
    claim_route_share,
    create_route_share,
    drop_pending_token,
    pending_shares,
    pending_tokens,
)
from tests.factories import RouteFactory, RouteShareFactory, UserFactory


def _session() -> SessionStore:
    """Return an empty session store to hand the pending helpers."""
    return SessionStore()


@pytest.mark.django_db
class TestCreateRouteShare:
    """Minting a share link for one's own route."""

    def test_a_share_is_created_for_the_owners_route(self) -> None:
        """The row points at the route and records who shared it."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)

        share = create_route_share(user, route.uuid)

        assert share.route == route
        assert share.created_by == user
        assert share.token

    def test_the_window_comes_from_the_setting(self, settings: Any) -> None:
        """expires_at is ROUTE_SHARE_MAX_AGE_DAYS ahead of now."""
        settings.ROUTE_SHARE_MAX_AGE_DAYS = 7
        user = UserFactory.create()
        route = RouteFactory.create(user=user)

        before = timezone.now()
        share = create_route_share(user, route.uuid)

        assert share.expires_at >= before + timedelta(days=7)
        assert share.expires_at <= timezone.now() + timedelta(days=7)

    def test_a_new_share_is_claimable(self) -> None:
        """What create_route_share mints is what active() returns."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)

        share = create_route_share(user, route.uuid)

        assert share.is_claimable
        assert list(RouteShare.objects.active()) == [share]

    def test_another_users_route_is_not_shareable(self) -> None:
        """Owner-scoped by the lookup — the view turns this into a 404."""
        owner = UserFactory.create()
        stranger = UserFactory.create()
        route = RouteFactory.create(user=owner)

        with pytest.raises(Route.DoesNotExist):
            create_route_share(stranger, route.uuid)

    def test_sharing_twice_mints_two_rows(self) -> None:
        """Separate rows, so a future per-link revoke is a delete of one."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)

        first = create_route_share(user, route.uuid)
        second = create_route_share(user, route.uuid)

        assert first.token != second.token
        assert route.shares.count() == 2

    def test_a_token_collision_is_retried(self) -> None:
        """A collided token is replaced rather than failing the request."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)
        real_create = RouteShare.objects.create
        calls: list[int] = []

        def flaky(*args: Any, **kwargs: Any) -> RouteShare:
            calls.append(1)
            if len(calls) == 1:
                raise IntegrityError(
                    "UNIQUE constraint failed: routes_routeshare.token"
                )
            return real_create(*args, **kwargs)

        with patch.object(RouteShare.objects, "create", side_effect=flaky):
            share = create_route_share(user, route.uuid)

        assert len(calls) == 2
        assert share.token

    def test_exhausting_the_retries_raises(self) -> None:
        """A permanently colliding generator is a failure, not a silent None."""
        user = UserFactory.create()
        route = RouteFactory.create(user=user)

        with patch.object(
            RouteShare.objects, "create", side_effect=IntegrityError("collision")
        ):
            with pytest.raises(RouteShareTokenCollision):
                create_route_share(user, route.uuid)


@pytest.mark.django_db
class TestClaimRouteShare:
    """Taking a copy of a shared route."""

    def test_the_claimer_gets_their_own_row(self) -> None:
        """A new Route, owned by the claimer, not the sharer's row re-pointed."""
        owner = UserFactory.create()
        claimer = UserFactory.create()
        route = RouteFactory.create(user=owner)
        share = RouteShareFactory.create(route=route, created_by=owner)

        copy = claim_route_share(claimer, share.token)

        assert copy.pk != route.pk
        assert copy.user == claimer

    def test_every_geometry_and_derived_field_is_copied(self) -> None:
        """The copy is the same route, described identically."""
        owner = UserFactory.create()
        claimer = UserFactory.create()
        route = RouteFactory.create(
            user=owner,
            name="Col de Balme",
            source_filename="col-de-balme.gpx",
            points=[[7.0, 46.0, 1500.0], [7.1, 46.1, 1900.0]],
            distance_m=8200.0,
            ascent_m=400.0,
            descent_m=120.0,
            point_count=2,
            bounds=[7.0, 46.0, 7.1, 46.1],
        )
        share = RouteShareFactory.create(route=route, created_by=owner)

        copy = claim_route_share(claimer, share.token)

        assert copy.name == route.name
        assert copy.source_filename == route.source_filename
        assert copy.points == route.points
        assert copy.distance_m == route.distance_m
        assert copy.ascent_m == route.ascent_m
        assert copy.descent_m == route.descent_m
        assert copy.started_at == route.started_at
        assert copy.finished_at == route.finished_at
        assert copy.point_count == route.point_count
        assert copy.bounds == route.bounds

    def test_a_null_ascent_is_copied_as_null(self) -> None:
        """Unknown must survive the copy — it is not zero on either row."""
        owner = UserFactory.create()
        claimer = UserFactory.create()
        route = RouteFactory.create(
            user=owner,
            ascent_m=None,
            descent_m=None,
            points=[[7.0, 46.0, None], [7.1, 46.1, None]],
        )
        share = RouteShareFactory.create(route=route, created_by=owner)

        copy = claim_route_share(claimer, share.token)

        assert copy.ascent_m is None
        assert copy.descent_m is None

    def test_the_sharer_keeps_their_route(self) -> None:
        """A claim copies, it never transfers."""
        owner = UserFactory.create()
        claimer = UserFactory.create()
        route = RouteFactory.create(user=owner)
        share = RouteShareFactory.create(route=route, created_by=owner)

        claim_route_share(claimer, share.token)

        route.refresh_from_db()
        assert route.user == owner
        assert Route.objects.for_user(owner).count() == 1
        assert Route.objects.for_user(claimer).count() == 1

    def test_the_counters_are_bumped(self) -> None:
        """claim_count and last_claimed_at record the use."""
        owner = UserFactory.create()
        claimer = UserFactory.create()
        share = RouteShareFactory.create(route=RouteFactory.create(user=owner))

        claim_route_share(claimer, share.token)

        share.refresh_from_db()
        assert share.claim_count == 1
        assert share.last_claimed_at is not None

    def test_the_link_is_reusable(self) -> None:
        """Two people claiming one link each get a copy."""
        owner = UserFactory.create()
        first = UserFactory.create()
        second = UserFactory.create()
        share = RouteShareFactory.create(route=RouteFactory.create(user=owner))

        claim_route_share(first, share.token)
        claim_route_share(second, share.token)

        share.refresh_from_db()
        assert share.claim_count == 2
        assert Route.objects.for_user(first).count() == 1
        assert Route.objects.for_user(second).count() == 1

    def test_an_expired_share_cannot_be_claimed(self) -> None:
        """The window is what revokes the link."""
        claimer = UserFactory.create()
        share = RouteShareFactory.create(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

        with pytest.raises(RouteShare.DoesNotExist):
            claim_route_share(claimer, share.token)

    def test_a_deleted_route_cannot_be_claimed(self) -> None:
        """Deleting the route revokes the link the moment it happens."""
        claimer = UserFactory.create()
        route = RouteFactory.create()
        share = RouteShareFactory.create(route=route)
        route.delete()

        with pytest.raises(RouteShare.DoesNotExist):
            claim_route_share(claimer, share.token)

    def test_an_unknown_token_raises_the_same_exception(self) -> None:
        """One exception for all three, so nothing tells a guesser what exists."""
        claimer = UserFactory.create()

        with pytest.raises(RouteShare.DoesNotExist):
            claim_route_share(claimer, "not-a-real-token")

    def test_a_claimer_at_the_cap_is_refused(self, settings: Any) -> None:
        """The claim competes for the same per-user cap an upload does."""
        settings.ROUTES_MAX_PER_USER = 2
        claimer = UserFactory.create()
        RouteFactory.create_batch(2, user=claimer)
        share = RouteShareFactory.create()

        with pytest.raises(RouteLimitReached):
            claim_route_share(claimer, share.token)

    def test_a_refused_claim_writes_nothing(self, settings: Any) -> None:
        """No copy, and no counter bump for a claim that did not happen."""
        settings.ROUTES_MAX_PER_USER = 1
        claimer = UserFactory.create()
        RouteFactory.create(user=claimer)
        share = RouteShareFactory.create()

        with pytest.raises(RouteLimitReached):
            claim_route_share(claimer, share.token)

        share.refresh_from_db()
        assert share.claim_count == 0
        assert Route.objects.for_user(claimer).count() == 1


@pytest.mark.django_db
class TestPendingTokenHelpers:
    """The session-held list of followed-but-unclaimed tokens."""

    def test_an_empty_session_has_no_pending_tokens(self) -> None:
        """A visitor who never followed a share link holds nothing."""
        assert pending_tokens(_session()) == []

    def test_a_token_round_trips(self) -> None:
        """What is added is what is read back."""
        session = _session()
        add_pending_token(session, "abc")
        assert pending_tokens(session) == ["abc"]

    def test_tokens_keep_the_order_they_were_followed_in(self) -> None:
        """The panel lists pending rows in this order."""
        session = _session()
        add_pending_token(session, "one")
        add_pending_token(session, "two")

        assert pending_tokens(session) == ["one", "two"]

    def test_adding_the_same_token_twice_does_not_reorder(self) -> None:
        """A refresh must not move a pending row down the list."""
        session = _session()
        add_pending_token(session, "one")
        add_pending_token(session, "two")
        add_pending_token(session, "one")

        assert pending_tokens(session) == ["one", "two"]

    def test_the_list_is_capped_dropping_the_oldest(self, settings: Any) -> None:
        """The link just followed is the one the visitor means."""
        settings.ROUTE_SHARE_MAX_PENDING = 2
        session = _session()
        add_pending_token(session, "one")
        add_pending_token(session, "two")
        add_pending_token(session, "three")

        assert pending_tokens(session) == ["two", "three"]

    def test_adding_marks_the_session_modified(self) -> None:
        """Otherwise the write never reaches the cookie."""
        session = _session()
        session.modified = False
        add_pending_token(session, "abc")

        assert session.modified is True

    def test_dropping_removes_only_that_token(self) -> None:
        """A claim clears its own pending row and leaves the others."""
        session = _session()
        add_pending_token(session, "one")
        add_pending_token(session, "two")

        drop_pending_token(session, "one")

        assert pending_tokens(session) == ["two"]

    def test_dropping_an_absent_token_is_silent(self) -> None:
        """A double-submitted claim reaches here twice."""
        session = _session()
        add_pending_token(session, "one")

        drop_pending_token(session, "nope")

        assert pending_tokens(session) == ["one"]

    def test_a_stale_session_value_of_the_wrong_shape_reads_as_empty(self) -> None:
        """A cookie outlives a deploy; a bad shape must not raise mid-render."""
        session = _session()
        session[PENDING_SESSION_KEY] = {"not": "a list"}

        assert pending_tokens(session) == []

    def test_non_string_entries_are_ignored(self) -> None:
        """Only tokens are tokens."""
        session = _session()
        session[PENDING_SESSION_KEY] = ["ok", 42, None]

        assert pending_tokens(session) == ["ok"]


@pytest.mark.django_db
class TestPendingShares:
    """Resolving pending tokens to live shares."""

    def test_no_tokens_resolves_to_nothing(self) -> None:
        """The common case — a visitor who followed no link."""
        assert pending_shares(_session()) == []

    def test_an_empty_session_costs_no_query(self) -> None:
        """The widened surfaces short-circuit before touching the database."""
        session = _session()
        with CaptureQueriesContext(connection) as ctx:
            pending_shares(session)
        assert len(ctx.captured_queries) == 0

    def test_a_pending_share_resolves(self) -> None:
        """One followed link, one share."""
        share = RouteShareFactory.create()
        session = _session()
        add_pending_token(session, share.token)

        assert pending_shares(session) == [share]

    def test_shares_come_back_in_session_order(self) -> None:
        """Session order, not the model's newest-first default."""
        first = RouteShareFactory.create()
        second = RouteShareFactory.create()
        session = _session()
        add_pending_token(session, first.token)
        add_pending_token(session, second.token)

        assert pending_shares(session) == [first, second]

    def test_an_expired_token_is_dropped_from_the_session(self) -> None:
        """A dead link stops costing a query on every later page load."""
        live = RouteShareFactory.create()
        dead = RouteShareFactory.create(expires_at=timezone.now() - timedelta(days=1))
        session = _session()
        add_pending_token(session, live.token)
        add_pending_token(session, dead.token)

        assert pending_shares(session) == [live]
        assert pending_tokens(session) == [live.token]

    def test_a_deleted_routes_token_is_dropped(self) -> None:
        """The other way a link dies, treated the same."""
        route = RouteFactory.create()
        share = RouteShareFactory.create(route=route)
        session = _session()
        add_pending_token(session, share.token)
        route.delete()

        assert pending_shares(session) == []
        assert pending_tokens(session) == []

    def test_an_unknown_token_is_dropped(self) -> None:
        """A token nothing matches never will."""
        session = _session()
        add_pending_token(session, "never-existed")

        assert pending_shares(session) == []
        assert pending_tokens(session) == []

    def test_resolution_costs_one_query_however_many_tokens(self) -> None:
        """One IN query, and the route joined rather than fetched per row."""
        shares = [RouteShareFactory.create() for _ in range(3)]
        session = _session()
        for share in shares:
            add_pending_token(session, share.token)

        with CaptureQueriesContext(connection) as ctx:
            resolved = pending_shares(session)
            # Touch the route on each, which is what every caller does —
            # a missing select_related would show up here, not above.
            for share in resolved:
                assert share.route is not None

        assert len(ctx.captured_queries) == 1
