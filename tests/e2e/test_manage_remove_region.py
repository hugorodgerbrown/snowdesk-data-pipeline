"""tests/e2e/test_manage_remove_region.py — A subscriber removes a region from their account.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

This journey earns a browser because the defect it guards (SNOW-650) was
invisible to every other layer: the manage page builds its hx-post from the
uppercase region id, the canonicalisation decorator answered 301, and the
browser — not htmx, not Django — replayed the POST as a GET into a 405. A
view test posting straight to the lowercase URL never triggers it, which is
why the Remove button was broken for every user, on every click, past a
green suite.

Scenario: 12
"""

from __future__ import annotations

from typing import Any

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import FavouritesPage
from tests.factories import MicroRegionFactory, SubscriptionFactory


@pytest.mark.django_db(transaction=True)
def test_remove_button_removes_the_subscription_card(
    favourites_page: FavouritesPage, django_db_blocker: Any
) -> None:
    """Sign in with one subscribed region, click Remove, the card goes.

    ``remove_region`` returns an empty 200 and the card's
    ``hx-swap="outerHTML"`` takes it out of the DOM, so the card count
    dropping to zero is the whole assertion.
    """
    with django_db_blocker.unblock():
        region = MicroRegionFactory.create(region_id="CH-4115", name="Valais")
        SubscriptionFactory.create(account=favourites_page.account, region=region)

    page = favourites_page.page
    page.goto(f"{favourites_page.live_server_url}/account/")

    card = page.locator(".subscription-card")
    expect(card).to_have_count(1)

    card.get_by_role("button", name="Remove").click()

    expect(card).to_have_count(0)
