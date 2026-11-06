"""
tests/map/test_map_3d.py — the retired /map/3d/ address (SNOW-1450).

/map/3d/ was SNOW-1438's 3D sandbox; it is now a 302 to where its view
lives. Pins where each old link goes (a bare link and ``?probe=1`` to
``/map/?…&view=3d``, a route share to its own 3D follow), what is carried
(``probe`` and ``at``) and what is dropped (``share`` and ``layers``), that
the answer is never stored and writes nothing to the session, and that it is
the same for everyone, flagged or signed out.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from waffle.testutils import override_flag

from apps.routes.services.shares import PENDING_SESSION_KEY
from tests.factories import RouteShareFactory, UserFactory

pytestmark = pytest.mark.django_db

URL = "/map/3d/"
AT = "7.87,46.07,12.6,64,215"


def _signed_in() -> Client:
    """Return a client signed in as a fresh user.

    Returns:
        The client.

    """
    client = Client()
    client.force_login(UserFactory.create())
    return client


def test_the_route_is_still_map_3d() -> None:
    """The name stays, for the redirect alone."""
    assert reverse("public:map_3d") == URL


def test_a_bare_link_goes_to_the_3d_view() -> None:
    """/map/3d/ is a 302 to /map/?view=3d, never stored."""
    response = Client().get(URL)
    assert response.status_code == 302
    assert response["Location"] == "/map/?view=3d"
    assert response["Cache-Control"] == "no-store"


def test_the_probe_still_lands_on_the_map() -> None:
    """SNOW-1456's URL: /map/3d/?probe=1 is /map/?probe=1&view=3d."""
    response = Client().get(URL, {"probe": "1"})
    assert response["Location"] == "/map/?probe=1&view=3d"


def test_a_share_link_keeps_its_camera_and_drops_the_rest() -> None:
    """``share`` and ``layers`` are dropped, ``at`` is carried."""
    response = Client().get(
        URL, {"share": "tok", "at": AT, "layers": "steep,tours", "x": "1"}
    )
    assert response["Location"] == f"/map/?at={AT}&view=3d"


def test_probe_and_at_are_carried_in_order() -> None:
    """``probe``, then ``at``, then ``view=3d``, whatever order they came in."""
    response = Client().get(f"{URL}?at={AT}&probe=1")
    assert response["Location"] == f"/map/?probe=1&at={AT}&view=3d"


def test_a_route_share_goes_to_its_3d_follow() -> None:
    """``?route_share=T`` is /routes/s/T/3d/, carrying ``at`` alone."""
    share = RouteShareFactory.create()
    response = Client().get(
        URL, {"route_share": share.token, "at": AT, "layers": "steep", "probe": "1"}
    )
    assert response.status_code == 302
    assert response["Location"] == f"/routes/s/{share.token}/3d/?at={AT}"
    assert response["Cache-Control"] == "no-store"


def test_a_route_share_without_a_camera_carries_nothing() -> None:
    """No ``at``, no query on the follow."""
    response = Client().get(URL, {"route_share": "abc"})
    assert response["Location"] == "/routes/s/abc/3d/"


def test_a_route_share_no_url_can_carry_falls_back_to_the_map() -> None:
    """A ``/`` in the token cannot be a share URL: the bare redirect instead."""
    response = Client().get(URL, {"route_share": "a/b"})
    assert response["Location"] == "/map/?view=3d"


def test_the_redirect_writes_nothing_to_the_session() -> None:
    """The follow seats the token, not this; a HEAD leaves no session at all."""
    share = RouteShareFactory.create()
    client = Client()
    response = client.head(URL, {"route_share": share.token})
    assert response.status_code == 302
    assert settings.SESSION_COOKIE_NAME not in response.cookies
    assert PENDING_SESSION_KEY not in client.session


def test_the_answer_is_the_same_flagged_and_signed_out() -> None:
    """No flag, no sign-in and no waffle question changes where it goes."""
    query = {"share": "tok", "at": AT}
    anonymous = Client().get(URL, query)
    with override_flag("map_3d", active=True):
        flagged = _signed_in().get(URL, query)
    with override_flag("map_3d", active=False):
        unflagged = _signed_in().get(URL, query)
    assert anonymous["Location"] == flagged["Location"] == unflagged["Location"]
    assert flagged.status_code == unflagged.status_code == 302


def test_a_post_is_refused() -> None:
    """A redirect for links: GET and HEAD only."""
    assert Client().post(URL).status_code == 405
