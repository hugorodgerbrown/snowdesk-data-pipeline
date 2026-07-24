"""
tests/e2e/test_favourite_detail.py — Playwright test for the SNOW-507 favourite
full page and the region-page "Your favourites here" list.

Covers the node-in-hierarchy navigation SNOW-507 adds: a signed-in user opens a
region bulletin, sees their favourite in that region under "Your favourites
here", clicks through to the favourite's own full page (``/favourites/<uuid>/``),
and follows the "← Region bulletin" back-link home. A second test asserts the
ownership no-oracle contract — another user's favourite uuid returns 404, not
403 or a distinguishing error.

Deterministic and WebGL-free: unlike the map-surface favourites tests, these
drive plain server-rendered pages (the region bulletin and the favourite page),
so no MapLibre frame timing is involved. Uses the shared ``favourites_page``
fixture (a signed-in ``Account``) plus ``_load_test_data`` for a real,
navigable region (CH-4115, Martigny / Verbier — the canonical seeded region used
across the e2e suite).
"""

from __future__ import annotations

from typing import Any

import pytest
from waffle.testutils import override_flag

from regions.models import MicroRegion
from tests.e2e.conftest import FavouritesPage
from tests.factories import FavouriteFactory

# CH-4115 (Martigny / Verbier) is seeded by ``seed_test_dataset`` and is the
# canonical navigable region used throughout the e2e suite.
_REGION_ID = "CH-4115"


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("_load_test_data")
def test_region_page_links_to_favourite_and_back(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Region bulletin → "Your favourites here" → favourite page → back.

    The signed-in user has one favourite resolved to CH-4115. Its region
    bulletin page must list it under "Your favourites here"; the link opens the
    favourite's own full page; the back-link returns to the bulletin.
    """
    with django_db_blocker.unblock():
        region = MicroRegion.objects.get(region_id=_REGION_ID)
        favourite = FavouriteFactory.create(
            user=favourites_page.account.user,
            name="My Verbier spot",
            region=region,
        )
        bulletin_url = region.get_absolute_url()
    fav_uuid = str(favourite.uuid)

    page = favourites_page.page
    page.goto(f"{favourites_page.live_server_url}{bulletin_url}")
    page.wait_for_load_state("domcontentloaded")

    section = page.locator('[data-testid="favourites-in-region"]')
    section.wait_for(state="visible")
    link = section.get_by_role("link", name="My Verbier spot")
    href = link.get_attribute("href")
    assert href is not None and href.endswith(f"/favourites/{fav_uuid}/")

    link.click()

    # The favourite's own full page renders — the wrapping <main> plus the
    # reused detail card, with the title showing the favourite's name.
    page.wait_for_selector('[data-testid="favourite-detail"]')
    page.wait_for_selector('[data-testid="favourite-card"]')
    assert (
        page.locator('[data-testid="favourite-card-title"]').inner_text().strip()
        == "My Verbier spot"
    )

    # The "← Region bulletin" back-link returns to the region bulletin page.
    page.click('[data-testid="favourite-detail-back-link"]')
    page.wait_for_url(f"{favourites_page.live_server_url}{bulletin_url}")
    page.wait_for_selector('[data-testid="favourites-in-region"]')


@override_flag("favourites", active=True)
@pytest.mark.django_db(transaction=True)
def test_foreign_favourite_uuid_returns_404(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Another user's favourite uuid returns 404 (no existence oracle).

    The signed-in user requests a favourite owned by a different user. The
    response must be 404 — indistinguishable from a nonexistent uuid — so the
    page gives no signal that the favourite exists.
    """
    with django_db_blocker.unblock():
        # A favourite owned by a *different* user (FavouriteFactory's default
        # ``user`` SubFactory, not ``favourites_page.account``).
        other_favourite = FavouriteFactory.create(name="Not yours")
    other_uuid = str(other_favourite.uuid)

    page = favourites_page.page
    response = page.goto(f"{favourites_page.live_server_url}/favourites/{other_uuid}/")
    assert response is not None
    assert response.status == 404
