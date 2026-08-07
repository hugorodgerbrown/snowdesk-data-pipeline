"""
tests/e2e/test_map_weather_layer.py — Playwright tests for the SNOW-573
map "Weather" overlay toggle.

Modelled on test_community_reports.py's overlay-toggle test: plain
``page`` + ``live_server`` fixtures, no signed-in session needed (the
seeded data here is the public, resort-anchored half of the payload).

Pins ``navigator.onLine = true`` via ``page.add_init_script`` — headless
Chromium in this harness reports offline, which would make every layers-
menu row inert (map_layer_sync_status.js's offline gating) and the toggle
click below a no-op — mirrors test_map_layer_sync_status.py's
``_navigate_home_map_loaded`` helper.

``weather_layer`` is flag-gated (``superusers=True`` in the manifest), so
every test here forces it on with ``@override_flag`` rather than signing
in as a superuser — the flag check is server-side per request and
``override_flag`` patches that regardless of the requesting user.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.core.cache import cache
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from tests.factories import (
    ForecastPointFactory,
    ForecastPointWeatherFactory,
    ResortFactory,
)


@pytest.fixture(autouse=True)
def _clear_forecast_weather_cache() -> Any:
    """Drop the endpoint's server-side payload cache around every test.

    ``forecast_weather_geojson`` memoises its whole FeatureCollection under
    ``forecast-weather:v1:<d|all>`` for 300s. That cache lives in the
    LiveServer process and is **not** reset between tests, while
    ``django_db(transaction=True)`` truncates the tables that built it — so
    a payload assembled while another test's fixtures were in place (or
    while there were none at all) outlives the data it described and is
    served to the next test. An empty payload read back that way has no
    feature carrying an icon and no dates in the forecast window, which
    fails as "the icon was never registered" and "the row is disabled on
    today's date" — two symptoms a long way from the cause.

    Clearing on the way in and out keeps each test's page fetching a
    payload built from its own fixtures.
    """
    cache.clear()
    yield
    cache.clear()


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Load / with navigator.onLine pinned true, wait for the map to boot.

    Mirrors test_map_layer_sync_status.py's ``_navigate_home_map_loaded`` —
    toggling a lazy overlay tier needs MAP.loaded() to be true, and the
    offline-integrity gate in map_layer_sync_status.js needs
    navigator.onLine pinned so it doesn't disable every row.
    """
    page.add_init_script(
        "Object.defineProperty(navigator, 'onLine', "
        "{ value: true, configurable: true });"
    )
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _toggle_weather_on(page: Page) -> Any:
    """Open the layers menu and click the Weather row; returns the row locator."""
    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="weather"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "false"
    with page.expect_response(lambda r: "/api/forecast-weather.geojson" in r.url):
        toggle.click()
    assert toggle.get_attribute("aria-checked") == "true"
    return toggle


@override_flag("weather_layer", active=True)
@pytest.mark.django_db(transaction=True)
def test_overlay_toggle_installs_source_and_registers_icons(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Enabling the overlay fetches the geojson, installs the symbol layer,
    and rasterises + registers the icon the seeded point's weather uses.
    """
    with django_db_blocker.unblock():
        point = ForecastPointFactory.create(latitude=46.2, longitude=7.6)
        ResortFactory.create(
            name="Verbier",
            latitude=46.2,
            longitude=7.6,
            forecast_point=point,
        )
        ForecastPointWeatherFactory.create(
            forecast_point=point,
            valid_for_date=datetime.date.today(),
        )

    _navigate_home(page, live_server.url)
    _toggle_weather_on(page)

    page.wait_for_function("() => !!MAP.getSource('weather')")
    assert page.evaluate("() => !!MAP.getLayer('weather-point')")
    assert page.evaluate(
        "() => JSON.stringify(MAP.getLayoutProperty('weather-point', 'icon-image'))"
        " === JSON.stringify(['get', 'icon'])"
    )

    # The layer's data-driven icon expression reads whatever filename the
    # seeded point's weather projected to; assert map.addImage actually
    # registered THAT icon (not a hardcoded filename, which would be
    # brittle against day/night and WMO-code-bucket details resolved
    # server-side).
    page.wait_for_function(
        "() => { const src = MAP.getSource('weather'); if (!src) return false;"
        " const data = src.serialize().data;"
        " const f = data.features.find((x) => x.properties.icon);"
        " return !!f && MAP.hasImage(f.properties.icon); }"
    )
    icon_filename = page.evaluate(
        "() => MAP.getSource('weather').serialize().data.features"
        ".find((x) => x.properties.icon).properties.icon"
    )
    assert icon_filename.endswith(".svg")
    assert page.evaluate(f"() => MAP.hasImage({icon_filename!r})")


@override_flag("weather_layer", active=True)
@pytest.mark.django_db(transaction=True)
def test_sync_dot_resolves_cached_after_toggle(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """The Weather row's sync dot resolves to "cached" once the toggle-on
    load succeeds — the same optimistic markCached() path every other
    lazy overlay tier uses.
    """
    with django_db_blocker.unblock():
        point = ForecastPointFactory.create(latitude=46.2, longitude=7.6)
        ResortFactory.create(
            name="Verbier",
            latitude=46.2,
            longitude=7.6,
            forecast_point=point,
        )
        ForecastPointWeatherFactory.create(
            forecast_point=point,
            valid_for_date=datetime.date.today(),
        )

    _navigate_home(page, live_server.url)
    _toggle_weather_on(page)

    dot = page.locator('[data-overlay-key="weather"] .sync-dot')
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"weather\"] .sync-dot')"
        ".getAttribute('data-sync-state') === 'cached'"
    )
    assert dot.get_attribute("data-sync-state") == "cached"


@override_flag("weather_layer", active=True)
@pytest.mark.django_db(transaction=True)
def test_row_disables_on_out_of_window_date(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Scrubbing to a date outside the stored forecast window disables the row.

    Dispatches ``snowdesk:date-changed`` directly (the explicit-dispatch
    technique ``test_favourites.py`` uses for ``snowdesk:favourite-selected``)
    rather than driving the real scrubber UI, since only the date value —
    not the drag gesture — matters to the behaviour under test.
    """
    with django_db_blocker.unblock():
        point = ForecastPointFactory.create(latitude=46.2, longitude=7.6)
        ResortFactory.create(
            name="Verbier",
            latitude=46.2,
            longitude=7.6,
            forecast_point=point,
        )
        ForecastPointWeatherFactory.create(
            forecast_point=point,
            valid_for_date=datetime.date.today(),
        )

    _navigate_home(page, live_server.url)
    toggle = _toggle_weather_on(page)
    page.wait_for_function("() => !!MAP.getSource('weather')")

    # Wait for the row to reach a settled (non out-of-window) state before
    # jumping to a date outside the window, so the assertion below is
    # attributable to the date change rather than a boot-time default.
    # This has to be a wait, not a bare read: the comment above claimed to
    # wait while the assertion sampled once, so on a loaded machine it could
    # catch the row still carrying its pre-payload disabled state and fail
    # with "assert 'true' is None" — a race in the test, not in the layer.
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"weather\"]')"
        ".getAttribute('aria-disabled') === null"
    )

    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('snowdesk:date-changed', "
        "{ detail: { date: '2099-01-01', source: 'test' } }))"
    )
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"weather\"]')"
        ".getAttribute('aria-disabled') === 'true'"
    )
    assert toggle.get_attribute("aria-disabled") == "true"
    assert toggle.get_attribute("title")

    # Scrubbing back into the window re-enables the row.
    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('snowdesk:date-changed', "
        f"{{ detail: {{ date: '{datetime.date.today().isoformat()}', source: 'test' }} }}))"
    )
    page.wait_for_function(
        "() => document.querySelector('[data-overlay-key=\"weather\"]')"
        ".getAttribute('aria-disabled') === null"
    )
