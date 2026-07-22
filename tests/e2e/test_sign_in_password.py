"""
tests/e2e/test_sign_in_password.py — Playwright tests for the SNOW-431
password sign-in path, plus the toggle reveal that exposes it.

Two things:

1. The password-toggle reveal on ``accounts/sign_in.html``: guards the
   inline ``<script>`` there — a template-tag-in-JS parse error would stop
   the script running, so the toggle would never reveal the password
   field. On load the "Sign in with a password instead" toggle is visible
   and the password field is hidden; clicking the toggle reveals the field
   and hides the toggle.
2. The SNOW-497 happy-path journey: reveal the password field, fill a known
   email/password for a ``SubscriberFactory`` user, submit, and land on
   ``/account/manage/`` — the real ``sign_in_view`` → ``_password_sign_in``
   branch (``accounts/views.py``), not just the client-side reveal.

The sign-in page renders standalone (no bulletin data needed), so no
``_load_test_data`` fixture is required. Both tests request
``_disable_real_sw`` — neither is about the SW, and stripping it closes off
any theoretical service-worker interference with the
``/account/sign-in/`` → ``/account/manage/`` redirect.
"""

from __future__ import annotations

from typing import Any, cast

from playwright.sync_api import Page, expect
from pytest_django.live_server_helper import LiveServer

from tests.factories import SubscriberFactory

_KNOWN_PASSWORD = "correct-horse-battery-staple"  # noqa: S105 — test fixture, not a real credential


def _has_hidden_class(page: Page, selector: str) -> bool:
    """Return True when the element carries the Tailwind ``hidden`` class."""
    return cast(
        bool,
        page.evaluate(
            "(s) => { const el = document.querySelector(s);"
            " return el ? el.classList.contains('hidden') : true; }",
            selector,
        ),
    )


def _reveal_password_field(page: Page, live_server_url: str) -> None:
    """Load the sign-in page and click the toggle to reveal the password field."""
    page.goto(f"{live_server_url}/account/sign-in/")
    page.wait_for_load_state("domcontentloaded")
    page.click("#toggle-password-signin")
    expect(page.locator("#password-signin-wrap")).to_be_visible()


def test_password_toggle_reveals_field(
    live_server: LiveServer,
    page: Page,
    _disable_real_sw: None,
) -> None:
    """Clicking the toggle reveals the password field (inline JS ran cleanly)."""
    page.goto(f"{live_server.url}/account/sign-in/")
    page.wait_for_load_state("domcontentloaded")

    # On load: toggle offered, password field hidden.
    assert not _has_hidden_class(page, "#toggle-password-signin"), (
        "the password toggle should be revealed on load"
    )
    assert _has_hidden_class(page, "#password-signin-wrap"), (
        "the password field should be hidden until the toggle is clicked"
    )

    # Click the toggle → field revealed, toggle hidden.
    page.click("#toggle-password-signin")

    expect(page.locator("#password-signin-wrap")).to_be_visible()
    assert page.is_visible("#id_password"), "the password input should be visible"


def test_password_sign_in_lands_on_manage_page(
    live_server: LiveServer,
    page: Page,
    django_db_blocker: Any,
    _disable_real_sw: None,
) -> None:
    """A correct email + password lands the subscriber on /account/manage/."""
    with django_db_blocker.unblock():
        subscriber = SubscriberFactory.create()
        subscriber.user.set_password(_KNOWN_PASSWORD)
        subscriber.user.save()
        email = subscriber.user.email

    _reveal_password_field(page, live_server.url)

    page.fill('input[name="email"]', email)
    page.fill("#id_password", _KNOWN_PASSWORD)
    page.get_by_role("button", name="Send sign-in link").click()

    page.wait_for_url(f"{live_server.url}/account/manage/")
    expect(page.get_by_role("heading", name="Passkeys")).to_be_visible()
