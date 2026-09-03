"""tests/e2e/test_setup_passkey.py — A user registers a passkey and the setup flow advances.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: none — a real WebAuthn ceremony needs a virtual authenticator
"""

from __future__ import annotations

import re
from typing import Any

from django.test import override_settings
from playwright.sync_api import BrowserContext, Page
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import _session_login
from tests.factories import AccountFactory


def _add_virtual_authenticator(context: BrowserContext, page: Page) -> None:
    """Attach a CDP internal-transport virtual authenticator (auto user-verified)."""
    cdp = context.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )


def test_passkey_cta_registers_and_advances(
    live_server: LiveServer,
    context: BrowserContext,
    page: Page,
    django_db_blocker: Any,
) -> None:
    """Clicking the CTA registers a passkey and auto-advances to manage."""
    with django_db_blocker.unblock():
        account = AccountFactory.create(is_verified=True)
        user = account.user

    _session_login(context, live_server.url, user)
    _add_virtual_authenticator(context, page)

    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    with override_settings(WEBAUTHN_ORIGIN=live_server.url, WEBAUTHN_RP_ID="localhost"):
        page.goto(f"{live_server.url}/account/setup/")
        page.wait_for_load_state("domcontentloaded")
        assert page.is_visible("#btn-register-passkey")

        page.click("#btn-register-passkey")
        # On passkey:registered the inline script sets window.location to
        # settings — the one account page left after SNOW-802 sent the hub
        # to the map. Anchored: a "**/account/**" glob would also match
        # /account/setup/, the page we started on.
        page.wait_for_url(re.compile(r"/account/settings/(\?.*)?$"), timeout=15000)

    assert re.search(r"/account/settings/(\?.*)?$", page.url), page.url
    assert page_errors == [], f"JS errors: {page_errors}"

    # The credential was persisted for the user.
    with django_db_blocker.unblock():
        assert user.passkeys.exists()
