"""
tests/e2e/test_cache_this_area.py — Playwright regression tests for the
single-region "Download basemap" icon (SNOW-492, SNOW-493, SNOW-521 final
shape).

SNOW-521 went through two intermediate shapes before landing here: first a
viewport-anchored docked confirm bar (``#basemap-download-bar``), then a
per-breadcrumb-tier set of icons (Major/Minor/Micro). Both were dropped —
the docked bar required zooming in tight before the viewport was small
enough to accept; the per-tier icons made download size non-monotonic with
containment (an L1 region could read smaller than an L2 it contains),
which read as a bug. The shipped shape is a single icon
(``#region-download-micro`` in ``_season_ribbon.html``'s ``#region-readout``
chip) for the focused MICRO region only, with no completion toast — the
icon itself carries the outcome (a green "available offline" circle on
success, reverting to idle otherwise). This file covers: the icon's
idle→busy→done flow, a reselected region reading ``done`` from real cache
state rather than in-page memory, a probe that couldn't resolve the
active basemap's tile template re-running once the style settles, and
the disabled/over_ceiling and partial/failed-run branches.

Every test requests ``_load_test_data`` (``pytestmark`` below) — not for
its ratings/bulletin rows, but because ``_season_ribbon.html``'s
``{% if ribbon %}`` gate (and so the icon markup itself) only renders when
``build_season_ribbon()`` resolves a real, DB-backed default region;
against an unseeded DB the icon doesn't exist in the DOM at all.
``pwa_page``'s own first navigation can race that seed (fixture
instantiation order between two same-scope, non-dependent fixtures is
unspecified), so every test's first action is ``_reload_home`` — a
second ``page.goto('/')`` — mirroring ``test_pwa_lifecycle_offline.py``'s
established pattern of navigating again inside the test body once
seeding is guaranteed to have landed.

None of these tests wait for a real MapLibre style load. A real
third-party basemap CDN (openfreemap.org) is documented elsewhere as
unreachable in this harness (``test_offline_basemap_cache.py``'s module
docstring; confirmed here too — the style fetch net::ERR_ABORTEDs when a
real SW is controlling, since ``page.route()`` cannot intercept a
request the SW's OWN fetch handler issues from inside its ``fetch()``
call, the same finding ``_spike_results.py`` documents), and
``regionDownloadInit`` doesn't need the map's style to have loaded at
all — it only reads ``FEATURE_BY_REGION_ID[regionId].properties.
download``, which the real boot sequence populates from a
``regions.geojson`` fetch gated behind ``map.on('load')``. ``_select_region``
sidesteps that whole chain by writing a synthetic entry into
``FEATURE_BY_REGION_ID`` directly and dispatching the real
``snowdesk:region-selected`` event — the same synthetic-injection idiom
this suite's sibling files use for the blank-map regression — so every
test here exercises ``regionDownloadInit``'s real listener/render/click
logic deterministically, independent of the CDN.

The SW round trip and the tile-URL assembly need the same kind of full,
deterministic control:

* ``sw.js`` declares ``_warmCache`` as a top-level classic-script
  function (no enclosing IIFE), so ``worker.evaluate()`` (the same
  technique ``test_offline_basemap_cache.py`` uses to seed SW-internal
  state) can directly reassign ``self._warmCache`` to a stub that
  returns a chosen ``{ok, failed}``, optionally drives
  ``opts.onProgress``, and — for a ``pinned: true`` call with ``ok >
  0`` — actually writes the warmed URLs into the real
  ``BASEMAP_PINNED_CACHE`` (referenced directly, the same way
  ``test_offline_basemap_cache.py``'s pinned-cache test does — Playwright
  evaluates in the SW's own realm, where sw.js's top-level ``const``
  is in scope) so a later done-probe against real Cache Storage is a
  genuine test of that probe, not just the ``{ok, failed}`` branching.
* ``map.js`` is similarly a classic, non-module script, so its
  top-level ``function activeBasemapTileTemplate(...)`` declaration is
  a plain, reassignable ``window`` property — stubbed to a fixed
  template so tile-URL assembly doesn't depend on a resolved
  basemap-CDN style.
* The ``/api/region-basemap-tiles/`` fetch is a plain page-level
  ``fetch()`` (not a service-worker-internal one), so it's stubbed via
  ``page.route()`` — unlike the SW's own fetches, this one IS visible
  to Playwright's request interception.
"""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from playwright.sync_api import Page, Route, Worker as SWWorker

from tests.e2e.conftest import PwaPage

pytestmark = pytest.mark.usefixtures("_load_test_data")

# A tiny, self-consistent blob: one z14 tile, matching its own
# centre_tile exactly — so a stubbed-successful download always writes
# the one URL the done-probe (centreTileURL) will check for.
_STUB_TEMPLATE = "https://tiles.example.invalid/{z}/{x}/{y}.pbf"
_CENTRE_TILE = {"z": 14, "x": 100, "y": 100}
_STUB_BLOB: dict[str, Any] = {
    "band": [10, 14],
    "count": 1,
    "mb": 1,
    "over_ceiling": False,
    "centre_tile": _CENTRE_TILE,
    "z": {"14": [100, 100, 100, 100]},
}
_MICRO_SUMMARY: dict[str, Any] = {
    "count": 1,
    "mb": 1,
    "over_ceiling": False,
    "centre_tile": _CENTRE_TILE,
}


def _reload_home(pwa_page: PwaPage) -> None:
    """Re-navigate to ``/`` so the page reflects ``_load_test_data``'s rows.

    See the module docstring for why this second navigation is needed —
    ``pwa_page``'s own first ``goto`` races the DB seed.
    """
    page = pwa_page.page
    page.goto(pwa_page.live_server_url + "/")
    page.wait_for_load_state("load")


def _select_region(page: Page, region_id: str, download: dict[str, Any] | None) -> None:
    """Inject a synthetic ``FEATURE_BY_REGION_ID`` entry and focus it.

    See the module docstring for why this bypasses the real
    ``regions.geojson`` boot fetch entirely.

    Args:
        page: The Playwright page.
        region_id: e.g. ``"CH-4115"``.
        download: The flat ``properties.download`` summary
            (``{count, mb, over_ceiling, centre_tile}``), or ``None``
            for a region with no computed blob.

    """
    page.evaluate(
        """({ regionId, download }) => {
            FEATURE_BY_REGION_ID[regionId] = {
                type: 'Feature',
                properties: { id: regionId, regionID: regionId, download },
            };
            document.dispatchEvent(new CustomEvent('snowdesk:region-selected', {
                detail: { region_id: regionId },
            }));
        }""",
        {"regionId": region_id, "download": download},
    )


def _stub_warm_cache(
    worker: SWWorker,
    *,
    ok: int,
    failed: int,
    progress_steps: list[tuple[int, int]] | None = None,
    step_delay_ms: int = 0,
) -> None:
    """Replace ``self._warmCache`` with a stub reporting a fixed outcome.

    Records the URL list and the ``pinned`` flag on ``self.__snow521Urls``
    / ``self.__snow521Pinned``, resolves with the caller-chosen ``{ok,
    failed}`` — the real ``sw.js``/``sw_register.js`` message round trip
    still carries this back to the page exactly as a genuine
    ``_warmCache`` result would — and, for a ``pinned`` call with ``ok >
    0``, writes every URL into the real ``BASEMAP_PINNED_CACHE`` so a
    subsequent done-probe (real Cache Storage read) succeeds.

    ``progress_steps``, if given, drives ``options.onProgress(done,
    total)`` once per tuple, spaced ``step_delay_ms`` apart so a test can
    reliably observe an intermediate ``busy`` state via
    ``page.wait_for_function`` before the run resolves.
    """
    worker.evaluate(
        """({ ok, failed, progressSteps, stepDelayMs }) => {
            self._warmCache = async (urls, options) => {
                self.__snow521Urls = urls;
                self.__snow521Pinned = !!(options && options.pinned);
                const onProgress = options && options.onProgress;
                for (const [done, total] of progressSteps) {
                    if (typeof onProgress === 'function') onProgress(done, total);
                    if (stepDelayMs > 0) {
                        await new Promise((resolve) => setTimeout(resolve, stepDelayMs));
                    }
                }
                if (options && options.pinned && ok > 0) {
                    const cache = await caches.open(BASEMAP_PINNED_CACHE);
                    for (const url of urls) {
                        await cache.put(url, new Response('stub-tile'));
                    }
                }
                return { ok, failed };
            };
        }""",
        {
            "ok": ok,
            "failed": failed,
            "progressSteps": progress_steps or [],
            "stepDelayMs": step_delay_ms,
        },
    )


def _stub_active_basemap_template(page: Page, template: str = _STUB_TEMPLATE) -> None:
    """Replace ``window.activeBasemapTileTemplate`` with a fixed template.

    Real basemap style resolution depends on a reachable third-party CDN
    (documented as unreachable in this harness — see the module
    docstring). ``map.js`` is a classic (non-module) ``<script>``, so its
    top-level ``function activeBasemapTileTemplate(...)`` declaration is
    a plain, reassignable ``window`` property.
    """
    page.evaluate(
        "(template) => { window.activeBasemapTileTemplate = () => template; }",
        template,
    )


def _stub_region_basemap_tiles(page: Page, blob: dict[str, Any] = _STUB_BLOB) -> None:
    """Route ``/api/region-basemap-tiles/`` to a fixed, deterministic blob.

    A plain page-level ``fetch()`` (``regionDownloadInit``'s click
    handler in ``map.js``), unlike the SW's own internal fetches — so
    ``page.route()`` sees and can intercept it.
    """

    def _handle_route(route: Route) -> None:
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(blob)
        )

    page.route("**/api/region-basemap-tiles/**", _handle_route)


def _wait_for_map_ready(page: Page) -> None:
    page.wait_for_function(
        "() => typeof MAP !== 'undefined' && MAP !== null && "
        "typeof window.pwaWarmCache === 'function' && "
        "typeof window.pwaBasemapDownloadCore !== 'undefined'"
    )


def _wait_for_state(page: Page, state: str, timeout: int = 10000) -> None:
    page.wait_for_function(
        """(state) => {
            const btn = document.getElementById('region-download-micro');
            return !!btn && btn.dataset.downloadState === state;
        }""",
        arg=state,
        timeout=timeout,
    )


def _recorded_urls(worker: SWWorker) -> list[str]:
    return cast(list[str], worker.evaluate("() => self.__snow521Urls || []"))


def _recorded_pinned(worker: SWWorker) -> Any:
    return worker.evaluate("() => self.__snow521Pinned")


def test_micro_icon_visible_idle_with_size_for_default_region(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """A focused region with a computed download summary shows an idle icon."""
    _reload_home(pwa_page)
    page = pwa_page.page
    _wait_for_map_ready(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#region-download-micro")
    icon.wait_for(state="visible")
    assert icon.get_attribute("data-download-state") == "idle"
    assert "MB" in (icon.get_attribute("aria-label") or "")


def test_micro_icon_download_flow_busy_then_done(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """Clicking the idle icon goes busy, then done — no toast, just the icon."""
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=1, failed=0, progress_steps=[(1, 1)], step_delay_ms=200)

    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#region-download-micro")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    icon.click()
    _wait_for_state(page, "busy")
    _wait_for_state(page, "done", timeout=10000)

    # SNOW-493 finding 6 continuity: same-origin data feeds (incl. the
    # default CH country) are still assembled alongside the tile URL.
    urls = _recorded_urls(worker)
    assert any("country=ch" in url for url in urls)
    assert any(url.startswith("https://tiles.example.invalid/") for url in urls)
    assert _recorded_pinned(worker) is True


def test_reselecting_a_downloaded_region_reads_done_from_real_cache(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """A region's ``done`` state survives reselect — derived from real cache.

    Downloads CH-4115, then focuses a different region with no computed
    summary (clearing the in-page microData for CH-4115 and hiding its
    icon), then reselects CH-4115 — the icon must read ``done`` again
    purely from ``regionDownloadInit``'s real ``BASEMAP_PINNED_CACHE``
    probe, not a remembered JS flag (the "layers menu is a live
    cache-state dashboard" invariant).
    """
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=1, failed=0)

    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#region-download-micro")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")
    icon.click()
    _wait_for_state(page, "done", timeout=10000)

    # Focus a different region with no computed summary — the icon hides.
    _select_region(page, "CH-1111", None)
    icon.wait_for(state="hidden")

    # Reselect CH-4115 — done, without a second click.
    _select_region(page, "CH-4115", _MICRO_SUMMARY)
    icon.wait_for(state="visible")
    _wait_for_state(page, "done", timeout=10000)


def test_icon_disabled_when_over_ceiling(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """A summary flagged over_ceiling shows a disabled icon, unclickable."""
    _reload_home(pwa_page)
    page = pwa_page.page
    _wait_for_map_ready(page)
    _select_region(page, "CH-4115", {**_MICRO_SUMMARY, "over_ceiling": True})

    icon = page.locator("#region-download-micro")
    icon.wait_for(state="visible")
    _wait_for_state(page, "disabled")
    assert "too large" in (icon.get_attribute("aria-label") or "")


def test_icon_reverts_to_idle_when_some_urls_fail(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """``ok > 0 and failed > 0`` reverts the icon to idle, not done."""
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=3, failed=2)

    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#region-download-micro")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")
    icon.click()
    _wait_for_state(page, "idle", timeout=10000)


def test_icon_reverts_to_idle_when_ok_is_zero(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """``ok === 0`` reverts the icon to idle, never claims done."""
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=0, failed=4)

    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#region-download-micro")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")
    icon.click()
    _wait_for_state(page, "idle", timeout=10000)


def test_icon_never_claims_done_for_vacuous_run(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """``{ok: 0, failed: 0}`` never claims "available offline"."""
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=0, failed=0)

    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#region-download-micro")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")
    icon.click()
    _wait_for_state(page, "idle", timeout=10000)


def test_done_state_resolves_once_the_style_settles(
    pwa_page: PwaPage, _load_test_data: None
) -> None:
    """An unresolvable tile template re-probes instead of settling on idle.

    ``activeBasemapTileTemplate`` is gated on ``map.isStyleLoaded()``,
    which is false for the whole of the boot sequence that first paints
    this icon — the overlay sources are added inside ``map.on('load')``
    itself, so the style is still dirty when ``MAP_READY_PROMISE``
    resolves. The first done-probe of a page load therefore has no URL to
    look up, which used to paint a durable ``idle`` over an
    already-downloaded region until the user reselected it.

    Reproduced here by nulling the template stub — the harness's own
    permanent condition (no reachable basemap CDN) is the same one the
    real boot sequence passes through transiently. The icon must re-probe
    real cache state on the next MapLibre ``idle`` and flip to ``done``
    with no reselect.
    """
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _stub_warm_cache(worker, ok=1, failed=0)

    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    _stub_region_basemap_tiles(page)
    _select_region(page, "CH-4115", _MICRO_SUMMARY)

    icon = page.locator("#region-download-micro")
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")
    icon.click()
    _wait_for_state(page, "done", timeout=10000)

    # Style not settled: the template is unresolvable, so the probe can't
    # tell — the icon reads idle (actionable, carrying the region's size).
    page.evaluate("() => { window.activeBasemapTileTemplate = () => null; }")
    _select_region(page, "CH-1111", None)
    icon.wait_for(state="hidden")
    _select_region(page, "CH-4115", _MICRO_SUMMARY)
    icon.wait_for(state="visible")
    _wait_for_state(page, "idle")

    # Style settles: the queued re-probe reads the real pinned cache and
    # the icon flips to done without the region being reselected.
    _stub_active_basemap_template(page)
    page.evaluate("() => MAP.fire('idle')")
    _wait_for_state(page, "done", timeout=10000)
