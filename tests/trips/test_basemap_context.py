"""
tests/trips/test_basemap_context.py — the trip pages' basemap catalogue
(SNOW-829).

Every trip surface that renders a map hands the page the whole
``settings.BASEMAP_STYLES`` catalogue plus the default's key, rather than
one resolved URL, so the page can resolve the READER's own choice from
``localStorage['snowdesk.map.basemap']``. These tests assert the catalogue
arrives on all four render paths, and that it arrives exactly once per page
— both map-bearing partials read it by id, and both render on
``trips/trip.html``.

**The coverage decision, named.** ``swisstopo_*`` covers Switzerland,
``ign_plan`` France, ``basemap_at`` Austria; outside their country they
render blank. This ticket ACCEPTS that rather than falling back to the
default, and the page says so and offers the standard map instead — see
``static/js/basemap_style_core.js``'s header for why the fallback could not
be driven off declared coverage (swisstopo's own declared bbox contains
Chamonix; IGN's is the whole world). The server-side half of that decision
is what ``test_the_page_offers_a_way_out_of_a_blank_basemap`` pins: the
notice ships with the page, hidden, rather than being built in JavaScript.
"""

from __future__ import annotations

import json
import re

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse
from freezegun import freeze_time

from apps.routes.models import Route
from apps.trips.services.shares import mint_trip_share
from tests.factories import RouteFactory, TripFactory

# Well before TripFactory's default date, so a minted link is live.
_NOW = "2026-01-10T09:00:00+00:00"

_CATALOGUE_RE = re.compile(
    r'<script id="trip-basemaps" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _catalogues(body: str) -> list[dict[str, str]]:
    """Return every basemap catalogue the page emitted, parsed."""
    return [json.loads(m) for m in _CATALOGUE_RE.findall(body)]


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTheCatalogueReachesEveryTripSurface:
    """The catalogue, not one resolved URL, on all four render paths."""

    def test_the_object_page_carries_it(self, client: Client) -> None:
        """The reader's own basemap, on the page they read the route on."""
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.get(reverse("trips:detail", args=[trip.uuid]))

        body = response.content.decode()
        assert _catalogues(body) == [dict(settings.BASEMAP_STYLES)]
        assert f'data-default-basemap-key="{settings.BASEMAP}"' in body

    def test_the_share_page_carries_it(self, client: Client) -> None:
        """And so does the surface a recipient opens."""
        trip = TripFactory.create()
        mint_trip_share(trip.created_by, trip.uuid)
        trip.refresh_from_db()

        response = client.get(reverse("trips:share_page", args=[trip.share_token]))

        assert _catalogues(response.content.decode()) == [dict(settings.BASEMAP_STYLES)]

    def test_the_authoring_form_carries_it(self, client: Client) -> None:
        """The organiser drops the pin on the same terrain they read it on.

        The picker and the trip map resolving differently would show the
        same track over two basemaps, which reads as two different places.
        """
        route: Route = RouteFactory.create()
        client.force_login(route.user)

        response = client.get(f"{reverse('trips:new')}?route={route.uuid}")

        assert _catalogues(response.content.decode()) == [dict(settings.BASEMAP_STYLES)]

    def test_a_rejected_submission_re_renders_the_default_key(
        self, client: Client
    ) -> None:
        """The 400 re-render is a FRAGMENT, and inherits the page's catalogue.

        It swaps the form — picker container included — into a page whose
        ``#trip-basemaps`` element it never touches, so the catalogue is
        still there to be read. What the fragment must carry is the
        container's own ``data-default-basemap-key``, because that
        attribute is re-rendered with it: without it a validation error
        would silently drop the organiser back to a resolution with no
        fallback.
        """
        route: Route = RouteFactory.create()
        client.force_login(route.user)

        response = client.post(
            reverse("trips:create"),
            {"route": str(route.uuid), "name": ""},
            headers={"HX-Request": "true"},
        )

        assert response.status_code == 400
        body = response.content.decode()
        assert "data-trip-meeting-picker" in body
        assert f'data-default-basemap-key="{settings.BASEMAP}"' in body
        # The fragment does not re-emit it — that would be a second
        # element with the same id once the swap lands.
        assert _catalogues(body) == []


@freeze_time(_NOW)
@pytest.mark.django_db
class TestItIsEmittedExactlyOnce:
    """Both map partials read it by id, and both render on the object page."""

    def test_the_organiser_page_emits_one_catalogue_not_two(
        self, client: Client
    ) -> None:
        """The trip map and the edit panel's picker share one element.

        Two ``json_script`` calls would put the same id on the document
        twice and ``getElementById`` would quietly answer with the first —
        which is why the catalogue is emitted by the PAGE.
        """
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.get(reverse("trips:detail", args=[trip.uuid]))

        body = response.content.decode()
        # The organiser's page really does render both maps.
        assert "data-trip-map" in body
        assert "data-trip-meeting-picker" in body
        assert len(_catalogues(body)) == 1


@freeze_time(_NOW)
@pytest.mark.django_db
class TestTheBlankBasemapNotice:
    """The accepted coverage decision, and the way out it ships with."""

    def test_the_page_offers_a_way_out_of_a_blank_basemap(self, client: Client) -> None:
        """A national basemap outside its country draws nothing here.

        The decision is to ACCEPT that rather than silently fall back —
        a reader who chose a national basemap knows what they chose — and
        to say so with one control that swaps to the standard map. The
        notice ships hidden with the page so its strings are translated;
        ``trip_map.js`` reveals it only when the canvas reports that
        nothing but this page's own sources drew.
        """
        trip = TripFactory.create()
        client.force_login(trip.created_by)

        response = client.get(reverse("trips:detail", args=[trip.uuid]))

        body = response.content.decode()
        assert 'id="trip-basemap-blank"' in body
        assert "data-blank-switch" in body
        # Hidden until the canvas says otherwise.
        assert re.search(r'id="trip-basemap-blank"\s+hidden', body)

    def test_the_meeting_picker_offers_no_such_switch(self, client: Client) -> None:
        """The picker is a CONTROL, and swapping its style moves the ground.

        The organiser is dragging a pin onto terrain they picked the
        basemap for; a mid-placement style swap would move it under them.
        """
        route: Route = RouteFactory.create()
        client.force_login(route.user)

        response = client.get(f"{reverse('trips:new')}?route={route.uuid}")

        body = response.content.decode()
        assert "data-trip-meeting-picker" in body
        assert "trip-basemap-blank" not in body
