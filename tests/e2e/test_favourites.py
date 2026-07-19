"""
tests/e2e/test_favourites.py — Playwright tests for the SNOW-414 favourites
map surface (add / rename / delete pins + the favourites overlay toggle) and
the SNOW-417 forecast panel on the favourite detail card.

Uses the ``favourites_page`` fixture (``tests/e2e/conftest.py``) — a plain
``page`` + ``live_server`` with a subscriber session, no real service-worker
lifecycle (the favourites surface doesn't need one). Every test still needs
``@override_flag("favourites", active=True)`` since the flag is seeded
``superusers=True`` in production and the fixture's subscriber isn't one;
``override_flag`` mutates ``Flag.everyone`` in the DB, which the live-server
thread sees immediately (pytest-django's ``live_server`` runs in-process).

Two interactions are driven synthetically rather than via a literal
Playwright mouse click on the MapLibre canvas, for determinism in headless
Chromium (no WebGL frame-timing dependency):

- Placing a favourite: ``MAP.fire('click', {lngLat, point})`` — the
  placement handler is a plain (non-layer-scoped) ``map.on('click', ...)``,
  so a synthetic fire drives the exact same code path a real canvas click
  would.
- Selecting an existing pin: dispatching ``snowdesk:favourite-selected``
  directly (the documented contract between map.js and favourites.js — see
  docs/map-and-api.md) rather than relying on ``queryRenderedFeatures``
  finding a rendered symbol-layer glyph at a pixel, which requires the
  layer to have actually composited a frame.

The forecast-panel test (SNOW-417) instead drives the manage page's
"My favourites" list — the "Details" button hx-gets ``favourite_card``
into ``#favourite-card-panel``, which is where the compact day strip and
the expandable hourly ``<details>`` actually render (the map pin-tap
trigger only shows the SNOW-413 rename/delete row, per
``_favourite_card.html``'s docstring).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.urls import reverse
from django.utils import timezone as django_timezone
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from favourites.models import Favourite
from tests.e2e.conftest import FavouritesPage
from tests.factories import FavouriteFactory, ForecastPointWeatherFactory


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_signed_in_add_flow_creates_favourite(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Add -> synthetic map click -> name -> Save creates a Favourite row
    and the map's favourites source picks it up.
    """
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-sheet:not([hidden])")

    # Synthetic map click — drives the same plain map.on('click', ...)
    # handler a real canvas tap would, without depending on WebGL frame
    # timing in headless Chromium.
    page.evaluate(
        "() => MAP.fire('click', {"
        " lngLat: { lng: 7.6, lat: 46.2 },"
        " point: MAP.project([7.6, 46.2]) })"
    )
    page.wait_for_selector("#favourite-create-form")
    assert page.locator(".maplibregl-marker").count() == 1

    page.fill("#favourite-name-input", "Test Peak")
    page.click("#favourite-create-form button[type=submit]")

    page.wait_for_selector("#favourite-sheet[hidden]", state="attached")
    assert page.locator(".maplibregl-marker").count() == 0

    with django_db_blocker.unblock():
        favourite = Favourite.objects.get(
            user=favourites_page.subscriber.user, name="Test Peak"
        )
        assert favourite.latitude == pytest.approx(46.2, abs=0.01)
        assert favourite.longitude == pytest.approx(7.6, abs=0.01)

    source_data = page.evaluate("() => MAP.getSource('favourites').serialize().data")
    names = [f["properties"]["name"] for f in source_data["features"]]
    assert "Test Peak" in names


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_rename_via_detail_sheet_refreshes_label(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Renaming via the detail sheet updates the DB row and the map source."""
    with django_db_blocker.unblock():
        favourite = FavouriteFactory.create(
            user=favourites_page.subscriber.user, name="Old Name"
        )
    fav_uuid = str(favourite.uuid)

    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.evaluate(
        "({ uuid, name }) => document.dispatchEvent("
        "new CustomEvent('snowdesk:favourite-selected', { detail: { uuid, name } }))",
        {"uuid": fav_uuid, "name": "Old Name"},
    )
    page.wait_for_selector(f"#favourite-{fav_uuid}")
    name_input = page.locator(f"#favourite-{fav_uuid} input[name=name]")
    assert name_input.input_value() == "Old Name"

    name_input.fill("New Name")
    # Wait for the actual rename response, not just the DOM value fill()
    # already set client-side (a wait_for_function on the input's value
    # would be trivially true immediately, before the htmx round-trip even
    # starts, racing ahead of the DB write below).
    with page.expect_response(
        lambda r: r.url.endswith(f"/favourites/partials/{fav_uuid}/rename/")
    ):
        name_input.dispatch_event("change")

    with django_db_blocker.unblock():
        favourite.refresh_from_db()
        assert favourite.name == "New Name"

    page.wait_for_function(
        "(uuid) => { "
        "const data = MAP.getSource('favourites').serialize().data; "
        "const f = data.features.find((x) => x.properties.uuid === uuid); "
        "return !!f && f.properties.name === 'New Name'; "
        "}",
        arg=fav_uuid,
    )


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_delete_removes_pin(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Delete via the detail sheet removes the DB row and closes the sheet."""
    with django_db_blocker.unblock():
        favourite = FavouriteFactory.create(
            user=favourites_page.subscriber.user, name="Doomed Pin"
        )
    fav_uuid = str(favourite.uuid)

    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.evaluate(
        "({ uuid, name }) => document.dispatchEvent("
        "new CustomEvent('snowdesk:favourite-selected', { detail: { uuid, name } }))",
        {"uuid": fav_uuid, "name": "Doomed Pin"},
    )
    page.wait_for_selector(f"#favourite-{fav_uuid}")
    page.click(f"#favourite-{fav_uuid} button[type=submit]")

    page.wait_for_selector("#favourite-sheet[hidden]", state="attached")

    with django_db_blocker.unblock():
        assert not Favourite.objects.filter(pk=favourite.pk).exists()


@override_flag("favourites", active=True)
def test_anonymous_add_shows_signin_cta(live_server: LiveServer, page: Page) -> None:
    """An anonymous visitor tapping Add favourite sees a sign-in CTA link."""
    _navigate_home(page, live_server.url)

    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-sheet:not([hidden])")

    signin_link = page.locator("#favourite-sheet a")
    assert signin_link.count() == 1
    href = signin_link.get_attribute("href")
    assert href is not None
    # Tighten beyond "not None": the CTA must point at the real sign-in route,
    # so a broken namespace / empty favourites_signin_url is caught here too.
    assert reverse("accounts:sign_in") in href


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_overlay_toggle_persists_across_reload(
    favourites_page: FavouritesPage,
) -> None:
    """Flipping the favourites overlay toggle off survives a page reload."""
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="favourites"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "true"

    toggle.click()
    assert toggle.get_attribute("aria-checked") == "false"

    stored = page.evaluate(
        "() => window.localStorage.getItem('snowdesk.map.overlay.favourites')"
    )
    assert stored == "false"

    page.reload()
    page.wait_for_load_state("domcontentloaded")
    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="favourites"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "false"


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_forecast_panel_hourly_detail_expands_and_collapses(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """The near-term hourly <details> on the forecast panel expands/collapses (SNOW-417).

    Opens the manage page's "My favourites" list, clicks "Details" to
    hx-get the detail card into #favourite-card-panel, then drives the
    expandable hourly panel's native <details>/<summary> toggle — no
    HTMX round-trip is involved in the expand/collapse itself, only in
    getting the card onto the page in the first place.
    """
    with django_db_blocker.unblock():
        favourite = FavouriteFactory.create(
            user=favourites_page.subscriber.user, name="Powder Stash"
        )
        # Match the view's own start_date basis (timezone.localdate()) —
        # favourite.created_at.date() is a UTC date and would diverge from
        # it around UTC midnight when TIME_ZONE is ahead of UTC.
        today = django_timezone.localdate()
        ForecastPointWeatherFactory.create(
            forecast_point=favourite.forecast_point, valid_for_date=today
        )

    page = favourites_page.page
    page.goto(f"{favourites_page.live_server_url}/account/manage/")
    page.wait_for_selector('[data-testid="favourite-list-row"]')

    page.click('[data-testid="favourite-list-row"] >> text=Details')
    page.wait_for_selector('[data-testid="favourite-forecast-panel"]')

    hourly_panel = page.locator('[data-testid="favourite-forecast-hourly"]')
    hourly_panel.wait_for(state="visible")
    assert hourly_panel.get_attribute("open") is None

    hourly_panel.locator("summary").click()
    assert hourly_panel.get_attribute("open") is not None

    hourly_panel.locator("summary").click()
    assert hourly_panel.get_attribute("open") is None
