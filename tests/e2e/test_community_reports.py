"""
tests/e2e/test_community_reports.py — Playwright tests for the SNOW-419
"Community reports" map overlay toggle.

Uses plain ``page`` + ``live_server`` fixtures (no signed-in session
needed — unlike favourites, the overlay shows anonymised, publicly-shared
data with no per-user eligibility gate). Every test still needs
``@override_flag("community_reports", active=True)`` since the flag is
seeded ``superusers=True`` in production and the test client is
anonymous; ``override_flag`` mutates the ``Flag.everyone`` DB column,
visible to the live-server thread since pytest-django's ``live_server``
runs in-process (mirrors ``test_favourites.py``).
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from observations.models import FieldObservation
from tests.factories import FieldObservationFactory


def _navigate_home(page: Page, live_server_url: str) -> None:
    """Navigate to / and wait for the map to finish its boot fetch."""
    page.goto(f"{live_server_url}/")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_selector('#season-scrubber[data-state="ready"]')
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


@override_flag("community_reports", active=True)
@pytest.mark.django_db(transaction=True)
def test_overlay_toggle_installs_clustered_source(
    live_server: LiveServer, page: Page, django_db_blocker: Any
) -> None:
    """Enabling the overlay fetches the geojson and installs the clustered source."""
    with django_db_blocker.unblock():
        FieldObservationFactory.create(
            latitude=46.2,
            longitude=7.6,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
        )

    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")
    # Default off — unlike favourites, a shared layer of other people's
    # reports is opt-in.
    assert toggle.get_attribute("aria-checked") == "false"

    with page.expect_response(lambda r: "/api/community-reports.geojson" in r.url):
        toggle.click()

    assert toggle.get_attribute("aria-checked") == "true"

    page.wait_for_function("() => !!MAP.getSource('community-reports')")
    assert page.evaluate("() => !!MAP.getLayer('community-reports-clusters')")
    assert page.evaluate("() => !!MAP.getLayer('community-reports-cluster-count')")
    assert page.evaluate("() => !!MAP.getLayer('community-reports-point')")

    source_data = page.evaluate(
        "() => MAP.getSource('community-reports').serialize().data"
    )
    assert len(source_data["features"]) == 1
    feature = source_data["features"][0]
    assert feature["properties"]["type"] == "WHUMPFING"
    # Anonymisation: only the 3dp-rounded pair crosses the wire.
    assert feature["geometry"]["coordinates"] == [7.6, 46.2]


@override_flag("community_reports", active=True)
@pytest.mark.django_db(transaction=True)
def test_overlay_toggle_persists_across_reload(
    live_server: LiveServer, page: Page
) -> None:
    """Flipping the community-reports overlay toggle on survives a reload."""
    _navigate_home(page, live_server.url)

    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "false"

    toggle.click()
    assert toggle.get_attribute("aria-checked") == "true"

    stored = page.evaluate(
        "() => window.localStorage.getItem('snowdesk.map.overlay.community_reports')"
    )
    assert stored == "true"

    page.reload()
    page.wait_for_load_state("domcontentloaded")
    page.click("#basemap-toggle")
    toggle = page.locator('[data-overlay-key="community_reports"]')
    toggle.wait_for(state="visible")
    assert toggle.get_attribute("aria-checked") == "true"
