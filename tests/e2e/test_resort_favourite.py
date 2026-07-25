"""
tests/e2e/test_resort_favourite.py — Playwright test for the SNOW-499 resort
favourite star (the minimal resort-pin popup's entry point for favouriting
a public ``regions.Resort``).

Opening the real resort-pin popup on the map requires a genuine MapLibre
``queryRenderedFeatures`` hit on a composited WebGL frame with the (default
-off, lazy-loaded) resorts overlay enabled — exactly the kind of
frame-timing dependency ``tests/e2e/test_favourites.py``'s module docstring
already flags as unreliable in headless Chromium, and which that file avoids
by dispatching ``snowdesk:favourite-selected`` directly rather than relying
on a canvas hit-test.

This test follows the same principle, one level further: rather than
hand-crafting a ``[data-resort-star]`` button, it fetches the real
server-rendered ``api:resort_popup`` partial (the exact HTML
``static/js/map.js``'s ``openResortPopup`` would ``popup.setHTML(...)``) and
injects it into the page, then drives the star exactly as a user tapping it
inside the real popup would. This is deterministic, needs no WebGL frame,
and still exercises the real template + the real ``[data-resort-star]``
delegated handler in ``static/js/favourites.js``.

``test_resort_popup_renders_rich_metadata`` (SNOW-501) reuses the same
injection technique to assert the popup's curated metadata rows (operator,
lift count, typical season) reach the DOM for a fully-populated resort.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse
from playwright.sync_api import Page

from favourites.models import Favourite
from favourites.services import create_resort_favourite
from tests.e2e.conftest import FavouritesPage
from tests.factories import ResortFactory


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _inject_resort_popup(page: Page, live_server_url: str, resort_id: int) -> None:
    """Fetch the real resort-popup partial and inject it into the page.

    Mirrors what ``map.js``'s ``openResortPopup`` does with
    ``popup.setHTML(data.html)`` — a real server round trip landed in a
    plain host ``<div>`` rather than a ``maplibregl.Popup``, so the
    ``[data-resort-star]`` handler can be driven without a WebGL-dependent
    canvas hit-test standing between the test and the button.
    """
    url = f"{live_server_url}{reverse('api:resort_popup', args=[resort_id])}"
    page.evaluate(
        """async (url) => {
            const resp = await fetch(url);
            const data = await resp.json();
            const host = document.getElementById('e2e-resort-popup-host')
                || document.createElement('div');
            host.id = 'e2e-resort-popup-host';
            host.innerHTML = data.html;
            document.body.appendChild(host);
        }""",
        url,
    )
    page.wait_for_selector("#e2e-resort-popup-host [data-resort-star]")


@pytest.mark.django_db(transaction=True)
def test_star_creates_resort_favourite(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Tapping the not-yet-favourited star creates a Favourite for the resort.

    Online, ``window.pwaMutationQueue.enqueue`` triggers an immediate drain
    that replays the POST and creates the row; the successful drain
    re-dispatches ``snowdesk:favourites-changed``, so ``MAP.getSource
    ('favourites')`` picks up the new resort_id without a page reload.
    """
    with django_db_blocker.unblock():
        resort = ResortFactory.create(name="Verbier", latitude=46.1, longitude=7.4)

    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    # SNOW-479: the star routes through the same client mutation queue as
    # the dropped-pin create — wait for it (and IndexedDB) to be ready.
    page.wait_for_function("() => typeof window.pwaMutationQueue === 'object'")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")

    _inject_resort_popup(page, favourites_page.live_server_url, resort.pk)

    star = page.locator("#e2e-resort-popup-host [data-resort-star]")
    assert star.get_attribute("data-favourited") == "false"

    star.click()

    page.wait_for_function(
        """(resortId) => {
            const src = MAP.getSource('favourites');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.resort_id === resortId,
            );
        }""",
        arg=resort.pk,
    )

    with django_db_blocker.unblock():
        favourite = Favourite.objects.get(resort=resort)
        assert favourite.user == favourites_page.account.user
        assert favourite.name == "Verbier"


@pytest.mark.django_db(transaction=True)
def test_star_unfavourites_existing_resort_favourite(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Tapping an already-favourited star deletes the Favourite.

    Unfavourite is a plain online POST to the existing delete endpoint
    (mirroring the dropped-pin detail flow's delete), so no mutation-queue
    wait is needed here — only the direct fetch response.
    """
    with django_db_blocker.unblock():
        resort = ResortFactory.create(name="Zermatt", latitude=46.0, longitude=7.75)
        favourite = create_resort_favourite(favourites_page.account.user, resort)
        favourite_pk = favourite.pk

    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    _inject_resort_popup(page, favourites_page.live_server_url, resort.pk)

    star = page.locator("#e2e-resort-popup-host [data-resort-star]")
    assert star.get_attribute("data-favourited") == "true"

    star.click()

    page.wait_for_function(
        """(resortId) => {
            const src = MAP.getSource('favourites');
            if (!src) return true;
            const data = src.serialize().data;
            return !(data.features || []).some(
                (f) => f.properties && f.properties.resort_id === resortId,
            );
        }""",
        arg=resort.pk,
    )

    with django_db_blocker.unblock():
        assert not Favourite.objects.filter(pk=favourite_pk).exists()


@pytest.mark.django_db(transaction=True)
def test_favourited_resort_returns_to_resort_layer_when_favourites_hidden(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """A favourited resort keeps its plain dot when the favourites overlay is off.

    Regression guard: the resorts layer hides a favourited resort's dot so it
    renders only as a favourite star — but that exclusion is only justified
    while the star is actually drawn. With the favourites overlay switched
    off there is no star, so the resort must fall back to its plain dot rather
    than vanishing from the map entirely.

    Asserted on the ``resorts-pin`` layer filter (deterministic, no WebGL
    frame needed) rather than a ``queryRenderedFeatures`` hit-test. Toggling
    the favourites overlay off is driven by the two actions the layers-menu
    picker performs — set the layers' visibility to ``none`` and dispatch
    ``snowdesk:favourites-visibility-changed`` — which is the documented seam
    ``map.js`` listens on to recompute the exclusion.
    """
    with django_db_blocker.unblock():
        resort = ResortFactory.create(name="Davos", latitude=46.8, longitude=9.83)
        create_resort_favourite(favourites_page.account.user, resort)

    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    # Ensure the favourites overlay is loaded and the pre-existing resort
    # favourite is in its source — this is what populates the resort-layer
    # exclusion set, so gating on it removes the boot-time race between the
    # (eager) favourites load and the (lazy) resorts install below.
    page.evaluate(
        "() => document.dispatchEvent("
        "new CustomEvent('snowdesk:overlay-load', {detail: {key: 'favourites'}}))"
    )
    page.wait_for_function(
        """(resortId) => {
            const src = MAP.getSource('favourites');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.resort_id === resortId,
            );
        }""",
        arg=resort.pk,
    )

    # Install the (default-off, lazy) resorts overlay so resorts-pin exists.
    page.evaluate(
        "() => document.dispatchEvent("
        "new CustomEvent('snowdesk:overlay-load', {detail: {key: 'resorts'}}))"
    )
    page.wait_for_function("() => MAP.getLayer('resorts-pin') != null")

    # While favourites are visible, the favourited resort is excluded from the
    # resorts-pin layer (it shows as a star instead).
    page.wait_for_function(
        """(resortId) => {
            const f = MAP.getFilter('resorts-pin');
            return JSON.stringify(f || '').includes(String(resortId));
        }""",
        arg=resort.pk,
    )

    # Switch the favourites overlay off, exactly as the picker does.
    page.evaluate(
        """(resortId) => {
            localStorage.setItem('snowdesk.map.overlay.favourites', 'false');
            for (const id of ['favourites-pin', 'favourites-label']) {
                if (MAP.getLayer(id)) MAP.setLayoutProperty(id, 'visibility', 'none');
            }
            document.dispatchEvent(
                new CustomEvent('snowdesk:favourites-visibility-changed'));
        }""",
        arg=resort.pk,
    )

    # The exclusion clears — the resort is back on the resorts-pin layer.
    page.wait_for_function(
        """(resortId) => {
            const f = MAP.getFilter('resorts-pin');
            return !JSON.stringify(f || '').includes(String(resortId));
        }""",
        arg=resort.pk,
    )


@pytest.mark.django_db(transaction=True)
def test_resort_popup_renders_rich_metadata(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """SNOW-501: the injected popup renders curated metadata for a populated resort.

    Reuses ``_inject_resort_popup`` (the real ``api:resort_popup`` partial,
    landed in a plain host ``<div>``) to assert the operator name, a lift
    count, and the formatted typical-season text all reach the DOM — the
    same rendering path SNOW-499's favourite-star tests already exercise.
    """
    with django_db_blocker.unblock():
        resort = ResortFactory.create(
            name="Verbier",
            latitude=46.1,
            longitude=7.4,
            operator_name="Téléverbier SA",
            num_lifts=25,
            typical_season_open="12-01",
            typical_season_close="04-30",
        )

    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)
    _inject_resort_popup(page, favourites_page.live_server_url, resort.pk)

    host_text = page.locator("#e2e-resort-popup-host").inner_text()
    assert "Téléverbier SA" in host_text
    assert "25" in host_text
    assert "1 Dec" in host_text
    assert "30 Apr" in host_text
