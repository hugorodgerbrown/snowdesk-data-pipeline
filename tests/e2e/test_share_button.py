"""tests/e2e/test_share_button.py — A user shares a bulletin and the link reaches their clipboard.

Smoke test — one user journey, mirroring docs/testing-scenarios.md.
Read docs/client-side-tests.md before adding anything here: the suite
is capped, and bin/e2e-lint enforces the cap.

Scenario: none — a real clipboard ceremony needs a browser
"""

from __future__ import annotations

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def test_share_button_completes_share_flow(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Share button POSTs to the API and writes a URL to the clipboard."""
    # pageerror fires for uncaught JS exceptions, including the SyntaxErrors
    # a template tag inside an inline script produces — the SNOW-217 bug
    # this whole harness was built for.
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.goto(f"{live_server.url}/ch-4115/martigny-verbier/2026-04-08/")
    page.wait_for_load_state("networkidle")
    assert page_errors == [], f"JS errors on page load: {page_errors}"

    with page.expect_request("**/api/bulletins/share/") as req_info:
        page.click("[data-bulletin-share-button]")
    assert req_info.value.method == "POST"

    # The share flow is fetch → navigator.share (may reject headless) →
    # clipboard.writeText, all async. Poll INSIDE one evaluate so no
    # cross-call race window exists — the shape SNOW-397 debugged.
    clipboard: str | None = page.evaluate(
        """async (timeoutMs) => {
            const deadline = Date.now() + timeoutMs;
            while (Date.now() < deadline) {
              const t = await navigator.clipboard.readText();
              if (typeof t === 'string' && t.startsWith('http')) return t;
              await new Promise((r) => setTimeout(r, 100));
            }
            return null;
        }""",
        5000,
    )
    assert clipboard is not None and clipboard.startswith("http"), (
        f"clipboard value: {clipboard!r}"
    )
    assert page_errors == [], f"JS errors after share: {page_errors}"
