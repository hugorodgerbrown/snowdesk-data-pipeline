"""
tests/e2e/test_public_pages_load.py — Playwright smoke test for SNOW-497:
the key public routes load with no JS ``pageerror``.

A lean happy-path guard, not a per-page content test: for each route in
``_PUBLIC_ROUTES`` (the map homepage, the canonical bulletin, and the
static/informational pages that ship no dynamic content of their own) the
test navigates, waits for the page's own natural "settled" signal, and
asserts no uncaught JS exception fired — the same ``pageerror`` capture
``tests/e2e/test_share_button.py`` uses, which catches template-tag-in-
JS-comment parse errors and other script-breakage regressions before they
reach a real user.

The wait differs by route because ``networkidle`` never settles on the
map homepage (MapLibre keeps fetching tiles): "/" waits for the real
``MAP.loaded()`` signal (the same technique
``tests/e2e/test_map_marker_exclusion.py`` uses), the canonical bulletin
waits for ``networkidle`` (mirroring ``test_share_button.py``), and the
plain static/informational pages — no live map, no polling JS — settle on
``load``.

Requests ``_disable_real_sw`` so no test in this module drives a real
service worker — none of these routes are about SW behaviour, only that
the page itself renders cleanly.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer


def _wait_for_map_home(page: Page) -> None:
    """Wait for the map homepage's own boot signal (SNOW-497)."""
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && MAP.loaded()"
    )


def _wait_for_bulletin(page: Page) -> None:
    """Wait for the bulletin page's inline scripts to settle."""
    page.wait_for_load_state("networkidle")


def _wait_for_static_page(page: Page) -> None:
    """Wait for a plain static/informational page to finish loading."""
    page.wait_for_load_state("load")


_PUBLIC_ROUTES: list[tuple[str, Callable[[Page], None]]] = [
    ("/", _wait_for_map_home),
    ("/ch-4115/martigny-verbier/2026-04-08/", _wait_for_bulletin),
    ("/terms/", _wait_for_static_page),
    ("/colophon/", _wait_for_static_page),
    ("/privacy/", _wait_for_static_page),
    ("/terms-of-service/", _wait_for_static_page),
    ("/how-to-read-a-bulletin/", _wait_for_static_page),
    ("/help/", _wait_for_static_page),
]


@pytest.mark.parametrize("route,wait_for_settled", _PUBLIC_ROUTES)
def test_public_route_loads_with_no_js_errors(
    live_server: LiveServer,
    page: Page,
    _load_test_data: None,
    _disable_real_sw: None,
    route: str,
    wait_for_settled: Callable[[Page], None],
) -> None:
    """Each key public route loads and fires no uncaught JS exception."""
    page_errors: list[str] = []
    page.on("pageerror", lambda err: page_errors.append(str(err)))

    page.goto(f"{live_server.url}{route}")
    wait_for_settled(page)

    assert page_errors == [], f"JS errors on {route!r}: {page_errors}"
