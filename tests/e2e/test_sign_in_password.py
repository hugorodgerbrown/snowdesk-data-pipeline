"""tests/e2e/test_sign_in_password.py — A returning user reveals their password on the sign-in page.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: 21
"""

from __future__ import annotations

from typing import cast

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


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


def test_password_toggle_reveals_field(
    live_server: LiveServer,
    page: Page,
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
    page.wait_for_timeout(50)  # let the synchronous handler settle

    assert not _has_hidden_class(page, "#password-signin-wrap"), (
        "the password field should be visible after clicking the toggle"
    )
    assert page.is_visible("#id_password"), "the password input should be visible"
