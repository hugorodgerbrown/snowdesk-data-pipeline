"""tests/e2e/test_signout_without_javascript.py — A user signs out with JavaScript disabled.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: 22
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import NoScriptPage

ACCOUNT_URL_PATH = "/account/"

_MENU = "#subscriber-menu"
_MENU_TOGGLE = "#subscriber-menu-toggle"
_SIGN_OUT = f'{_MENU} form[action$="/account/sign-out/"] button[type="submit"]'


def _assert_signed_out(page: Page, live_server_url: str) -> None:
    """Assert the session is over: the manage page bounces to sign-in.

    Checked by navigation rather than by the absence of the nav avatar,
    because a stale render would still pass that — the session ending is
    the claim, and only the server can settle it.
    """
    page.goto(live_server_url + ACCOUNT_URL_PATH)
    page.wait_for_load_state("load")
    assert "sign-in" in page.url, (
        f"expected the manage page to redirect to sign-in after signing out; "
        f"landed on '{page.url}'"
    )


@pytest.mark.django_db(transaction=True)
def test_sign_out_works_with_javascript_disabled(no_script_page: NoScriptPage) -> None:
    """A signed-in user can end their session with scripts unavailable.

    Opens the account disclosure by clicking its ``<summary>`` — which is
    what a real user does, and which needs no script — then submits the
    sign-out form inside it.
    """
    page = no_script_page.page
    page.goto(no_script_page.live_server_url + ACCOUNT_URL_PATH)
    page.wait_for_load_state("load")
    assert "sign-in" not in page.url, "fixture failed to sign the user in"

    # Closed to begin with: the menu's contents must not be reachable (or
    # readable) until the disclosure is opened, script or no script.
    expect(page.locator(_SIGN_OUT)).to_be_hidden()

    page.click(_MENU_TOGGLE)
    expect(page.locator(_SIGN_OUT)).to_be_visible()

    page.click(_SIGN_OUT)
    page.wait_for_load_state("load")

    _assert_signed_out(page, no_script_page.live_server_url)
