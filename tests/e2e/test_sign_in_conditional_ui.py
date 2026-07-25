"""
tests/e2e/test_sign_in_conditional_ui.py — Playwright test for the deferred
passkey conditional-UI ceremony on the sign-in page (SNOW-519).

Every WebAuthn credential provider (1Password, Dashlane, iCloud Keychain, the
browser's own manager, …) surfaces its UI in response to the
``navigator.credentials.get()`` call itself. Calling it at page load therefore
pops an unsolicited passkey prompt for anyone running a third-party provider.
The sign-in page instead starts the conditional ceremony on first *focus* of
the email field, tying it to a deliberate sign-in gesture.

This test guards that wiring by instrumenting ``navigator.credentials.get`` and
``PublicKeyCredential.isConditionalMediationAvailable`` before the page loads:

1. On a bare page load ``navigator.credentials.get`` is never called.
2. Focusing the email field starts the ceremony — ``get`` is called exactly
   once, with ``mediation: 'conditional'``.

The sign-in page renders standalone (no bulletin data needed), so no
``test_data`` fixture is required.
"""

from __future__ import annotations

from typing import cast

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

# Injected before any page script. Forces conditional mediation to appear
# available and replaces navigator.credentials.get with a recorder that never
# resolves (so no real WebAuthn UI is driven), tracking call count and the
# mediation of the most recent call.
_INSTRUMENT = """
(() => {
  window.__condGetCalls = 0;
  window.__condGetLastMediation = null;
  const forceAvailable = () => Promise.resolve(true);
  try {
    PublicKeyCredential.isConditionalMediationAvailable = forceAvailable;
  } catch (_e) {
    Object.defineProperty(
      PublicKeyCredential, 'isConditionalMediationAvailable',
      { value: forceAvailable, configurable: true, writable: true },
    );
  }
  navigator.credentials.get = (opts) => {
    window.__condGetCalls += 1;
    window.__condGetLastMediation = (opts && opts.mediation) || null;
    return new Promise(() => {});  // never settles — no real ceremony
  };
})();
"""


def _cond_get_calls(page: Page) -> int:
    """Return how many times the instrumented ``credentials.get`` has run."""
    return cast(int, page.evaluate("() => window.__condGetCalls"))


def test_conditional_ceremony_deferred_to_email_focus(
    live_server: LiveServer,
    page: Page,
) -> None:
    """The conditional ceremony starts on email focus, not on page load."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))
    page.add_init_script(_INSTRUMENT)

    page.goto(f"{live_server.url}/account/sign-in/")
    page.wait_for_load_state("domcontentloaded")

    # A bare load must not touch navigator.credentials.get — give any
    # (regressed) on-load ceremony room to fire before asserting it did not.
    page.wait_for_timeout(300)
    assert _cond_get_calls(page) == 0, (
        "the conditional ceremony must not start on page load — it pops an "
        "unsolicited passkey prompt for third-party providers (SNOW-519)"
    )

    # Focusing the email field is the deliberate sign-in gesture that starts it.
    page.focus('input[name="email"]')
    page.wait_for_function("() => window.__condGetCalls > 0", timeout=5000)

    assert _cond_get_calls(page) == 1, "focus should start exactly one ceremony"
    assert page.evaluate("() => window.__condGetLastMediation") == "conditional", (
        "the focus-started ceremony must use conditional mediation (autofill)"
    )
    assert page_errors == [], f"JS errors: {page_errors}"
