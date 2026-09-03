"""tests/e2e/test_account_access_link.py — A returning user follows the emailed access link and lands signed in.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: 10
"""

from __future__ import annotations

import re
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

    page.goto(f"{live_server.url}/account/access/{token}/")
    page.wait_for_load_state("domcontentloaded")

    assert "/account/access/" in page.url, (
        "the GET link must land on the confirm page, not redirect into the account"
    )
    assert page.is_visible("button[type='submit']"), (
        "the confirm page must render a POST button"
    )

    # The GET must not verify or sign anyone in — that is the whole point of
    # the confirm step. Re-fetched into a fresh name rather than
    # refresh_from_db() so mypy does not narrow is_verified to Literal[False]
    # and call the post-POST assertions unreachable.
    with django_db_blocker.unblock():
        before = Account.objects.get(pk=account.pk)
        assert not before.is_verified

    page.click("button[type='submit']")
    # SNOW-802: lands on the map (map.js consumes and strips ?panel=).
    # Anchored on the origin so /account/access/<token>/ cannot match.
    page.wait_for_url(re.compile(r"^https?://[^/]+/(\?.*)?$"))

    with django_db_blocker.unblock():
        after = Account.objects.get(pk=account.pk)
        assert after.is_verified
        assert after.verified_at is not None
