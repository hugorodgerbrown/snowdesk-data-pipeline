"""
tests/e2e/test_signout_without_javascript.py — SNOW-616: sign out is
reachable with JavaScript unavailable.

Sign out was only reachable through a JavaScript-driven disclosure menu:
the nav rendered ``#subscriber-menu`` with the ``hidden`` attribute and an
inline script removed it on a click. With scripts off the attribute stayed,
so a signed-in user had no way to end their session. Finding M8 in
``docs/code-reviews/2026-08-03-js-review.md``.

The project position is that JavaScript is progressive enhancement and core
functionality must work without it. Ending a session on a shared or borrowed
device is squarely core, and it was the one control the review found on the
wrong side of that line.

The fix is markup, not a new endpoint — sign out was always a POST form, so
once the menu opens without script the control works. The nav is now a
native ``<details>``/``<summary>`` disclosure and the script only adds
click-outside and Escape.

Two halves, one test each:

  * ``test_sign_out_works_with_javascript_disabled`` — the fix. Runs on the
    ``no_script_page`` fixture (``java_script_enabled=False``), which this
    ticket introduced; there was no JS-disabled case in the suite before,
    which is why a control that only existed once a script had run looked
    identical to one that did not.
  * ``test_escape_still_closes_the_menu_with_javascript_enabled`` — the
    enhancement the rewrite had to preserve. ``<details>`` gives Enter and
    Space for free but nothing for Escape.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import FavouritesPage, NoScriptPage

MANAGE_URL_PATH = "/account/manage/"

_MENU = "#subscriber-menu"
_MENU_TOGGLE = "#subscriber-menu-toggle"
_SIGN_OUT = f'{_MENU} form[action$="/account/sign-out/"] button[type="submit"]'


def _assert_signed_out(page: Page, live_server_url: str) -> None:
    """Assert the session is over: the manage page bounces to sign-in.

    Checked by navigation rather than by the absence of the nav avatar,
    because a stale render would still pass that — the session ending is
    the claim, and only the server can settle it.
    """
    page.goto(live_server_url + MANAGE_URL_PATH)
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
    page.goto(no_script_page.live_server_url + MANAGE_URL_PATH)
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


@pytest.mark.django_db(transaction=True)
def test_sign_out_is_also_reachable_from_the_account_page(
    no_script_page: NoScriptPage,
) -> None:
    """The manage page carries its own sign-out control.

    The secondary half of SNOW-616: the nav control is the primary one, but
    the account page is where a user goes looking for account actions, and
    on a borrowed device this is the one they need.
    """
    page = no_script_page.page
    page.goto(no_script_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")

    page.click('[data-testid="manage-sign-out"]')
    page.wait_for_load_state("load")

    _assert_signed_out(page, no_script_page.live_server_url)


@pytest.mark.django_db(transaction=True)
def test_escape_still_closes_the_menu_with_javascript_enabled(
    favourites_page: FavouritesPage,
) -> None:
    """Escape closes the disclosure and returns focus to its summary.

    ``<details>`` handles opening, closing on a second click, Enter and
    Space natively — Escape is not among them, so it stays the inline
    script's job and has to survive the rewrite.
    """
    page = favourites_page.page
    page.goto(favourites_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")

    page.click(_MENU_TOGGLE)
    expect(page.locator(_SIGN_OUT)).to_be_visible()

    page.keyboard.press("Escape")

    expect(page.locator(_SIGN_OUT)).to_be_hidden()
    assert (
        page.evaluate("() => document.activeElement && document.activeElement.id")
        == "subscriber-menu-toggle"
    ), (
        "Escape must return focus to the control that opened the menu, or a "
        "keyboard user is dropped back at the top of the document"
    )


@pytest.mark.django_db(transaction=True)
def test_click_outside_still_closes_the_menu(favourites_page: FavouritesPage) -> None:
    """A click elsewhere closes the disclosure.

    The other behaviour ``<details>`` does not provide — it stays open until
    its own summary is clicked again.
    """
    page = favourites_page.page
    page.goto(favourites_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")

    page.click(_MENU_TOGGLE)
    expect(page.locator(_SIGN_OUT)).to_be_visible()

    page.click("h1")

    expect(page.locator(_SIGN_OUT)).to_be_hidden()
