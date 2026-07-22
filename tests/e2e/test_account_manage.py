"""
tests/e2e/test_account_manage.py — Playwright happy-path journey for
SNOW-497: the authenticated subscriptions dashboard actually renders.

Signs in a ``Subscriber`` with one subscribed region (``SubscriptionFactory``)
via the ``_session_login`` cookie helper, GETs ``/account/manage/``, and
asserts a ``.subscription-card`` bearing the region name renders — not just
that the page avoids the anonymous redirect (which
``test_account_access_link.py`` already covers via the magic-link flow).
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import _session_login
from tests.factories import MicroRegionFactory, SubscriberFactory, SubscriptionFactory


def test_manage_page_shows_subscription_card_for_region(
    live_server: LiveServer,
    page: Page,
    django_db_blocker: Any,
    _disable_real_sw: None,
) -> None:
    """The manage page renders a subscription card naming the subscribed region."""
    with django_db_blocker.unblock():
        subscriber = SubscriberFactory.create()
        region = MicroRegionFactory.create(name="Journey Test Region")
        SubscriptionFactory.create(subscriber=subscriber, region=region)

    _session_login(page.context, live_server.url, subscriber.user)
    page.goto(f"{live_server.url}/account/manage/")
    page.wait_for_load_state("domcontentloaded")

    card = page.locator(".subscription-card")
    expect(card).to_have_count(1)
    expect(card.locator("h2")).to_have_text("Journey Test Region")
