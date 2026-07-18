"""
tests/e2e/test_setup_passkey.py — Playwright test for the setup-page passkey
CTA (SNOW-434).

Drives the full WebAuthn registration ceremony against a CDP virtual
authenticator: click "Save a passkey for this device" → the browser's
``navigator.credentials.create()`` is answered by the virtual authenticator →
``registerPasskey`` posts the response → on ``passkey:registered`` the inline
script auto-advances to the manage page.

WebAuthn verification checks the request origin against ``WEBAUTHN_ORIGIN``, so
the test pins it to the live-server URL (the live server runs in-process, so
the override is visible to its request thread).
"""

from __future__ import annotations

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
        # On passkey:registered the inline script sets window.location to manage.
        page.wait_for_url("**/account/manage/**", timeout=15000)

    assert "/account/manage/" in page.url
    assert page_errors == [], f"JS errors: {page_errors}"

    # The credential was persisted for the user.
    with django_db_blocker.unblock():
        assert user.passkeys.exists()
