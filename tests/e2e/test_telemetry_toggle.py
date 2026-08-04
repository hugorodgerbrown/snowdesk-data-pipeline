"""
tests/e2e/test_telemetry_toggle.py — Playwright tests for the SNOW-387
"Share anonymous usage data" switch on the manage page.

M2 (2026-08-03 JS review): the switch is server-rendered ``disabled`` +
``aria-busy="true"``, and the only code that clears that state is the inline
block at the bottom of ``apps/accounts/templates/accounts/manage.html``. That
block is a parser-blocking classic script inside the page content, so it used
to run before ``static/js/telemetry.js`` — a ``defer`` script in
``base.html`` — had installed ``window.pwaTelemetry``. The guard never
passed, and the switch stayed disabled and announced "busy" for every user on
every load. These tests assert the state the binding is supposed to reach.

Uses ``signed_in_page`` (``tests/e2e/conftest.py``) — a real-SW,
authenticated page — because ``/account/manage/`` redirects to sign-in
otherwise, and because ``window.pwaTelemetry.isOptIn()`` reads the persisted
preference out of the IndexedDB database ``static/js/db.js`` opens on load.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import SignedInPage

MANAGE_URL_PATH = "/account/manage/"

TOGGLE_SELECTOR = "[data-telemetry-toggle]"


@pytest.mark.django_db(transaction=True)
def test_toggle_becomes_enabled(signed_in_page: SignedInPage) -> None:
    """The switch loses ``disabled`` and ``aria-busy`` once the page has loaded."""
    page = signed_in_page.page
    page.goto(signed_in_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")

    toggle = page.locator(TOGGLE_SELECTOR)
    toggle.wait_for(state="attached")

    # The binding resolves isOptIn() asynchronously (IndexedDB read), so
    # poll rather than asserting on the first paint.
    page.wait_for_function(
        """() => {
            const el = document.querySelector('[data-telemetry-toggle]');
            return !!el && !el.disabled && !el.hasAttribute('aria-busy');
          }"""
    )

    assert toggle.is_enabled()
    assert toggle.get_attribute("aria-busy") is None
    # aria-checked must carry a real answer, not the rendered placeholder
    # left behind by a binding that never ran.
    assert toggle.get_attribute("aria-checked") in {"true", "false"}


@pytest.mark.django_db(transaction=True)
def test_toggle_click_persists_opt_in(signed_in_page: SignedInPage) -> None:
    """Clicking the switch flips aria-checked and persists via setOptIn()."""
    page = signed_in_page.page
    page.goto(signed_in_page.live_server_url + MANAGE_URL_PATH)
    page.wait_for_load_state("load")
    page.wait_for_function(
        """() => {
            const el = document.querySelector('[data-telemetry-toggle]');
            return !!el && !el.disabled && !el.hasAttribute('aria-busy');
          }"""
    )

    toggle = page.locator(TOGGLE_SELECTOR)
    before = toggle.get_attribute("aria-checked") == "true"
    toggle.click()
    after = toggle.get_attribute("aria-checked") == "true"
    assert after is not before

    # The click handler writes through window.pwaTelemetry.setOptIn, so the
    # persisted preference must agree with the new switch state.
    persisted = page.evaluate("async () => window.pwaTelemetry.isOptIn()")
    assert bool(persisted) is after
