"""
tests/e2e/_spike_sw_update.py — SNOW-389 spike Q1: SW-update determinism.

Throwaway harness answering the plan's Q1: can Playwright deterministically
drive a real service-worker install -> activate -> update -> waiting cycle
against the ``live_server``, with ``#sw-update-banner`` appearing on cue?

Unlike every other e2e file in this repo, this test does NOT disable the
real SW (no ``_disable_real_sw`` / no stripped ``navigator.serviceWorker``)
— it lets ``/sw.js`` genuinely register, waits for it to self-activate (no
prior controller, so ``sw.js`` never needs ``skipWaiting()`` for the first
install), then forces the *next* ``/sw.js`` fetch to return byte-different
content so the browser detects and installs an update.

First finding, worth recording here because it shaped the approach: a
service worker's own script fetch (both the initial registration fetch and
every subsequent ``registration.update()`` re-fetch) is NOT visible to
Playwright's ``page.on("request", ...)`` listener, nor interceptable via
``page.route()`` or ``context.route()`` — neither fired even once across
several manual runs. This is a genuine Playwright/Chromium limitation (the
SW's own script fetch happens outside the page's document-driven network
stack that Playwright's routing hooks into), not a flake. Client-side
interception is therefore not viable for this scenario at all.

The working alternative, used below: ``live_server`` runs the Django app
in-process (a background thread in the same interpreter, not a subprocess),
so the test can monkeypatch ``public.views._serve_sw_file`` directly —
changing what bytes the live server itself returns for ``/sw.js`` on the
second-and-later request. This is arguably more faithful to a real deploy
than any client-side trick would have been: it's a genuine server-side
content change, exactly what happens when ``CACHE_VERSION`` gets bumped and
shipped.

Run 30x back-to-back via::

    for i in $(seq 1 30); do uv run tox -e e2e -- tests/e2e/_spike_sw_update.py || break; done

If flake > 0/30, P4 (Scenario 4 — SW-driven update banner) stays manual;
``test_pwa_lifecycle_update.py`` still ships with the P5/P6 header-drift /
forced-update tests, which don't depend on this mechanism.

Deleted once the outcome is captured in ``tests/e2e/_spike_results.py`` —
see the SNOW-389 plan, step 1.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from django.http import HttpResponse
from playwright.sync_api import Page
from pytest_django.live_server_helper import LiveServer

from public import views as public_views


@pytest.fixture(scope="session")
def browser_type_launch_args(
    browser_type_launch_args: dict[str, Any],
) -> dict[str, Any]:
    """Point Playwright at a pre-installed chromium binary when one is set.

    See ``tests/e2e/test_pwa_db.py`` for the identical fixture and rationale.
    """
    executable = os.environ.get("PLAYWRIGHT_EXECUTABLE_PATH")
    if executable:
        return {**browser_type_launch_args, "executable_path": executable}
    return browser_type_launch_args


def test_real_sw_installs_and_update_shows_banner(
    live_server: LiveServer,
    page: Page,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Register the real SW, force a byte-different update, assert the banner.

    Four steps, matching the SNOW-389 plan's Q1 spike (the client-side
    route-interception step from the original plan text was replaced by
    server-side monkeypatching — see the module docstring):

    1. Let ``/sw.js`` register normally (no kill-switch, no stripped
       ``navigator.serviceWorker``).
    2. Wait for the freshly-registered worker to self-activate and claim
       the page — first install has no existing controller, so ``sw.js``
       activates automatically without needing ``skipWaiting()``.
    3. Flip the live server's ``/sw.js`` response to byte-different content
       and call ``registration.update()`` from the page (registration used
       ``updateViaCache: 'none'``, so the browser always re-fetches on
       ``update()`` rather than serving from HTTP cache).
    4. Wait for the new worker to land on ``waiting`` and
       ``#sw-update-banner`` to become visible.
    """
    page.goto(live_server.url)
    page.wait_for_load_state("load")

    # Step 2: first-install activation. clients.claim() in sw.js's activate
    # handler claims this very page once it resolves, so controller flips
    # from null to the new worker without a reload.
    page.wait_for_function(
        "() => navigator.serviceWorker.controller?.state === 'activated'",
        timeout=5000,
    )

    # Step 3: monkeypatch the live server's /sw.js response — append a
    # comment so the diff is unambiguous without depending on the current
    # CACHE_VERSION string. live_server runs in-process (a background
    # thread in this same interpreter), so this affects real requests the
    # browser makes against it.
    original_serve = public_views._serve_sw_file

    def _serve_modified(static_relative_path: str) -> HttpResponse:
        response = original_serve(static_relative_path)
        if static_relative_path == "js/sw.js":
            response.content = response.content + b"\n// spike-marker\n"
        return response

    monkeypatch.setattr(public_views, "_serve_sw_file", _serve_modified)

    page.evaluate(
        """async () => {
            const reg = await navigator.serviceWorker.getRegistration();
            await reg.update();
          }"""
    )

    # Step 4.
    page.wait_for_selector("#sw-update-banner:not(.hidden)", timeout=5000)
