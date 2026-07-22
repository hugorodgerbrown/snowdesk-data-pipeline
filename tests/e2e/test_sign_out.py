"""
tests/e2e/test_sign_out.py — Playwright happy-path journey for SNOW-497:
signing out via the nav's subscriber dropdown.

Establishes a session via ``_session_login`` (the same test-only cookie
helper ``pwa_page``/``signed_in_page`` use), opens the subscriber avatar
dropdown, and submits the real POST-only ``accounts:sign_out`` form
(``templates/includes/nav.html``). Asserts the redirect lands on
``/account/sign-in/`` with the signed-out nav links visible, and that a
follow-up GET of ``/account/manage/`` redirects back to sign-in (the
session is genuinely gone, not just the nav repainted).
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from tests.e2e.conftest import _session_login
from tests.factories import SubscriberFactory


def test_sign_out_redirects_to_sign_in_with_signed_out_nav(
    live_server: LiveServer,
    page: Page,
    django_db_blocker: Any,
    _disable_real_sw: None,
) -> None:
    """Signing out redirects to sign-in and the nav reverts to signed-out."""
    with django_db_blocker.unblock():
        subscriber = SubscriberFactory.create()

    _session_login(page.context, live_server.url, subscriber.user)
    page.goto(f"{live_server.url}/account/manage/")
    page.wait_for_load_state("domcontentloaded")

    page.click("#subscriber-menu-toggle")
    menu = page.locator("#subscriber-menu")
    expect(menu).to_be_visible()

    # The sign-out control carries an explicit role="menuitem" (the
    # dropdown's items are all role="menuitem"), which overrides its
    # implicit button role in the accessibility tree.
    menu.get_by_role("menuitem", name="Sign out").click()
    page.wait_for_url(f"{live_server.url}/account/sign-in/")

    expect(page.get_by_role("link", name="Sign in")).to_be_visible()
    expect(page.get_by_role("link", name="Register")).to_be_visible()

    # The session is genuinely gone — a follow-up GET of the manage page
    # redirects straight back to sign-in rather than rendering the dashboard.
    page.goto(f"{live_server.url}/account/manage/")
    page.wait_for_url(f"{live_server.url}/account/sign-in/")
