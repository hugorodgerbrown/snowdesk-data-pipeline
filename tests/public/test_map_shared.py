"""
tests/public/test_map_shared.py — the map's shared gates (apps/public/map_shared.py).

``map_3d_view_enabled`` is the one rule for who gets the map's 3D view:
the map, the home page, ``/offline/`` and the nav's wordmark all read it.
Since SNOW-1451 it is the ``map_3d`` flag alone, for signed-in readers; a
signed-out request with no route share 3D grant (SNOW-1469) makes no query.
The grant's own rules are tested in tests/routes/test_map_3d_grant.py.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory
from waffle.testutils import override_flag

from apps.public.map_shared import map_3d_view_enabled, map_modes_enabled
from apps.routes.services.map_3d_grant import GRANT_SESSION_KEY
from tests.factories import RouteShareFactory, UserFactory


def _request(rf: RequestFactory, *, signed_in: bool) -> HttpRequest:
    """Build a GET / with a session and a viewer.

    Args:
        rf: The RequestFactory building the request.
        signed_in: Whether the viewer is a signed-in user.

    Returns:
        The request.

    """
    request = rf.get("/")
    request.user = UserFactory.create() if signed_in else AnonymousUser()
    SessionMiddleware(lambda _req: HttpResponse()).process_request(request)
    return request


@pytest.mark.django_db
@pytest.mark.parametrize("map_modes", [True, False])
@pytest.mark.parametrize("map_3d", [True, False])
def test_map_3d_view_follows_map_3d_alone_when_signed_in(
    rf: RequestFactory, map_3d: bool, map_modes: bool
) -> None:
    """Signed in, the 3D view is map_3d's answer whatever map_modes says."""
    request = _request(rf, signed_in=True)
    with (
        override_flag("map_3d", active=map_3d),
        override_flag("map_modes", active=map_modes),
    ):
        assert map_3d_view_enabled(request) is map_3d
        assert map_modes_enabled(request) is map_modes


@pytest.mark.django_db
@pytest.mark.parametrize("map_modes", [True, False])
@pytest.mark.parametrize("map_3d", [True, False])
def test_map_3d_view_is_off_and_free_when_signed_out(
    rf: RequestFactory, django_assert_num_queries: Any, map_3d: bool, map_modes: bool
) -> None:
    """Signed out it is always False, and asks waffle nothing."""
    request = _request(rf, signed_in=False)
    with (
        override_flag("map_3d", active=map_3d),
        override_flag("map_modes", active=map_modes),
        django_assert_num_queries(0),
    ):
        assert map_3d_view_enabled(request) is False


@pytest.mark.django_db
def test_a_signed_out_visitor_with_a_live_grant_gets_3d(rf: RequestFactory) -> None:
    """A route share's 3D grant opens 3D signed out (SNOW-1469)."""
    share = RouteShareFactory.create()
    request = _request(rf, signed_in=False)
    request.session[GRANT_SESSION_KEY] = share.token
    with override_flag("map_3d", active=True):
        assert map_3d_view_enabled(request) is True


@pytest.mark.django_db
def test_a_grant_whose_sharer_left_map_3d_is_flat(rf: RequestFactory) -> None:
    """The grant holds only while the sharer is inside the flag."""
    share = RouteShareFactory.create()
    request = _request(rf, signed_in=False)
    request.session[GRANT_SESSION_KEY] = share.token
    with override_flag("map_3d", active=False):
        assert map_3d_view_enabled(request) is False


@pytest.mark.django_db
def test_a_signed_in_reader_outside_the_flag_gets_3d_from_a_grant(
    rf: RequestFactory,
) -> None:
    """The grant is for anyone who followed the link, signed in or not.

    ``override_flag`` turns the flag on for everyone, so the reader is kept
    out of it by asking with a flag that covers the sharer only.
    """
    from waffle import get_waffle_flag_model

    share = RouteShareFactory.create()
    flag, _ = get_waffle_flag_model().objects.update_or_create(
        name="map_3d", defaults={"everyone": None, "superusers": False}
    )
    flag.users.set([share.created_by])
    flag.flush()
    request = _request(rf, signed_in=True)
    request.session[GRANT_SESSION_KEY] = share.token

    assert map_3d_view_enabled(request) is True


@pytest.mark.django_db
def test_the_answer_is_kept_on_the_request(
    rf: RequestFactory, django_assert_num_queries: Any
) -> None:
    """/map/ asks twice; a granted visitor's second ask costs nothing."""
    share = RouteShareFactory.create()
    request = _request(rf, signed_in=False)
    request.session[GRANT_SESSION_KEY] = share.token
    with override_flag("map_3d", active=True):
        assert map_3d_view_enabled(request) is True
        with django_assert_num_queries(0):
            assert map_3d_view_enabled(request) is True
