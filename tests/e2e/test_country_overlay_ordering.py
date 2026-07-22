"""
tests/e2e/test_country_overlay_ordering.py — Playwright regression test for
SNOW-493 finding 5: enabling a foreign country and the L1/L2 tiers in
either order must end with the foreign country's hierarchy data present.

``ensureCountryLoaded`` (``static/js/map.js``) used to only retain a
fetched country's L1 (major-region) / L2 (sub-region) data when
``majorGeojsonCache``/``subGeojsonCache`` already existed — which only
happens once the user has enabled L1/L2 at least once. Enabling a foreign
country BEFORE L1/L2's first enable silently discarded that country's
hierarchy data; ``loadedCountries`` then marked the country as loaded, so
a later L1/L2 enable — which only ever fetches Switzerland — never
re-fetched it, leaving the foreign country's L1/L2 hierarchy permanently
missing regardless of toggle order afterwards.

Two orderings are covered:

- Country first, then L1/L2 (the order the bug manifested in).
- L1/L2 first, then country (the order that always worked, kept here as
  a control so a regression in the fix itself would show up as an
  asymmetry between the two).
"""

from __future__ import annotations

import time
from typing import Any, cast

import pytest
from django.core.management import call_command
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && !!MAP.getSource('regions')"
    )


def _fr_major_region_count(page: Page) -> int:
    """Count FR features currently in the ``major-regions`` source."""
    return cast(
        int,
        page.evaluate(
            """() => {
            const src = MAP.getSource('major-regions');
            if (!src) return 0;
            const data = src.serialize().data;
            return (data.features || []).filter(
                (f) => f.properties && f.properties.country === 'FR',
            ).length;
        }"""
        ),
    )


def _poll_fr_major_region_count(page: Page, *, timeout_s: float = 5.0) -> int:
    """Poll ``_fr_major_region_count`` until it's positive, or time out.

    ``ensureCountryLoaded``'s major/sub-region merge is async relative to
    the 'regions' source update the caller already waited for, so a
    single read can race a still-in-flight fetch.
    """
    deadline = time.monotonic() + timeout_s
    count = 0
    while time.monotonic() < deadline:
        count = _fr_major_region_count(page)
        if count > 0:
            return count
        time.sleep(0.05)
    return count


@pytest.mark.django_db(transaction=True)
def test_country_enabled_before_l1_still_shows_its_hierarchy(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Enabling FR before L1's first enable must not lose FR's L1 data.

    Regression coverage for SNOW-493 finding 5's original repro order:
    country toggle first, tier toggle second.
    """
    with django_db_blocker.unblock():
        call_command("loaddata", "eaws_FR", verbosity=0)

    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    country_toggle = page.locator('[data-overlay-key="country.fr"]')
    country_toggle.wait_for(state="visible")
    with page.expect_response(lambda r: "regions" in r.url and "country=fr" in r.url):
        country_toggle.click()
    page.wait_for_function(
        """() => {
            const src = MAP.getSource('regions');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.country === 'FR',
            );
        }"""
    )

    l1_toggle = page.locator('[data-overlay-key="l1"]')
    l1_toggle.wait_for(state="visible")
    with page.expect_response(
        lambda r: "major-regions" in r.url and "country=ch" in r.url
    ):
        l1_toggle.click()
    page.wait_for_function(
        "() => MAP.getLayer('major-regions-line') && !!MAP.getSource('major-regions')"
    )

    assert _poll_fr_major_region_count(page) > 0, (
        "FR's major-region hierarchy must survive enabling L1 after the country toggle"
    )


@pytest.mark.django_db(transaction=True)
def test_country_enabled_after_l1_shows_its_hierarchy(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Enabling L1 first, then FR, must show FR's L1 data too (control case)."""
    with django_db_blocker.unblock():
        call_command("loaddata", "eaws_FR", verbosity=0)

    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    l1_toggle = page.locator('[data-overlay-key="l1"]')
    l1_toggle.wait_for(state="visible")
    with page.expect_response(
        lambda r: "major-regions" in r.url and "country=ch" in r.url
    ):
        l1_toggle.click()
    page.wait_for_function(
        "() => MAP.getLayer('major-regions-line') && !!MAP.getSource('major-regions')"
    )

    country_toggle = page.locator('[data-overlay-key="country.fr"]')
    country_toggle.wait_for(state="visible")
    with page.expect_response(lambda r: "regions" in r.url and "country=fr" in r.url):
        country_toggle.click()
    page.wait_for_function(
        """() => {
            const src = MAP.getSource('regions');
            if (!src) return false;
            const data = src.serialize().data;
            return (data.features || []).some(
                (f) => f.properties && f.properties.country === 'FR',
            );
        }"""
    )

    assert _poll_fr_major_region_count(page) > 0, (
        "FR's major-region hierarchy must be present when L1 was enabled first"
    )
