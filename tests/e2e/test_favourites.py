"""
tests/e2e/test_favourites.py — Playwright tests for the SNOW-414 favourites
map surface (add / rename / delete pins + the favourites overlay toggle),
the SNOW-417 forecast panel on the favourite detail card, and the SNOW-474
persistent close (×) control / Esc dismissal on the favourite sheet.

Uses the ``favourites_page`` fixture (``tests/e2e/conftest.py``) — a plain
``page`` + ``live_server`` with a subscriber session, no real service-worker
lifecycle (the favourites surface doesn't need one). Every test still needs
``@override_flag("favourites", active=True)`` since the flag is seeded
``superusers=True`` in production and the fixture's subscriber isn't one;
``override_flag`` mutates ``Flag.everyone`` in the DB, which the live-server
thread sees immediately (pytest-django's ``live_server`` runs in-process).

Placing a favourite (SNOW-475) drives the touch-friendly place-picker
rather than a canvas click: ``MAP.setCenter([lon, lat])`` inside
``page.evaluate`` fires the same 'moveend' the picker listens for as a real
pan would, without depending on WebGL frame-timing in headless Chromium.

Selecting an existing pin is driven synthetically too, for the same
determinism reason: dispatching ``snowdesk:favourite-selected`` directly
(the documented contract between map.js and favourites.js — see
docs/map-and-api.md) rather than relying on ``queryRenderedFeatures``
finding a rendered symbol-layer glyph at a pixel, which requires the layer
to have actually composited a frame. SNOW-499: an existing-favourite tap now
opens an anchored popup rather than the docked sheet, so the rename/delete
tests supply the ``[data-favourite-detail]`` container map.js would (see
``_open_favourite_detail``) and assert against it.

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
    """Add -> pan the map -> name -> Save creates a Favourite row at the
    map's centre, and the map's favourites source picks it up (SNOW-475:
    the place-picker, not a draggable marker, is what places the pin).

    SNOW-479: Save is now routed through the client mutation queue rather
    than htmx. Online, enqueue triggers an immediate drain that replays the
    POST and creates the row; the sheet shows the optimistic confirmation
    (it no longer auto-hides), and the successful drain re-dispatches
    ``snowdesk:favourites-changed`` so the authoritative pin lands.
    """
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-sheet:not([hidden])")
    page.wait_for_selector("#favourite-create-form")

    # The centre pin is shown the moment the create form appears — no
    # separate "tap the map" step.
    page.wait_for_selector("#map-place-pin:not([hidden])")

    # Pan the map to a known centre — MAP.setCenter fires 'moveend', the
    # same event a real drag/pinch pan would, without depending on WebGL
    # frame timing in headless Chromium. The pin itself never moves; only
    # the map underneath it does (the actual bug this ticket fixes).
    page.evaluate("() => MAP.setCenter([7.6, 46.2])")
    page.wait_for_function(
        "() => { "
        "const el = document.querySelector('#favourite-create-form input[name=lat]'); "
        "return el && Math.abs(parseFloat(el.value) - 46.2) < 0.01; "
        "}"
    )
    lat = page.eval_on_selector(
        "#favourite-create-form input[name=lat]", "el => el.value"
    )
    lon = page.eval_on_selector(
        "#favourite-create-form input[name=lon]", "el => el.value"
    )
    assert float(lat) == pytest.approx(46.2, abs=0.01)
    assert float(lon) == pytest.approx(7.6, abs=0.01)
    # The pin stays visually centred (its CSS keeps it fixed at 50%/50% of
    # the viewport) across the pan — the coordinate above tracking the new
    # centre while the pin itself never left is what proves it.
    assert page.locator("#map-place-pin:not([hidden])").count() == 1

    page.fill("#favourite-name-input", "Test Peak")

    # SNOW-479: create is routed through the client mutation queue, not htmx —
    # wait for the queue + IndexedDB wrapper to be ready before submitting.
    page.wait_for_function("() => typeof window.pwaMutationQueue === 'object'")
    page.wait_for_function("() => typeof window.pwaDb === 'object'")
    page.click("#favourite-create-form button[type=submit]")

    # The optimistic confirmation renders immediately in the sheet (which stays
    # open showing it — it no longer auto-hides), and the place-picker
    # deactivates so the centre pin disappears. Online, the "will sync" line
    # stays hidden.
    page.wait_for_selector('#favourite-sheet:has-text("Pin saved")')
    assert (
        page.locator("#favourite-sheet [data-favourite-pending]:not([hidden])").count()
        == 0
    )
    assert page.locator("#map-place-pin:not([hidden])").count() == 0

    # Online, enqueue triggers an immediate drain that replays the POST; on
    # success the queue re-dispatches ``snowdesk:favourites-changed`` → fetch
    # geojson → ``source.setData``. Poll until the authoritative (non-pending)
    # feature lands rather than reading the source once, which races that fetch
    # — reliable locally but flaky in CI, where the create round-trip (a live
    # Open-Meteo elevation lookup) is slower. This also proves the optimistic
    # ``pending`` feature was replaced by the real server pin. The create is now
    # asynchronous (queue → drain → replay), so this poll must precede the DB
    # assertion below — the confirmation renders optimistically, before the row
    # exists server-side.
    page.wait_for_function(
        """() => {
            const src = MAP.getSource('favourites');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.name === 'Test Peak'
                       && !f.properties.pending,
            );
        }"""
    )

    with django_db_blocker.unblock():
        favourite = Favourite.objects.get(
            user=favourites_page.subscriber.user, name="Test Peak"
        )
        assert favourite.latitude == pytest.approx(46.2, abs=0.01)
        assert favourite.longitude == pytest.approx(7.6, abs=0.01)


def _open_favourite_detail(page: Page, uuid: str, name: str) -> None:
    """Open an existing favourite's rename/delete detail deterministically.

    SNOW-499: an existing-favourite tap now opens an anchored popup, not the
    docked sheet — map.js hands favourites.js an empty ``[data-favourite-detail]``
    container (via the ``snowdesk:favourite-selected`` contract) to fill, then
    anchors it in a MapLibre popup at the pin. Mounting that popup needs a
    composited WebGL ``queryRenderedFeatures`` hit (the frame-timing flake this
    file avoids), so the test reproduces map.js's DOM effect instead: create
    the same container, attach it, and dispatch the event. This exercises the
    real favourites.js fill + htmx rename/delete lifecycle without the canvas
    hit-test; the popup mounting itself is covered by an in-browser check.
    """
    page.evaluate(
        """({ uuid, name }) => {
            const container = document.createElement('div');
            container.setAttribute('data-favourite-detail', '');
            document.body.appendChild(container);
            document.dispatchEvent(new CustomEvent('snowdesk:favourite-selected', {
                detail: { uuid, name, container },
            }));
        }""",
        {"uuid": uuid, "name": name},
    )


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_rename_via_detail_popup_refreshes_label(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Renaming via the detail popup updates the DB row and the map source."""
    with django_db_blocker.unblock():
        favourite = FavouriteFactory.create(
            user=favourites_page.subscriber.user, name="Old Name"
        )
    fav_uuid = str(favourite.uuid)

    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    _open_favourite_detail(page, fav_uuid, "Old Name")
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
    """Delete via the detail popup removes the DB row and tears the detail down."""
    with django_db_blocker.unblock():
        favourite = FavouriteFactory.create(
            user=favourites_page.subscriber.user, name="Doomed Pin"
        )
    fav_uuid = str(favourite.uuid)

    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    # Listen for the popup-close signal favourites.js fires on a successful
    # delete (map.js closes the anchored popup on it).
    page.evaluate(
        """() => {
            window.__detailClosed = false;
            document.addEventListener('snowdesk:favourite-detail-close',
                () => { window.__detailClosed = true; });
        }"""
    )

    _open_favourite_detail(page, fav_uuid, "Doomed Pin")
    page.wait_for_selector(f"#favourite-{fav_uuid}")
    with page.expect_response(
        lambda r: r.url.endswith(f"/favourites/partials/{fav_uuid}/delete/")
    ):
        page.click(f"#favourite-{fav_uuid} button[type=submit]")

    # The row is gone from the detail container, and the popup-close fired.
    page.wait_for_selector(f"#favourite-{fav_uuid}", state="detached")
    page.wait_for_function("() => window.__detailClosed === true")

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
def test_create_form_close_button_hides_sheet(
    favourites_page: FavouritesPage,
) -> None:
    """The persistent × in the create-form header closes the sheet (SNOW-474)."""
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-sheet:not([hidden])")
    page.wait_for_selector("#favourite-create-form")

    # The Cancel button also carries data-action="dismiss" (SNOW-486) —
    # filter on the × glyph to target the header control specifically.
    close_btn = page.locator('#favourite-sheet [data-action="dismiss"]', has_text="×")
    assert close_btn.count() == 1
    close_btn.click()

    page.wait_for_selector("#favourite-sheet[hidden]", state="attached")


@override_flag("favourites", active=True)
def test_anonymous_signin_cta_has_close_button(
    live_server: LiveServer, page: Page
) -> None:
    """The anonymous sign-in CTA state also carries the persistent × (SNOW-474)."""
    _navigate_home(page, live_server.url)

    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-sheet:not([hidden])")

    close_btn = page.locator('#favourite-sheet [data-action="dismiss"]', has_text="×")
    assert close_btn.count() == 1
    close_btn.click()

    page.wait_for_selector("#favourite-sheet[hidden]", state="attached")


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_escape_key_closes_favourite_sheet(
    favourites_page: FavouritesPage,
) -> None:
    """Esc dismisses an open favourite sheet (SNOW-474)."""
    page = favourites_page.page
    _navigate_home(page, favourites_page.live_server_url)

    page.click("#favourite-add-btn")
    page.wait_for_selector("#favourite-sheet:not([hidden])")
    page.wait_for_selector("#favourite-create-form")

    page.keyboard.press("Escape")
    page.wait_for_selector("#favourite-sheet[hidden]", state="attached")


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
