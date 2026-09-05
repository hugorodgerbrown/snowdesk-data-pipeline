"""
tests/public/test_htmx_is_loaded.py — every page that posts a fragment
loads htmx (SNOW-834).

``public/base.html`` does not ship htmx. Each page that needs it loads
``static/js/htmx.min.js`` in its own ``extra_js`` block, which is cheap and
deliberate — most pages post nothing — but it fails SILENTLY when forgotten.
The attributes render, nothing reads them, and the control does one of two
things depending on what it is attached to: nothing at all, or, for a
``<form>`` with no ``action`` and no ``method``, a native GET back to the
same URL that clears the fields and writes nothing.

That is what shipped with the trips app. All four of its templates rendered
``hx-post`` and none of them loaded htmx, so creating a trip, editing one,
deleting one, saving its route and — on the public share page — JOINING one
were every one of them inert. Nothing caught it: the fragment endpoints are
tested by posting to them directly with the ``HX-Request`` header, which
asserts the server's half of a conversation the page could not start.

So this module asserts the pairing on the RENDERED page, where the
attributes and the script tag finally meet, rather than in the template
source — a page inherits its controls from partials it includes and its
scripts from a block it overrides, and only the response has both.

The page list is HAND-MAINTAINED: nothing here walks the URL conf, because
half these pages need a signed-in reader and a row in the database. A new
page that posts a fragment has to be added here, or nothing checks it.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.test import Client
from django.urls import reverse

from apps.routes.models import Route
from apps.trips.services.shares import mint_trip_share
from tests.factories import RouteFactory, TripFactory, UserFactory

# Any hx-* attribute in the rendered markup. Deliberately broad: the fault
# is a page carrying ANY htmx instruction with no htmx to read it, not a
# page carrying one particular verb.
_HX_ATTRIBUTE_RE = re.compile(r"\shx-[a-z-]+=")

# How every page in this project loads htmx. A path fragment rather than the
# full {% static %} output, which carries a hash in production settings.
_HTMX_SCRIPT = "js/htmx"


def _htmx_pages() -> dict[str, tuple[Client, str]]:
    """Return every page that renders an ``hx-*`` attribute, by label.

    Each entry carries its OWN client, because who is looking decides which
    controls a page renders. The share page is the case that forces it: its
    one htmx control is the recipient's Join, and an organiser opening
    their own link is already on the roster, so signing in as them would
    render a page with nothing to assert about.

    Returns:
        A dict of label → (client, URL).

    """
    organiser = UserFactory.create()
    recipient = UserFactory.create()
    organiser_client = Client()
    organiser_client.force_login(organiser)
    recipient_client = Client()
    recipient_client.force_login(recipient)

    route: Route = RouteFactory.create(user=organiser)
    # A week out rather than a fixed date: a share link's life is measured
    # from the trip's own date, so a past one would mint a dead link.
    trip = TripFactory.create(
        created_by=organiser, date=datetime.date.today() + datetime.timedelta(days=7)
    )
    mint_trip_share(organiser, trip.uuid)
    trip.refresh_from_db()

    return {
        # The map, which carries the routes / favourites / observations
        # panels and their partials.
        "home": (organiser_client, reverse("public:home")),
        "sign_in": (Client(), reverse("accounts:sign_in")),
        "account_settings": (organiser_client, reverse("accounts:settings")),
        # SNOW-834's four. Every one of these rendered hx-post with no htmx.
        "trip_new": (
            organiser_client,
            f"{reverse('trips:new')}?route={route.uuid}",
        ),
        "trip_detail": (
            organiser_client,
            reverse("trips:detail", args=[trip.uuid]),
        ),
        "trip_share_page": (
            recipient_client,
            reverse("trips:share_page", args=[trip.share_token]),
        ),
        "trips_list": (organiser_client, reverse("trips:list")),
    }


@pytest.mark.django_db
class TestHtmxIsLoadedWhereItIsUsed:
    """The pairing that had never been asserted anywhere."""

    def test_every_page_with_hx_attributes_loads_htmx(self) -> None:
        """A page that renders an hx-* attribute also loads the library."""
        for label, (client, url) in _htmx_pages().items():
            html = client.get(url).content.decode()
            if not _HX_ATTRIBUTE_RE.search(html):
                continue
            assert _HTMX_SCRIPT in html, (
                f"{label}: renders hx-* attributes but never loads htmx — "
                "its controls are inert (SNOW-834)"
            )

    @pytest.mark.parametrize(
        "label",
        ["trip_new", "trip_detail", "trip_share_page"],
    )
    def test_the_trips_pages_still_render_the_attributes(self, label: str) -> None:
        """The guard above is only worth anything if these still post.

        A page whose hx-* attributes were removed would pass the pairing
        test vacuously, so each of the three trips pages the defect landed
        on asserts that it does still carry them.
        """
        client, url = _htmx_pages()[label]

        html = client.get(url).content.decode()

        assert _HX_ATTRIBUTE_RE.search(html), f"{label}: renders no hx-* attribute"
        assert _HTMX_SCRIPT in html, f"{label}: does not load htmx"
