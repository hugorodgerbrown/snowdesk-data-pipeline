"""
tests/e2e/test_favourites_offline.py — Playwright tests for the SNOW-418
favourites offline-read cache.

Uses ``signed_in_page`` (``tests/e2e/conftest.py``) — a real-SW,
authenticated-subscriber page — since the offline repaint depends on the
same-origin shell being served from Cache Storage by ``sw.js``'s
network-first navigation strategy once the connection drops (a plain
``page``/``live_server`` pair with no real SW registration can't reload
while offline at all).

Two scenarios:

- ``test_offline_reload_repaints_favourites_list_from_cache`` — a normal
  online visit caches the roster + an opened detail card, then an offline
  reload repaints the list from ``data:favourites`` with an explicit
  "as of" stamp.
- ``test_offline_expired_rating_shows_expired_not_stale_chip`` — a
  synthetic cached record whose ``generated_at`` is 3 days old (past the
  172800s / 48h ``unsafe_after_seconds`` horizon) renders the explicit
  EXPIRED state offline, never a stale-looking danger-tile chip.
"""

from __future__ import annotations

import datetime
from typing import Any

import pytest
from django.utils import timezone

from tests.e2e.conftest import SignedInPage
from tests.factories import FavouriteFactory, MicroRegionFactory, RegionDayRatingFactory

MANAGE_URL_PATH = "/account/manage/"

# Polls window.pwaDb until a row with the given uuid lands in
# data:favourites — used both to know the natural write-through has
# completed (Test A) and, in Test B, to guarantee our synthetic
# page.evaluate() write lands strictly AFTER the real roster sync so it
# isn't clobbered by it.
_WAIT_FOR_CACHED_UUID_JS = """async (uuid) => {
    if (typeof window.pwaDb !== 'object') return false;
    const rows = await window.pwaDb.getAll('data:favourites');
    return rows.some((r) => r.uuid === uuid);
}"""


@pytest.mark.django_db(transaction=True)
def test_offline_reload_repaints_favourites_list_from_cache(
    signed_in_page: SignedInPage, django_db_blocker: Any
) -> None:
    """An offline reload repaints the favourites list from data:favourites.

    Online: navigate to the manage page, let the roster load, open the
    one pin's detail card (caching both the roster and card records).
    Offline: reload — the roster's ``htmx:sendError`` fires, and
    ``favourites_offline.js`` repaints the list container from the
    cached record, complete with an "as of HH:MM" stamp.
    """
    with django_db_blocker.unblock():
        region = MicroRegionFactory.create()
        RegionDayRatingFactory.create(region=region, max_rating="considerable")
        favourite = FavouriteFactory.create(
            user=signed_in_page.account.user, name="Cache Peak", region=region
        )
    fav_uuid = str(favourite.uuid)

    page = signed_in_page.page
    page.goto(signed_in_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")
    assert "sign-in" not in page.url

    page.wait_for_selector('[data-testid="favourite-list-row"]')
    page.click('[data-testid="favourite-list-row"] button:has-text("Details")')
    page.wait_for_selector('#favourite-card-panel [data-testid="favourite-card"]')

    # Both the roster and card write-throughs are async (htmx:afterOnLoad
    # handlers) — wait for the cached row to actually land before flipping
    # offline, rather than racing the reload against an in-flight IDB write.
    page.wait_for_function(_WAIT_FOR_CACHED_UUID_JS, arg=fav_uuid)

    page.context.set_offline(True)
    try:
        page.reload()
        page.wait_for_load_state("load")
        page.wait_for_selector('[data-testid="favourite-card"]')
        card_text = page.locator('[data-testid="favourite-card"]').first.inner_text()
        assert "as of" in card_text.lower()
    finally:
        page.context.set_offline(False)


@pytest.mark.django_db(transaction=True)
def test_offline_expired_rating_shows_expired_not_stale_chip(
    signed_in_page: SignedInPage, django_db_blocker: Any
) -> None:
    """A cached rating past its 48h horizon renders EXPIRED, never a chip.

    Injects a synthetic ``data:favourites`` record with a 3-day-old
    ``generated_at`` and the default ``unsafe_after_seconds`` (172800 —
    48h), then asserts the offline repaint shows the explicit "expired"
    state instead of a stale-looking ``danger-tile`` chip.
    """
    with django_db_blocker.unblock():
        favourite = FavouriteFactory.create(
            user=signed_in_page.account.user, name="Stale Peak"
        )
    fav_uuid = str(favourite.uuid)

    page = signed_in_page.page
    page.goto(signed_in_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")
    assert "sign-in" not in page.url

    page.wait_for_selector('[data-testid="favourite-list-row"]')
    # Wait for the natural roster sync to land the (real, rating-less)
    # record first, so our synthetic override below — written strictly
    # afterwards — is the one still present when we go offline.
    page.wait_for_function(_WAIT_FOR_CACHED_UUID_JS, arg=fav_uuid)

    generated_at = (timezone.now() - datetime.timedelta(days=3)).isoformat(
        timespec="seconds"
    )
    page.evaluate(
        """({ uuid, name, generatedAt }) => window.pwaDb.put('data:favourites', {
            uuid,
            name,
            latitude: 46.1,
            longitude: 7.4,
            elevation: 1500,
            region: { id: 1, name: 'Test region', bulletin_url: '/ch-1000/test/' },
            rating: { max_rating: 'high', max_subdivision: '', digit: '4' },
            weather: null,
            generated_at: generatedAt,
            unsafe_after_seconds: 172800,
          })""",
        {"uuid": fav_uuid, "name": "Stale Peak", "generatedAt": generated_at},
    )

    page.context.set_offline(True)
    try:
        page.reload()
        page.wait_for_load_state("load")
        page.wait_for_selector('[data-testid="favourite-card-rating-expired"]')
        assert page.locator('[data-testid="favourite-card-rating-chip"]').count() == 0
    finally:
        page.context.set_offline(False)
