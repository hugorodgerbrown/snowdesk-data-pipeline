"""
tests/e2e/test_share_button.py — Playwright smoke test for the share button.

Verifies the share-button JavaScript on the bulletin page executes without
errors, issues a POST to ``/api/bulletins/share/``, and writes a URL to the
clipboard.  This single test detects all three SNOW-217 regression patterns:

1. Template-tag-inside-JS-comment → ``SyntaxError`` at script parse → caught
   by the ``pageerror`` listener (a hard JS parse error, distinct from a
   network console message).
2. Script-runs-before-DOM → ``querySelector`` returns ``null``, listener never
   attaches, click does nothing → ``expect_request`` times out.
3. Silent ``navigator.share`` rejection without clipboard fallback → clipboard
   stays empty → the ``startswith("http")`` assertion fails.

The ``browser_context_args`` fixture in ``conftest.py`` grants
``clipboard-read`` and ``clipboard-write`` permissions so headless Chromium
does not block the clipboard write.

Console errors for missing static assets (e.g. ``output.css``, which is a
gitignored build artefact) are filtered out — those do not affect JavaScript
execution and are irrelevant to the share-button behaviour under test.

Note: the ``live_server`` fixture is session-scoped (pytest-django default);
database access is managed through ``_load_test_data`` which calls
``django_db_blocker.unblock()``, so we do not use the ``django_db`` marker
here — the live server manages its own DB connection.
"""

from __future__ import annotations

from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def test_share_button_completes_share_flow(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
) -> None:
    """Share button POSTs to the API and writes a URL to the clipboard.

    Navigates to the canonical bulletin page, clicks the share button, and
    asserts that the resulting clipboard value starts with ``http``.  Any
    JS syntax error or missing DOM element causes an earlier assertion to
    fail first.
    """
    # pageerror fires for uncaught JS exceptions, including SyntaxErrors
    # raised when the browser fails to parse an inline script.
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    # live_server.url is the base URL of the Django dev server spun up by
    # pytest-django for this session.
    page.goto(f"{live_server.url}/ch-4115/martigny-verbier/2026-04-08/")
    page.wait_for_load_state("networkidle")
    assert page_errors == [], f"JS errors on page load: {page_errors}"

    with page.expect_request("**/api/bulletins/share/") as req_info:
        page.click("[data-bulletin-share-button]")
    assert req_info.value.method == "POST"

    # Wait for the async clipboard write to settle.  The share flow involves
    # a ``fetch`` → ``navigator.share`` (async, may reject on headless
    # Chromium) → ``navigator.clipboard.writeText`` (also async) chain.
    #
    # The wait and the capture happen inside a single ``page.evaluate`` with
    # an inline ``setTimeout`` poll so no cross-call race window exists.
    # Earlier revisions split them into ``wait_for_function`` + a second
    # ``page.evaluate`` — same shape SNOW-397 debugged in ``PwaPage``: the
    # async predicate's Promise return could resolve the wait before its
    # resolved value's truthiness was checked, letting the subsequent read
    # fire while the clipboard was still empty.  One round trip, no
    # cross-call window.
    clipboard: str | None = page.evaluate(
        """async (timeoutMs) => {
            const deadline = Date.now() + timeoutMs;
            while (Date.now() < deadline) {
              const t = await navigator.clipboard.readText();
              if (typeof t === 'string' && t.startsWith('http')) {
                return t;
              }
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
