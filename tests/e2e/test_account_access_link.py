"""
tests/e2e/test_account_access_link.py — Playwright test for the account-access
("magic link") confirm page (SNOW-439).

Guards the GET-verb-does-not-grant-access invariant end to end in a real
browser: following the emailed account-access link must land on a confirm page
with a POST button and must NOT sign the user in on the GET. Only clicking the
button (the POST) establishes the session and lands on the manage page.

SNOW-514: also asserts the POST verifies the ``Account`` (``is_verified`` +
``verified_at``) — the direct regression test for the bug that motivated the
Subscriber-into-Account collapse (the magic-link POST used to flip
``Subscriber.status`` without ever touching ``Account.is_verified``).

Note: this runs over the live server's plain HTTP, so it cannot reproduce the
HTTPS ``Origin: null`` CSRF failure that SNOW-438 fixes — that fix is asserted
at the response-header level in the pytest suite (a same-origin Referrer-Policy
on the confirm pages). This test covers the SNOW-439 flow shape only.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from apps.accounts.models import Account
from apps.accounts.services.token import SALT_ACCOUNT_ACCESS, generate_token
from tests.factories import AccountFactory


def test_access_link_lands_on_confirm_then_post_signs_in(
    live_server: LiveServer,
    page: Page,
    django_db_blocker: Any,
) -> None:
    """GET renders the confirm page (no login); clicking the button signs in."""
    with django_db_blocker.unblock():
        account = AccountFactory.create(
            user__email="magic@example.com", is_verified=False
        )
        token = generate_token("magic@example.com", salt=SALT_ACCOUNT_ACCESS)

    # Follow the emailed link.
    page.goto(f"{live_server.url}/account/access/{token}/")
    page.wait_for_load_state("domcontentloaded")

    # We stay on the access URL (no auto-redirect into the account) and the
    # POST button is present.
    assert "/account/access/" in page.url, (
        "the GET link must land on the confirm page, not redirect into the account"
    )
    assert page.is_visible("button[type='submit']"), (
        "the confirm page must render a POST button"
    )

    # The account is untouched by the GET (no verification, no login).
    # Re-fetched into a fresh variable (rather than account.refresh_from_db())
    # so mypy doesn't narrow account.is_verified to Literal[False] and flag
    # the POST-state assertions below as unreachable.
    with django_db_blocker.unblock():
        before = Account.objects.get(pk=account.pk)
        assert not before.is_verified

    # Click the button → POST → verified + redirected to manage.
    page.click("button[type='submit']")
    page.wait_for_url(f"{live_server.url}/account/manage/**")

    with django_db_blocker.unblock():
        after = Account.objects.get(pk=account.pk)
        assert after.is_verified
        assert after.verified_at is not None
