"""tests/e2e/test_map_weather_overlay.py — A user switches the Weather overlay on and the icons appear.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

This is here for the one thing jsdom cannot answer. Every pure part of the
overlay — the WMO code → icon mapping, the label, the date projection, the
cluster-to-lowest collapse — is covered in tests/js/test_map_weather_core.js.
What is NOT is the round trip through a real canvas: a condition SVG is
multi-path and image-shaded, so it cannot be registered as an SDF mask
like the favourite star; map.js decodes it through an ``<img>`` and a 2D
context and hands the raw ImageData to ``map.addImage``. Whether that
decode actually yields a registered MapLibre image needs a browser.

Scenario: M5
"""

from __future__ import annotations

from typing import Any

import pytest
from django.utils import timezone
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

# WMO 71 — light snowfall. Its icon is the one the payload below forces the
# map to decode and register. No `-day` suffix: the set draws a day/night
# pair only where a sun or a moon appears (SNOW-791), and snow is neither.
_WEATHER_CODE = 71
_ICON = "light_snow.svg"


def _seed_public_weather(django_db_blocker: Any) -> None:
    """Give the feed one public location with a row for today.

    ``seed_test_data`` mints Locations only behind Favourites, and the feed
    is filtered by ``Location.objects.public()`` — so without a curated link
    here the payload is empty and there is no icon to decode.
    """
    from tests.factories import (
        LocationFactory,
        ResortLocationFactory,
        WeatherFactory,
    )

    with django_db_blocker.unblock():
        location = LocationFactory.create(
            name="Mont Fort", latitude=46.10, longitude=7.30, elevation_m=3328.0
        )
        ResortLocationFactory.create(location=location)
        WeatherFactory.create(
            location=location,
            observed_on=timezone.localdate(),
            weather_code=_WEATHER_CODE,
        )


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    # SNOW-794: `state="attached"`, not the default "visible". The scrubber
    # is a SEASON scrubber and is not rendered at all while the day the map
    # is showing falls outside the season — which a bare `/` in the
    # off-season is, since the default day is today. `data-state="ready"` is
    # still set on the hidden element, and it is the season-data signal this
    # wait actually wants; visibility never was.
    page.wait_for_selector('#season-scrubber[data-state="ready"]', state="attached")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


@pytest.mark.django_db(transaction=True)
def test_weather_overlay_registers_its_icons(
    live_server: LiveServer,
    page: Page,
    django_db_blocker: Any,
    _load_test_data: None,
) -> None:
    """Switching the Weather row on decodes and registers the day's icons."""
    _seed_public_weather(django_db_blocker)
    _navigate_home(page, live_server.url)

    assert page.evaluate(f"() => MAP.hasImage('{_ICON}')") is False

    page.evaluate(
        "() => document.querySelector("
        "'#basemap-menu [data-overlay-key=\"weather\"]').click()"
    )

    # The layer is added synchronously; the icon arrives only once the SVG
    # has been decoded through a canvas, which is the whole point of the test.
    page.wait_for_function("() => !!MAP.getLayer('weather-point')")
    page.wait_for_function(f"() => MAP.hasImage('{_ICON}')")
