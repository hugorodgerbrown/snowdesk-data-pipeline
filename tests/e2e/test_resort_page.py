"""
tests/e2e/test_resort_page.py — Playwright test for the resort detail page
(SNOW-504), plus (SNOW-508) the point-local field-observation panel.

Verifies the round-trip cross-link SNOW-504 introduces: the bulletin page's
"Resorts in this region" list links to a resort's own page, and that page's
"View bulletin" link returns to the same region's bulletin.

Uses the seeded navigable dataset (``_load_test_data``) — CH-4115
(Martigny/Verbier) carries both a bulletin for 2026-04-08 and several
fixture-seeded resorts (Verbier, Nendaz, Veysonnaz, Thyon), so no bespoke
factory data is needed. Plain server-rendered navigation — no map/WebGL
dependency, unlike ``test_resort_favourite.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer
from waffle.testutils import override_flag

from regions.models import Resort
from tests.factories import FieldObservationFactory


def test_bulletin_to_resort_and_back_round_trips_to_same_region(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Bulletin → resort link → resort page → "View bulletin" → same region.

    Navigates the canonical bulletin page, follows the "Verbier" link from
    the "Resorts in this region" section, confirms the resort page renders
    that resort's name, then follows its "View bulletin" link back to the
    CH-4115 region's bulletin page.
    """
    page.goto(f"{live_server.url}/ch-4115/martigny-verbier/2026-04-08/")
    page.wait_for_load_state("networkidle")

    resorts_section = page.locator('[data-testid="resorts-in-region"]')
    resorts_section.wait_for(state="visible")
    resort_link = resorts_section.locator("a", has_text="Verbier")
    resort_link.click()

    page.wait_for_load_state("networkidle")
    assert "/resorts/" in page.url
    heading = page.locator('[data-testid="resort-heading"]')
    assert heading.inner_text().strip() == "Verbier"

    bulletin_link = page.locator('[data-testid="resort-danger"] a')
    bulletin_link.click()

    page.wait_for_load_state("networkidle")
    assert "/ch-4115/" in page.url


@override_flag("field_observations", active=True)
@pytest.mark.django_db(transaction=True)
def test_resort_page_shows_point_local_observation_count(
    live_server: LiveServer,
    page: Page,
    django_db_blocker: Any,
    _load_test_data: None,
) -> None:
    """SNOW-508: the panel shows a point-local count for a geocoded resort.

    Seeds a ``FieldObservation`` at Verbier's own coordinates (fixture-seeded
    with lat/lon by ``_load_test_data``, so distance from the resort is 0 —
    guaranteed inside the default 10 km radius) and asserts the "Reported
    nearby" (point-scoped, not region-wide) heading and the report's label
    both render.
    """
    with django_db_blocker.unblock():
        resort = Resort.objects.get(name="Verbier")
        assert resort.latitude is not None
        assert resort.longitude is not None
        FieldObservationFactory.create(
            latitude=resort.latitude,
            longitude=resort.longitude,
            observation_type="WHUMPFING",
        )
        resort_url = resort.get_absolute_url()

    page.goto(f"{live_server.url}{resort_url}")
    page.wait_for_load_state("networkidle")

    heading = page.locator("#obs-counts-heading")
    heading.wait_for(state="visible")
    # The heading is visually uppercased via CSS (text-transform), which
    # Playwright's inner_text() reflects — compare against the raw DOM text
    # instead so the assertion checks the actual rendered copy.
    assert (heading.text_content() or "").strip() == "Reported nearby"
    assert "Whumpfing" in page.locator("main").inner_text()
