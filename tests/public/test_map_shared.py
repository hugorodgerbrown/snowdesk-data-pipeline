"""
tests/public/test_map_shared.py — the map's shared gates (apps/public/map_shared.py).

``map_3d_view_enabled`` is the one rule for who gets the map's 3D view:
the map, the home page, ``/offline/`` and the nav's wordmark all read it.
Since SNOW-1451 it is the ``map_3d`` flag alone, for signed-in readers; a
signed-out request is short-circuited and makes no query.
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
from tests.factories import UserFactory


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
