"""tests/e2e/test_resort_page.py — A user opens a resort, saves it, and reads its weather.

Smoke tests — the resort page's user journeys, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

The two htmx journeys below earn a browser because SNOW-650's second defect
was the *absence* of a script: the page shipped with hx-post on both its
favourite toggle and its weather retry while ``window.htmx`` stayed
undefined, so both controls were inert from the day they landed. No Python
test can observe a missing script tag from the outside, and no view test
exercises the redirect the browser follows. tests/public/test_htmx_pages.py
is the cheap structural guard; these are the two proofs that the controls
actually fire.

Scenario: none — the resort page has no manual scenario yet; add one and cite it here
"""

from __future__ import annotations

from typing import Any

import pytest
from django.utils import timezone
from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from apps.regions.models import Resort
from tests.e2e.conftest import FavouritesPage
from tests.factories import WeatherSnapshotFactory


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


@pytest.mark.django_db(transaction=True)
def test_save_resort_toggles_the_favourite_button(
    favourites_page: FavouritesPage, _load_test_data: None, django_db_blocker: Any
) -> None:
    """A signed-in user clicks "Save resort" and the button flips to "Saved".

    The toggle posts to ``favourites:resort_toggle``, keyed on ``resort.pk``
    — an integer, so the casing redirect never touched it. It was dead
    purely because the page loaded no htmx, which makes this the case that
    isolates that defect from the redirect one.
    """
    with django_db_blocker.unblock():
        resort_url = Resort.objects.get(name="Verbier").get_absolute_url()

    page = favourites_page.page
    page.goto(f"{favourites_page.live_server_url}{resort_url}")

    toggle = page.get_by_test_id("resort-favourite-toggle")
    expect(toggle).to_have_attribute("data-favourited", "false")

    toggle.click()

    expect(toggle).to_have_attribute("data-favourited", "true")


@pytest.mark.django_db(transaction=True)
def test_weather_panel_retry_swaps_in_the_forecast(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
    monkeypatch: pytest.MonkeyPatch,
    django_db_blocker: Any,
) -> None:
    """With no snapshot for today, the panel's on-load retry fills itself in.

    Restores the coverage SNOW-571 removed rather than assert a fiction.
    Exercises both halves of SNOW-650 at once: the page has to load htmx for
    the retry to fire at all, and the POST to the uppercase region id has to
    survive the canonicalisation redirect as a POST.
    """
    with django_db_blocker.unblock():
        resort = Resort.objects.get(name="Verbier")
        resort_url = resort.get_absolute_url()
        # .build() — no DB row, so the view takes the fetch path rather than
        # short-circuiting on an existing snapshot. Deliberate exception to
        # the .create() rule, as in tests/public/test_weather_snippet.py.
        snapshot = WeatherSnapshotFactory.build(
            region=resort.region,
            valid_for_date=timezone.localdate(),
            weather_code=0,
        )
    monkeypatch.setattr(
        "apps.public.views.fetch_weather_for_region",
        lambda *args, **kwargs: (snapshot, True),
    )

    page.goto(f"{live_server.url}{resort_url}")

    panel = page.get_by_test_id("resort-weather")
    expect(panel).not_to_have_attribute("data-weather-bucket", "none")
