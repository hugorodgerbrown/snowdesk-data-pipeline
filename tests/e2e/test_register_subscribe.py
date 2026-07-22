"""
tests/e2e/test_register_subscribe.py — Playwright happy-path journey for
SNOW-497: subscribing to a region from the inline bulletin-page CTA.

Drives the anonymous-visitor branch of
``accounts/templates/accounts/partials/subscribe_form.html`` (embedded on
every bulletin page via ``public/bulletin.html``, not the homepage): fills
the email field inside ``#subscribe-cta-<region_id>``, submits, and asserts
the HTMX swap lands the "Check your inbox" success fragment
(``accounts/partials/subscribe_success_access.html``).

Email delivery itself is asserted at the pytest layer
(``tests/accounts/test_email.py`` via ``mail.outbox``, the ``ImmediateBackend``
locmem-style path) — a Playwright test cannot read a real inbox, so this
journey only asserts the visible confirmation card the anonymous visitor
actually sees.
"""

from __future__ import annotations

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

_BULLETIN_URL = "/ch-4115/martigny-verbier/2026-04-08/"
_REGION_ID = "CH-4115"


def test_subscribe_from_bulletin_shows_check_your_inbox(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
    _disable_real_sw: None,
) -> None:
    """Submitting the inline subscribe form swaps in the "Check your inbox" card."""
    page.goto(f"{live_server.url}{_BULLETIN_URL}")
    page.wait_for_load_state("networkidle")

    cta = page.locator(f"#subscribe-cta-{_REGION_ID}")
    cta.locator('input[name="email"]').fill("journey-subscriber@example.com")

    with page.expect_response(
        lambda r: "/account/" in r.url and r.request.method == "POST"
    ):
        cta.get_by_role("button", name="Subscribe").click()

    # hx-swap="outerHTML" replaces the #subscribe-cta-<region_id> element
    # wholesale with the response fragment, which carries no wrapping id of
    # its own — so the success heading is located page-wide, not scoped to
    # the (now-gone) CTA id.
    heading = page.get_by_role("heading", name="Check your inbox")
    heading.wait_for(state="visible")
