"""
tests/offline/test_update_keeps_downloaded_content.py — a service-worker
update does not wipe what an area download saved (SNOW-1311).

Every deploy changes the worker's ``CACHE_VERSION``, and ``activate`` used to
reap every older ``snowdesk-shell-*`` cache — which is where a download
writes its bulletin pages and weather sheets. A silent update the evening
before a tour left the user with a map and no conditions. ``activate`` now
carries that content into the new cache first (``_carryShellContent``), and
this is the browser-level check that it does: download, update, cut the
network, open what was downloaded.

A new ``CACHE_VERSION`` is made the way a deploy makes one — the server
starts serving a ``sw.js`` with a different name substituted into it
(``served_cache_version``, patched in the live server's process). The page
then asks for the update and posts ``SKIP_WAITING``, which is the message
``sw_register.js`` itself posts when it applies a routine update; waiting
for the tab to be hidden, its usual trigger, would add nothing but a
timing dependency.

The vitest suite (``tests/js/test_sw.js``) holds the carry's rules —
precedence, quota, the budget, complete-or-keep. This holds the one thing it
cannot: that a real Chromium, real Cache Storage and a real activation
leave the downloaded pages readable offline.

Scenario: D1, P4
"""

from __future__ import annotations

import re

import pytest

from apps.core.sw_shell import served_cache_version
from tests.offline.conftest import OfflineMapPage

# What the download writes, as paths: a region's bulletin for a day
# (``areaContentURLs`` in basemap_download_core.js) and a weather sheet dated
# for today (``weatherDetailURL``).
_BULLETIN_PATH = re.compile(r"^/[a-z]{2}-[\w-]+/[^/]+/\d{4}-\d{2}-\d{2}/$")
_SHEET_PATH = re.compile(r"^/api/weather/[^/]+/detail/$")

_SHELL_KEYS_JS = """async (name) => {
    const cache = await caches.open(name);
    return (await cache.keys()).map((request) => request.url);
}"""

# Ask for the update, apply it, and wait until the new worker has finished
# activating — its ``waitUntil``, carry included, has settled by then.
_APPLY_UPDATE_JS = """async () => {
    const registration = await navigator.serviceWorker.getRegistration();
    await registration.update();
    const next = registration.installing || registration.waiting;
    if (!next) return 'no new worker';
    const deadline = Date.now() + 60000;
    while (next.state !== 'activated' && Date.now() < deadline) {
        if (next.state === 'installed') next.postMessage({ type: 'SKIP_WAITING' });
        if (next.state === 'redundant') return 'redundant';
        await new Promise((resolve) => setTimeout(resolve, 200));
    }
    return next.state;
}"""


@pytest.mark.usefixtures("_load_offline_dataset")
def test_an_update_keeps_the_downloaded_bulletins_and_sheets(
    offline_map_page: OfflineMapPage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download, deploy a new worker, go offline: the conditions are still there."""
    page = offline_map_page.page
    subject = offline_map_page.subject
    offline_map_page.choose_basemap(subject.basemap_key)
    offline_map_page.select_region(subject.region_name)
    offline_map_page.download_selected_region()

    old = served_cache_version()
    held = [
        re.sub(r"^https?://[^/]+", "", url)
        for url in page.evaluate(_SHELL_KEYS_JS, old)
    ]
    bulletins = [path for path in held if _BULLETIN_PATH.match(path)]
    sheets = [path for path in held if _SHEET_PATH.match(path.split("?")[0])]
    assert bulletins and sheets, (
        f"The download of {subject.region_id} kept {len(bulletins)} bulletin "
        f"page(s) and {len(sheets)} weather sheet(s) in {old}; this test needs "
        f"one of each to carry. Seed {subject.seed}."
    )

    new = f"{old}-next"
    for module in (
        "apps.public.views",
        "apps.public.api",
        "apps.public.context_processors",
    ):
        monkeypatch.setattr(f"{module}.served_cache_version", lambda: new)
    state = page.evaluate(_APPLY_UPDATE_JS)
    assert state == "activated", f"The {new} worker ended {state!r}, not activated."

    names = page.evaluate("() => caches.keys()")
    assert old not in names, (
        f"{old} survived the activation of {new}: the carry did not report "
        "complete, so the old shell cache was kept. The page is still "
        "readable that way, but a carry this small should finish."
    )
    carried = page.evaluate(_SHELL_KEYS_JS, new)
    carried_paths = {re.sub(r"^https?://[^/]+", "", url) for url in carried}
    missing = [path for path in [bulletins[0], sheets[0]] if path not in carried_paths]
    assert not missing, f"{new} does not hold {missing}, which {old} did."

    offline_map_page.go_offline()
    page.goto(offline_map_page.live_server_url + bulletins[0])
    page.wait_for_load_state("load")
    # static/offline.html's own title — what a navigation the cache cannot
    # answer falls back to.
    assert page.title() != "Offline — Snowdesk", (
        f"{bulletins[0]} was downloaded before the update and fell back to "
        "offline.html after it.\n  " + offline_map_page.diagnostics()
    )
    sheet = page.evaluate(
        """async (url) => {
            const response = await fetch(url);
            return { status: response.status, cache: response.headers.get('X-SW-Cache') };
        }""",
        sheets[0],
    )
    assert sheet == {"status": 200, "cache": "hit"}, (
        f"The weather sheet {sheets[0]} was downloaded before the update and "
        f"answered {sheet} offline after it."
    )
