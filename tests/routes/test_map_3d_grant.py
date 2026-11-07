"""
tests/routes/test_map_3d_grant.py — a route share's 3D grant (SNOW-1469).

``apps/routes/services/map_3d_grant.py``: the 3D follow seats a share's
token in the session, and the grant holds only while the share is
claimable (live, not the example) and its sharer is inside ``map_3d``. The
sharer's flag is read through waffle's own membership rules, so these tests
put the sharer in the flag by user, group and superuser rather than only
through ``override_flag``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth.models import Group
from django.contrib.sessions.backends.db import SessionStore
from django.utils import timezone
from waffle import get_waffle_flag_model
from waffle.testutils import override_flag

from apps.routes.services.map_3d_grant import (
    GRANT_SESSION_KEY,
    map_3d_granted,
    seat_map_3d_grant,
)
from tests.factories import RouteFactory, RouteShareFactory, UserFactory


def _session(token: object = None) -> SessionStore:
    """Build a session, holding ``token`` under the grant key if given.

    Args:
        token: The value to store, or None for no grant.

    Returns:
        The session.

    """
    session = SessionStore()
    if token is not None:
        session[GRANT_SESSION_KEY] = token
    return session


def _flag_off_for_everyone_else() -> Any:
    """Return the ``map_3d`` flag row, reset so only membership turns it on.

    waffle flushes its cache on commit, which a test transaction never
    reaches, so each test calls ``flag.flush()`` once it has set the flag up.

    Returns:
        The saved flag.

    """
    flag, _ = get_waffle_flag_model().objects.update_or_create(
        name="map_3d",
        defaults={"everyone": None, "superusers": False, "authenticated": False},
    )
    flag.users.clear()
    flag.groups.clear()
    return flag


@pytest.mark.django_db
class TestSeat:
    """``seat_map_3d_grant`` writes the token and marks the session."""

    def test_it_stores_the_token(self) -> None:
        """The token goes under the grant key."""
        session = _session()

        seat_map_3d_grant(session, "abc")

        assert session[GRANT_SESSION_KEY] == "abc"
        assert session.modified is True

    def test_the_newest_grant_wins(self) -> None:
        """A second 3D link replaces the first."""
        session = _session("old")

        seat_map_3d_grant(session, "new")

        assert session[GRANT_SESSION_KEY] == "new"


@pytest.mark.django_db
class TestGranted:
    """``map_3d_granted`` re-checks the share and its sharer on every read."""

    def test_no_grant_is_false_and_free(self, django_assert_num_queries: Any) -> None:
        """No key: no query."""
        with django_assert_num_queries(0):
            assert map_3d_granted(_session()) is False

    @pytest.mark.parametrize("value", ["", 42, ["abc"], {"token": "abc"}])
    def test_a_malformed_value_is_false(
        self, value: object, django_assert_num_queries: Any
    ) -> None:
        """A shape this code never wrote reads as no grant, and asks nothing."""
        with django_assert_num_queries(0):
            assert map_3d_granted(_session(value)) is False

    def test_an_unknown_token_is_false(self) -> None:
        """A token with no share grants nothing."""
        with override_flag("map_3d", active=True):
            assert map_3d_granted(_session("nope")) is False

    def test_a_live_share_by_a_listed_sharer_grants(self) -> None:
        """The sharer named on the flag's user list."""
        share = RouteShareFactory.create()
        flag = _flag_off_for_everyone_else()
        flag.users.add(share.created_by)
        flag.flush()

        assert map_3d_granted(_session(share.token)) is True

    def test_a_sharer_in_a_listed_group_grants(self) -> None:
        """The sharer in a group on the flag."""
        share = RouteShareFactory.create()
        group = Group.objects.create(name="3d testers")
        share.created_by.groups.add(group)
        flag = _flag_off_for_everyone_else()
        flag.groups.add(group)
        flag.flush()

        assert map_3d_granted(_session(share.token)) is True

    def test_a_superuser_sharer_grants_under_the_superusers_rule(self) -> None:
        """The launch setting: superusers first."""
        sharer = UserFactory.create(is_superuser=True)
        share = RouteShareFactory.create(
            route=RouteFactory.create(user=sharer), created_by=sharer
        )
        flag = _flag_off_for_everyone_else()
        flag.superusers = True
        flag.save()
        flag.flush()

        assert map_3d_granted(_session(share.token)) is True

    def test_a_sharer_outside_the_flag_grants_nothing(self) -> None:
        """The sharer lost 3D, so the visitor does too."""
        share = RouteShareFactory.create()
        _flag_off_for_everyone_else().flush()

        assert map_3d_granted(_session(share.token)) is False

    def test_an_expired_share_grants_nothing(self) -> None:
        """Past its window."""
        share = RouteShareFactory.create(expires_at=timezone.now() - timedelta(days=1))
        with override_flag("map_3d", active=True):
            assert map_3d_granted(_session(share.token)) is False

    def test_a_revoked_share_grants_nothing(self) -> None:
        """Its route deleted."""
        route = RouteFactory.create()
        share = RouteShareFactory.create(route=route)
        route.delete()
        with override_flag("map_3d", active=True):
            assert map_3d_granted(_session(share.token)) is False

    def test_the_example_share_grants_nothing(self) -> None:
        """Offered to every visitor, so it must not open 3D to everyone."""
        share = RouteShareFactory.create(is_example=True)
        with override_flag("map_3d", active=True):
            assert map_3d_granted(_session(share.token)) is False

    def test_no_flag_row_grants_nothing(self) -> None:
        """A database with no map_3d row reads as a flag that is off."""
        share = RouteShareFactory.create()
        get_waffle_flag_model().objects.filter(name="map_3d").delete()
        get_waffle_flag_model()(name="map_3d").flush()

        assert map_3d_granted(_session(share.token)) is False
