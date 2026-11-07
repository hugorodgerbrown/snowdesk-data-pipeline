"""
tests/public/templatetags/test_map_flags.py — the ``{% map_3d_view_enabled %}`` tag.

The nav asks it whether the wordmark wears the 3D specs (SNOW-1451). It
answers ``map_shared.map_3d_view_enabled`` for the request in the context,
and False when there is no request to ask.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory
from waffle.testutils import override_flag

from apps.routes.services.map_3d_grant import GRANT_SESSION_KEY
from tests.factories import RouteShareFactory, UserFactory

TEMPLATE = Template(
    "{% load map_flags %}{% map_3d_view_enabled as on %}{% if on %}yes{% else %}no{% endif %}"
)


def _render(rf: RequestFactory | None, *, signed_in: bool = True) -> str:
    """Render the tag, with a request for a viewer or with none.

    Args:
        rf: The RequestFactory, or None to render with no request.
        signed_in: Whether the viewer is signed in.

    Returns:
        "yes" or "no".

    """
    if rf is None:
        return TEMPLATE.render(Context({}))
    request = rf.get("/")
    request.user = UserFactory.create() if signed_in else AnonymousUser()
    SessionMiddleware(lambda _req: HttpResponse()).process_request(request)
    return TEMPLATE.render(Context({"request": request}))


@pytest.mark.django_db
@pytest.mark.parametrize(("flag", "expected"), [(True, "yes"), (False, "no")])
def test_the_tag_answers_the_gate_for_a_signed_in_reader(
    rf: RequestFactory, flag: bool, expected: str
) -> None:
    """Signed in, it follows map_3d, without map_modes."""
    with (
        override_flag("map_3d", active=flag),
        override_flag("map_modes", active=False),
    ):
        assert _render(rf) == expected


@pytest.mark.django_db
def test_the_tag_is_off_for_a_signed_out_visitor(rf: RequestFactory) -> None:
    """Signed out, it is off even with the flag on."""
    with override_flag("map_3d", active=True):
        assert _render(rf, signed_in=False) == "no"


def test_the_tag_is_off_without_a_request() -> None:
    """A context with no request (an email, a bare render) gets no specs."""
    assert _render(None) == "no"


@pytest.mark.django_db
def test_the_tag_is_true_for_a_granted_signed_out_visitor(rf: RequestFactory) -> None:
    """A route share's 3D grant reaches the nav too (SNOW-1469)."""
    share = RouteShareFactory.create()
    request = rf.get("/")
    request.user = AnonymousUser()
    SessionMiddleware(lambda _req: HttpResponse()).process_request(request)
    request.session[GRANT_SESSION_KEY] = share.token
    with override_flag("map_3d", active=True):
        assert TEMPLATE.render(Context({"request": request})) == "yes"
