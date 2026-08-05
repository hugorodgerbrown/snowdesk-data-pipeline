"""
tests/e2e/test_custom_download_area.py — Playwright regression tests for the
"Download a custom area" basemap control (SNOW-522; the way in rewritten by
SNOW-634; SNOW-635 lets more than one custom area exist).

Companion to ``test_cache_this_area.py``'s per-region control: this one has
no fixed region to size ahead of time. SNOW-634 changed how framing is
reached: clicking ``#map-custom-download-control`` now opens the downloads
sheet (``#map-downloads-sheet``), and its own ``[data-downloads-add]``
trigger is what opens the framing overlay (``#map-frame-overlay``) — a
Google-Maps-style dim mask with a fixed, centred frame the user pans/zooms
the map underneath, with a live "up to N MB" readout (computed entirely
client-side — ``static/js/basemap_download_core.js``, no network round
trip) and a docked Cancel/Download CTA bar. ``_open_framing`` below
centralises that two-click path so the rest of this file reads exactly as
it did when one click sufficed.

Reuses ``test_cache_this_area.py``'s helpers (``_reload_home``,
``_stub_warm_cache``, ``_stub_active_basemap_template``,
``_wait_for_map_ready``, ``_wait_for_state`` — the last now takes an
optional ``selector`` so both controls' ``data-download-state`` idiom can
share one waiter) rather than duplicating them, and the same rationale for
why none of these tests wait for a real MapLibre STYLE load: the real
basemap CDN is unreachable in this harness. Unlike the per-region control,
though, this one genuinely needs a working ``MAP`` transform (``unproject``,
``fitBounds``) — that is available synchronously off the ``bounds``
constructor option the moment ``new maplibregl.Map(...)`` returns, well
before the style network fetch resolves (confirmed by
``test_map_placement_focus.py`` and ``test_place_pin_clearance.py``, which
also call ``MAP.unproject``/``MAP.setCenter`` without waiting for
``MAP.loaded()``), so ``_wait_for_map_ready`` alone is enough here too.

The dim-mask assertion reads the frame rect's computed ``box-shadow``
rather than diffing a screenshot — matching this suite's established
"assert computed style, not pixels" convention (see
``test_map_placement_focus.py``'s module docstring) — a canvas/DOM
screenshot would depend on frame timing this harness deliberately avoids.

Covers: opening framing dims the map and shows the frame; the readout
tracks the frame as the map moves; a ceiling-capped frame holds its size
while the map pans beneath it (SNOW-566); and, from SNOW-567, that a
capped frame stays over the same ground while zooming, never lags the
canvas mid-gesture, stays centred however far off the pointer is, and
releases again on the way back in. A confirmed download warms with
``pinned: true`` and a freshly-minted (SNOW-635) ``areaId`` — never the
single fixed ``'custom'`` every download used to share — reaches ``done``,
and notifies the layers sync dashboard.

SNOW-635 removed the single-saved-area behaviour SNOW-586/632 built here:
opening framing no longer re-centres on any previously-downloaded area
(there is no longer one canonical area to jump to), and confirming a
SECOND download — whether the frame moved, the basemap changed, or
neither — no longer evicts the first custom area's bucket. It downloads a
genuinely SEPARATE area instead, covered below
(``test_two_confirmed_areas_are_both_available_offline`` and its
neighbours) alongside the one case that still evicts something: the
standing BYTE BUDGET, which can still make one of the user's own custom
areas the oldest thing on disk
(``test_confirming_a_second_area_over_a_full_budget_evicts_the_first``).

SNOW-568 adds the failure path, which had no coverage because it had no
behaviour: every failed run reverted the roundel to ``idle`` and closed
the overlay, indistinguishable from a Cancel. A failed run now holds the
frame up, paints ``error`` (SNOW-634: on the overlay's own
``data-run-state`` — the roundel itself settles back to ``idle``, since a
failed run records nothing), and raises a toast (lifted clear of the CTA
sheet it shares the foot of the viewport with) whose copy depends on
whether the cause was the storage quota or anything else — covered here
along with retrying and cancelling out of that state.
"""

from __future__ import annotations

import re
import time
from typing import Any, cast

import pytest
from playwright.sync_api import Page, Worker as SWWorker, expect

from tests.e2e.conftest import PwaPage
from tests.e2e.test_basemap_download_budget import _set_budget_mb
from tests.e2e.test_cache_this_area import (
    _STUB_TEMPLATE,
    _reload_home,
    _stub_active_basemap_template,
    _stub_warm_cache,
    _wait_for_map_ready,
    _wait_for_state,
)

pytestmark = pytest.mark.usefixtures("_load_test_data")

_CONTROL = "#map-custom-download-control"
_PINNED_CACHE_PREFIX = "snowdesk-basemap-pinned-"


def _boot(pwa_page: PwaPage) -> tuple[Page, SWWorker]:
    """Reload, wait for the map + SW, and stub the active basemap template.

    Every test needs this same sequence; SW round trips need a genuine
    (activated) worker, hence the assertion below rather than a silent
    empty list.
    """
    _reload_home(pwa_page)
    page = pwa_page.page
    assert page.context.service_workers, "expected a registered service worker"
    worker = page.context.service_workers[0]
    _wait_for_map_ready(page)
    _stub_active_basemap_template(page)
    return page, worker


def _open_framing(page: Page) -> None:
    """Open the downloads sheet via the roundel, then its add-trigger opens framing.

    SNOW-634: the roundel opens ``#map-downloads-sheet`` now (the same
    surface "Manage downloads…" used to reach, before that layers-menu row
    was removed) rather than jumping straight into framing — "Download a
    custom area" is an action inside the sheet
    (``[data-downloads-add]``), not the roundel's own click.
    """
    page.click(_CONTROL)
    page.wait_for_selector("#map-downloads-sheet:not([hidden])")
    page.click("[data-downloads-add]")
    page.wait_for_selector("#map-frame-overlay:not([hidden])")


def _wait_for_overlay_closed(page: Page) -> None:
    page.wait_for_selector("#map-frame-overlay[hidden]", state="attached")


def _wait_for_run_state(page: Page, state: str, timeout: int = 10000) -> None:
    """Wait for ``#map-frame-overlay``'s ``data-run-state`` to read ``state``.

    SNOW-634: replaces waiting on the roundel's own ``data-download-state``
    for a run in flight. That attribute is now derived from storage
    (``_renderControl``) and only ever reads ``idle``/``done`` — it stopped
    tracking a run at all — and the roundel itself is hidden for the whole
    time the overlay covering it is open (``.map-framing`` hides
    ``#map-controls-br``, static/css/map.css) anyway. ``data-run-state`` is
    map.js's own replacement observable, mirrored by ``paintRun`` onto the
    overlay — see that template's own comment in _map_embed.html.
    """
    page.wait_for_selector(
        f'#map-frame-overlay[data-run-state="{state}"]', timeout=timeout
    )


def _wait_for_worker_flag(
    worker: SWWorker, expression: str, *, timeout_ms: int = 5000
) -> None:
    """Poll ``worker.evaluate(expression)`` until it is truthy.

    SNOW-632 review finding: Playwright's Python ``Worker`` has no
    ``wait_for_function`` (unlike ``Page``), so a test that needs to observe
    SW-side state — here, ``_stub_warm_cache``'s ``pause_after_step``
    protocol — has no built-in way to wait for it deterministically. This is
    the substitute: retry a short ``evaluate`` a few milliseconds apart
    rather than sleep a fixed guess and hope the worker got there in time.
    """
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if worker.evaluate(expression):
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for worker condition: {expression}")


def _close_completed_overlay(page: Page) -> None:
    """Click a completed run's Close (the relabelled Cancel button) and wait for it to hide.

    SNOW-632: framing hides ``#map-controls-br`` — the roundel's own
    container — for as long as the overlay is open (``.map-framing`` in
    static/css/map.css), which now includes the whole of a completed run
    (the overlay no longer auto-closes on success). A test wanting a
    SECOND framing session therefore has to close the first one via the
    CTA itself before ``_open_framing`` can click the roundel again — that
    click is simply not there to hit otherwise.
    """
    page.click("#map-frame-cancel")
    _wait_for_overlay_closed(page)


def _wait_for_budget_banner(page: Page) -> None:
    """Wait for #map-frame-instruction to become the standing-budget banner.

    SNOW-632: map.js's ``_refreshBudgetBanner`` overwrites the server-
    rendered "Download selected area…" copy asynchronously (an IndexedDB
    round trip) once framing opens, and deliberately does not block the
    overlay's own reveal on it — so a test has to wait for the real text
    to land rather than read whatever placeholder is there the instant
    ``_open_framing`` returns.
    """
    page.wait_for_function(
        """() => {
            const el = document.getElementById('map-frame-instruction');
            return !!el && el.innerText.includes('/') && el.innerText.includes('MB');
        }"""
    )


def _instruction_text(page: Page) -> str:
    return str(page.locator("#map-frame-instruction").inner_text())


def _readout_text(page: Page) -> str:
    # str(), not cast(): Playwright's own stubs already type inner_text()
    # as str (unlike evaluate() below, which is genuinely Any in Playwright's
    # own stubs regardless of environment) — a cast is "redundant" whenever
    # those stubs are resolvable and only needed when they aren't (e.g. the
    # tox mypy env's `type` dependency group deliberately excludes
    # playwright — see pyproject.toml). str() satisfies both consistently.
    return str(page.locator("#map-frame-readout").inner_text())


def _settle_readout(page: Page) -> None:
    """Wait for the frame's size and readout to catch up with the map.

    The ``move`` handler runs synchronously now (SNOW-567 — deferring it a
    frame is what left the frame lagging the canvas), so this is really
    waiting for the map's own animation to reach the frame it is on. Two
    nested rAFs cover that: the first shares the frame MapLibre is
    rendering, the second is strictly later.
    """
    page.evaluate(
        "() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))"
    )


_SYNC_SENTINEL = "__snow522_not_refreshed__"


def _arm_sync_dashboard_probe(page: Page) -> None:
    """Stamp a sentinel on every sync dot so a later ``refresh()`` is observable.

    ``window.pwaLayerSyncStatus`` cannot be spied on: it is installed with
    ``Object.defineProperty(window, …, {writable: false, configurable:
    false})`` over an ``Object.freeze``d value
    (``static/js/map_layer_sync_status.js``), so assigning a stub over it
    silently no-ops in sloppy mode and the assertion can never pass.

    ``refresh()`` rewrites ``data-sync-state`` on every dot it probes, so
    stamping a sentinel first and asserting it is gone afterwards observes
    the real call rather than a stub of it.
    """
    page.evaluate(
        """(sentinel) => {
            document
                .querySelectorAll('#basemap-menu .sync-dot')
                .forEach((dot) => { dot.dataset.syncState = sentinel; });
        }""",
        _SYNC_SENTINEL,
    )


def _assert_sync_dashboard_refreshed(page: Page) -> None:
    """Wait for ``refresh()`` to clear the sentinel stamped by the arm step."""
    page.wait_for_function(
        """(sentinel) => {
            const dots = document.querySelectorAll('#basemap-menu .sync-dot');
            return (
                dots.length > 0 &&
                [...dots].some((dot) => dot.dataset.syncState !== sentinel)
            );
        }""",
        arg=_SYNC_SENTINEL,
        timeout=10000,
    )


def _frame_a_downloadable_area(page: Page) -> None:
    """Zoom in until the framed area is under the download ceiling.

    The homepage's default view frames most of Switzerland, which across
    the z10–z14 band is comfortably over ``DOWNLOAD_CEILING_MB`` (200) —
    so framing mode legitimately opens with ``#map-frame-confirm``
    disabled and the readout reading "Area too large to download". Every
    confirm-path test therefore has to zoom in first; without this the
    click just times out against a disabled button.

    Steps until the button enables rather than jumping to a hardcoded
    zoom, so this stays correct if the frame's CSS size, the default
    viewport, or the ceiling constant changes.
    """
    confirm = page.locator("#map-frame-confirm")
    for _ in range(10):
        if not confirm.is_disabled():
            return
        page.evaluate("() => MAP.setZoom(Math.min(MAP.getZoom() + 1, 16))")
        _settle_readout(page)
    raise AssertionError(
        "the framed area never dropped under the download ceiling — "
        f"readout still reads {_readout_text(page)!r}"
    )


def _move_the_frame(page: Page) -> None:
    """Move the frame onto ground that does not overlap the previous area.

    Deliberately a PAN, not a zoom. Zooming in leaves the new framed area
    a strict *subset* of the old one, so the old area's tiles — including
    its centre tile — are legitimately members of the new set too: the
    evict-then-warm sequence deletes them and immediately re-adds them,
    and an eviction assertion keyed on the old centre tile reads as a
    failure when the behaviour is in fact correct. Panning clear of the
    old footprint keeps the two tile sets disjoint, so eviction is
    observable.

    Panning by the frame's own width guarantees no overlap; the map's
    ``maxBounds`` (roughly the Alps, static/js/map.js) comfortably
    accommodates a frame-width step from the default view.
    """
    page.evaluate(
        """() => {
            const rect = document.getElementById('map-frame-rect').getBoundingClientRect();
            const centre = MAP.project(MAP.getCenter());
            // A full frame-width step east, in map-container pixels.
            MAP.setCenter(MAP.unproject([centre.x + rect.width, centre.y]));
        }"""
    )
    _settle_readout(page)


def _ground_under_frame(page: Page) -> tuple[float, float]:
    """The (lng, lat) the centre of the framing rectangle sits over."""
    point = page.evaluate(
        """() => {
            const a = document.getElementById('map-frame-area').getBoundingClientRect();
            const m = MAP.getContainer().getBoundingClientRect();
            const p = MAP.unproject([
                a.left + a.width / 2 - m.left,
                a.top + a.height / 2 - m.top,
            ]);
            return [p.lng, p.lat];
        }"""
    )
    return (float(point[0]), float(point[1]))


def _frame_width(page: Page) -> float:
    """The frame's on-screen width in CSS pixels."""
    return float(
        page.evaluate(
            "() => document.getElementById('map-frame-rect').getBoundingClientRect().width"
        )
    )


def _zoom_out_until_capped(page: Page) -> None:
    """Zoom out until the download ceiling caps the frame's size.

    The cap is engaged exactly when map.js has written an inline width onto
    the frame (below the ceiling it clears both dimensions and lets the
    stylesheet size the box), so that — rather than a hardcoded zoom — is
    what this waits for.
    """
    for _ in range(8):
        if page.evaluate("() => document.getElementById('map-frame-rect').style.width"):
            return
        page.evaluate("() => MAP.setZoom(Math.max(MAP.getZoom() - 1, 4))")
        _settle_readout(page)
    raise AssertionError("the download ceiling never capped the frame's size")


def _confirm_download(
    page: Page,
    worker: SWWorker,
    *,
    ok: int = 1,
    failed: int = 0,
    reason: str | None = None,
) -> None:
    """Stub a warm-cache outcome and click Download, waiting for it to settle.

    SNOW-568: a run that doesn't cleanly succeed settles on ``error``
    (with a toast and the framing overlay still open), not the ``idle``
    it used to revert to.

    SNOW-634: a run in flight is now observed on the overlay's own
    ``data-run-state`` (``_wait_for_run_state``), not the roundel — see
    that helper's own docstring. A SUCCESSFUL run still lands on the
    roundel too (``_renderControl`` re-derives ``done`` from storage once
    the run settles); a FAILED one does not — nothing was recorded, so the
    roundel reads ``idle``, not an ``error`` state it no longer has. This
    only waits for the roundel on success; callers asserting a failure's
    own reporting check the CTA/toast directly (SNOW-568/632), not the
    roundel.
    """
    _stub_warm_cache(worker, ok=ok, failed=failed, reason=reason)
    page.click("#map-frame-confirm")
    _wait_for_run_state(page, "busy")
    if ok > 0 and failed == 0:
        _wait_for_run_state(page, "done", timeout=10000)
        _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)
    else:
        _wait_for_run_state(page, "error", timeout=10000)


def _display(page: Page, selector: str) -> str:
    """The computed ``display`` of `selector`, or ``"absent"`` if not in the DOM."""
    return cast(
        str,
        page.evaluate(
            """(selector) => {
                const el = document.querySelector(selector);
                return el ? getComputedStyle(el).display : 'absent';
            }""",
            selector,
        ),
    )


def _custom_areas(page: Page) -> list[dict[str, Any]]:
    """The persisted ``basemap.customAreas`` array.

    SNOW-635 replaced the single ``basemap.customArea`` row this used to
    read.
    """
    row = page.evaluate("() => window.pwaDb.get('meta:app', 'basemap.customAreas')")
    value = row["value"] if row else None
    return cast("list[dict[str, Any]]", value or [])


def _last_custom_area(page: Page) -> dict[str, Any] | None:
    """The most recently confirmed custom area (highest ``ordinal``), or None."""
    areas = _custom_areas(page)
    if not areas:
        return None
    return max(areas, key=lambda a: a["ordinal"])


def _centre_tile_url(page: Page, centre_tile: dict[str, Any]) -> str:
    """The done-probe URL for `centre_tile`, under the stubbed template.

    SNOW-615: substituted here rather than through
    ``pwaBasemapDownloadCore.centreTileURL``. That function had no
    production caller — ``_probeDone`` reads the stored record's
    ``centre_tile`` directly instead of re-deriving a URL — so this test
    was the only thing keeping it alive, and a three-token substitution is
    not worth a shipped export.
    """
    del page  # No longer needs the browser; kept for call-site symmetry.
    return (
        _STUB_TEMPLATE.replace("{z}", str(centre_tile["z"]))
        .replace("{x}", str(centre_tile["x"]))
        .replace("{y}", str(centre_tile["y"]))
    )


def _pinned_cache_has(page: Page, url: str) -> bool:
    """Whether `url` is present in ANY prefix-matched pinned bucket.

    SNOW-586 gave every downloaded area its own bucket. Through SNOW-634
    this file only ever downloaded ONE custom area at a time, so checking
    the first prefix match was equivalent to checking the only one that
    existed. SNOW-635 lets this file hold several at once — checking only
    the first (``caches.keys()``'s own, unspecified, order) made a real
    second area's tiles invisible to this helper whenever its bucket
    happened to sort after another's. Unions across every matching bucket
    instead, mirroring how production itself reads pinned tiles
    (``pinnedBasemapCacheURLs`` in static/js/map.js).
    """
    return bool(
        page.evaluate(
            """async ({ url, prefix }) => {
                const names = (await caches.keys()).filter((n) => n.startsWith(prefix));
                for (const name of names) {
                    const cache = await caches.open(name);
                    if (await cache.match(url)) return true;
                }
                return false;
            }""",
            {"url": url, "prefix": _PINNED_CACHE_PREFIX},
        )
    )


def test_opening_framing_dims_the_map_and_shows_the_frame(pwa_page: PwaPage) -> None:
    """Clicking the roundel reveals the dim mask, the frame, and a size readout."""
    page, _worker = _boot(pwa_page)
    overlay = page.locator("#map-frame-overlay")
    assert overlay.get_attribute("hidden") is not None

    _open_framing(page)

    assert overlay.get_attribute("hidden") is None
    frame_box = page.locator("#map-frame-rect").bounding_box()
    assert frame_box is not None
    assert frame_box["width"] > 0
    assert frame_box["height"] > 0
    box_shadow = page.evaluate(
        "() => getComputedStyle(document.getElementById('map-frame-rect')).boxShadow"
    )
    assert "9999px" in box_shadow, "the dim mask is a 9999px box-shadow spread"
    assert "MB" in _readout_text(page)

    # SNOW-632: the instruction bar becomes the standing download-budget
    # banner while framing is open — "0 MB / 500 MB downloaded" against
    # the default budget, since a fresh session has nothing downloaded
    # yet. It precedes the frame in DOM order, so it needs its own
    # stacking context to escape the frame's box-shadow mask — assert it
    # is lifted, since a silently-dimmed banner still "renders" and would
    # pass a bare visibility check.
    _wait_for_budget_banner(page)
    instruction = page.locator("#map-frame-instruction")
    assert instruction.is_visible()
    assert "500 MB" in _instruction_text(page)
    assert (
        page.evaluate(
            "() => getComputedStyle(document.getElementById('map-frame-instruction')).zIndex"
        )
        != "auto"
    )


def test_the_capped_frame_holds_its_size_while_the_map_pans(
    pwa_page: PwaPage,
) -> None:
    """Panning must not resize a ceiling-capped frame (SNOW-566).

    The frame shrinks once the framed ground area would exceed the download
    ceiling. That size was originally found by shrinking until
    ``buildBlob`` came in under the ceiling — but ``buildBlob``'s tile
    indices are floored, so its count steps by a whole row or column as a
    box crosses the tile grid, and the size it yielded moved with the
    frame's ALIGNMENT to that grid rather than with its footprint. Panning
    a few pixels at a time made the frame visibly shimmer.

    Eight small pans, one width. Guarded by the cap actually being engaged
    first, so it cannot pass vacuously against an uncapped frame that the
    stylesheet is holding at a fixed size anyway.
    """
    page, _worker = _boot(pwa_page)
    _open_framing(page)
    _zoom_out_until_capped(page)

    widths = {_frame_width(page)}
    for _ in range(8):
        page.evaluate(
            """() => {
                const centre = MAP.project(MAP.getCenter());
                MAP.setCenter(MAP.unproject([centre.x + 13, centre.y + 7]));
            }"""
        )
        _settle_readout(page)
        widths.add(_frame_width(page))

    assert len(widths) == 1, f"the frame resized while panning: {sorted(widths)}"


def test_zooming_a_locked_frame_leaves_it_on_the_same_ground(
    pwa_page: PwaPage,
) -> None:
    """A ceiling-capped selection is locked to the ground, so zoom cannot move it.

    SNOW-566 stopped the frame resizing as the map *pans*. Zoom was still
    moving it, for a reason no amount of smoothing fixes: a
    viewport-anchored frame holding a fixed maximum ground area has a
    screen size proportional to 2**zoom, and MapLibre's wheel zoom is
    anchored at the cursor rather than the viewport centre, so re-deriving
    the box from the viewport each frame also walked the selection across
    the terrain.

    SNOW-567 locks the bbox instead. The assertion is therefore not "the
    frame did not move" — it must move, in step with the map — but "it is
    still over the same ground": unproject the frame before the zoom, and
    afterwards the frame must sit exactly where the map now projects that
    same bbox to.
    """
    page, _worker = _boot(pwa_page)
    _open_framing(page)
    _zoom_out_until_capped(page)

    # The ground the frame covers right now, in map coordinates.
    locked_bbox = page.evaluate(
        """() => {
            const r = document.getElementById('map-frame-rect').getBoundingClientRect();
            const m = MAP.getContainer().getBoundingClientRect();
            const nw = MAP.unproject([r.left - m.left, r.top - m.top]);
            const se = MAP.unproject([r.right - m.left, r.bottom - m.top]);
            return [nw.lng, se.lat, se.lng, nw.lat];
        }"""
    )
    readout_before = _readout_text(page)

    # Kept inside the range where the map is not clamped by its own
    # maxBounds. Past roughly z5.9 the visible longitude span exceeds those
    # bounds, the map slides east or west to stay inside them, and the
    # ground under the frame genuinely changes — at which point the
    # selection follows the frame by design and "same ground" is the wrong
    # assertion. That case has its own test.
    for delta in (-0.3, -0.5, 0.4):
        # Centre-anchored, which is what framing makes every zoom: the map's
        # centre is the frame's centre for the duration
        # (_anchorZoomOnTheFrame), so this is the gesture the user actually
        # produces, pointer position notwithstanding.
        page.evaluate(
            "(d) => MAP.easeTo({zoom: MAP.getZoom() + d, duration: 0})", delta
        )
        _settle_readout(page)
        drift = page.evaluate(
            """(bbox) => {
                const [west, south, east, north] = bbox;
                const m = MAP.getContainer().getBoundingClientRect();
                const nw = MAP.project([west, north]);
                const se = MAP.project([east, south]);
                const r = document.getElementById('map-frame-rect').getBoundingClientRect();
                return {
                    left: Math.abs((r.left - m.left) - nw.x),
                    top: Math.abs((r.top - m.top) - nw.y),
                    right: Math.abs((r.right - m.left) - se.x),
                    bottom: Math.abs((r.bottom - m.top) - se.y),
                };
            }""",
            locked_bbox,
        )
        # Two pixels of slack for the whole-pixel rounding the frame's
        # width/height and offset are written at, plus its 2px border.
        assert max(drift.values()) <= 3, (
            f"the locked selection drifted at zoom delta {delta}: {drift}"
        )

    # A fixed bbox covers a fixed set of tiles, so the estimate must not
    # have so much as flickered — this is the readout the user watches
    # while zooming.
    assert _readout_text(page) == readout_before


def test_a_real_wheel_zoom_never_moves_the_estimate(pwa_page: PwaPage) -> None:
    """The same guarantee, driven by genuine wheel input rather than the API.

    The companion test above drives the zoom with ``MAP.easeTo``, which
    gives precise control over the anchor but is a single synthetic step.
    A real wheel gesture is not that: MapLibre runs it as an inertial ease
    whose tail has frames where the zoom value has plateaued while the
    centre is still settling — and a pan/zoom check that watched only the
    zoom read those frames as a pan and re-aimed the selection. That
    shipped once (the estimate visibly stepping 189 → 182 → 186 MB while
    zooming) precisely because every test drove the map through its API
    instead of its input handlers.

    Both halves of the guarantee are asserted. The readout must not so much
    as flicker — a locked bbox covers a fixed set of tiles, and the number
    is what the user watches — and the frame must still be over the same
    ground at the end. The readout alone is too weak a guard: its value only
    moves when the tile count crosses a grid boundary, so a viewport-derived
    frame can drift a long way while the estimate happens to hold.
    """
    page, _worker = _boot(pwa_page)
    _open_framing(page)
    _zoom_out_until_capped(page)

    locked_bbox = page.evaluate(
        """() => {
            const r = document.getElementById('map-frame-rect').getBoundingClientRect();
            const m = MAP.getContainer().getBoundingClientRect();
            const nw = MAP.unproject([r.left - m.left, r.top - m.top]);
            const se = MAP.unproject([r.right - m.left, r.bottom - m.top]);
            return [nw.lng, se.lat, se.lng, nw.lat];
        }"""
    )
    box = page.locator("#map").bounding_box()
    assert box is not None
    # Well off the frame's centre, as a hand on a trackpad would be — a
    # cursor-anchored zoom is what moves the map centre, and a
    # centre-anchored one cannot reproduce the defect at all.
    page.mouse.move(box["x"] + box["width"] * 0.3, box["y"] + box["height"] * 0.65)

    ground_before = _ground_under_frame(page)
    zoom_before = float(page.evaluate("() => MAP.getZoom()"))
    seen = {_readout_text(page)}
    # Four steps, not more: past roughly z5.9 the map clamps against its own
    # maxBounds, which genuinely puts different ground under the frame — the
    # selection then follows the frame, as it should, and the estimate moves
    # with it. That case is covered by
    # test_the_frame_stays_centred_however_far_off_the_pointer_is; this test
    # is about the ordinary range where the ground does not move.
    for _ in range(4):
        page.mouse.wheel(0, 120)
        page.wait_for_timeout(120)
        _settle_readout(page)
        seen.add(_readout_text(page))
    # Let the inertial tail land: the frames after the zoom value settles
    # are exactly the ones that used to be misread as a pan.
    page.wait_for_timeout(600)
    _settle_readout(page)
    seen.add(_readout_text(page))
    zoom_after = float(page.evaluate("() => MAP.getZoom()"))
    ground_after = _ground_under_frame(page)

    assert abs(zoom_after - zoom_before) > 0.2, (
        f"the wheel never zoomed the map ({zoom_before} → {zoom_after}), "
        "so this test would pass vacuously"
    )
    # State the premise rather than assuming it: a constant estimate only
    # means anything while the frame is over the same ground.
    assert max(abs(a - b) for a, b in zip(ground_before, ground_after)) < 1e-6, (
        f"the ground under the frame moved ({ground_before} → {ground_after}), "
        "so a constant estimate would prove nothing"
    )
    assert len(seen) == 1, f"the estimate moved during a wheel zoom: {sorted(seen)}"

    drift = page.evaluate(
        """(bbox) => {
            const [west, south, east, north] = bbox;
            const m = MAP.getContainer().getBoundingClientRect();
            const nw = MAP.project([west, north]);
            const se = MAP.project([east, south]);
            const r = document.getElementById('map-frame-rect').getBoundingClientRect();
            return {
                left: Math.abs((r.left - m.left) - nw.x),
                top: Math.abs((r.top - m.top) - nw.y),
                right: Math.abs((r.right - m.left) - se.x),
                bottom: Math.abs((r.bottom - m.top) - se.y),
            };
        }""",
        locked_bbox,
    )
    assert max(drift.values()) <= 3, (
        f"the locked selection drifted across the wheel gesture: {drift}"
    )


def test_the_frame_never_lags_the_canvas_mid_zoom(pwa_page: PwaPage) -> None:
    """The frame must track the map on every frame, not just when it settles.

    Every other test here samples after the map has come to rest, and a
    settled frame was correct all along — the judder was purely transient.
    The DOM rect was updated from a ``requestAnimationFrame`` scheduled off
    MapLibre's ``move`` event, so it landed one frame after the canvas it
    was chasing: measured at up to 12px of positional lag and 15px of size
    lag mid-gesture, against 0.2px once stopped. A whole class of "it still
    judders" survives a suite that only ever looks at the end state.

    So this samples on MapLibre's own ``render`` event, every frame of a
    900ms eased zoom, and compares where the frame IS against where the map
    says its locked bbox should be. The tolerance is a pixel: the frame is
    positioned in sub-pixel CSS precisely so it cannot snap against a
    canvas that moves in sub-pixel steps.
    """
    page, _worker = _boot(pwa_page)
    _open_framing(page)
    _zoom_out_until_capped(page)
    _settle_readout(page)

    result = page.evaluate(
        """async () => {
            const el = document.getElementById('map-frame-rect');
            const m0 = MAP.getContainer().getBoundingClientRect();
            const r0 = el.getBoundingClientRect();
            const nw0 = MAP.unproject([r0.left - m0.left, r0.top - m0.top]);
            const se0 = MAP.unproject([r0.right - m0.left, r0.bottom - m0.top]);
            const bbox = [nw0.lng, se0.lat, se0.lng, nw0.lat];

            const samples = [];
            const sample = () => {
                const m = MAP.getContainer().getBoundingClientRect();
                const r = el.getBoundingClientRect();
                const nw = MAP.project([bbox[0], bbox[3]]);
                const se = MAP.project([bbox[2], bbox[1]]);
                samples.push({
                    dx: (r.left - m.left) - nw.x,
                    dw: r.width - (se.x - nw.x),
                });
            };
            MAP.on('render', sample);
            // One level, not two: two reaches the zoom at which the map
            // clamps against its own maxBounds, which slides the ground
            // under the frame and re-aims the selection — real behaviour,
            // but it would show up here as "lag" it is not.
            MAP.easeTo({ zoom: MAP.getZoom() - 1, duration: 900 });
            await new Promise((r) => MAP.once('moveend', r));
            await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
            MAP.off('render', sample);

            // Drop the first and last: the first samples the frame before
            // the ease has moved anything, the last can land after the
            // final render but before the settle rAF.
            const mid = samples.slice(1, -1);
            const maxAbs = (k) => Math.max(...mid.map((s) => Math.abs(s[k])));
            return {
                frames: mid.length,
                offsetLag: maxAbs('dx'),
                sizeLag: maxAbs('dw'),
            };
        }"""
    )

    # Low bar on purpose: this only has to prove the ease actually animated
    # rather than jumping. A loaded CI runner renders the same 900ms ease in
    # far fewer frames than a desktop does — 14 of them once, against a
    # threshold of 20 tuned on a laptop, which failed the build for no
    # defect at all.
    assert result["frames"] >= 6, (
        f"only {result['frames']} frames sampled — the eased zoom did not "
        "animate, so this test would pass vacuously"
    )
    assert result["offsetLag"] <= 1, f"the frame lagged the canvas: {result}"
    assert result["sizeLag"] <= 1, f"the frame's size lagged the canvas: {result}"


def test_the_frame_stays_centred_however_far_off_the_pointer_is(
    pwa_page: PwaPage,
) -> None:
    """Framing pivots every zoom on the frame, not on the mouse pointer.

    MapLibre anchors a wheel zoom at the cursor. With a ground-locked
    selection that reads as a defect: zoom out with the pointer off to one
    side and the frame, still correctly glued to its terrain, sails towards
    that corner and then has to travel back on the way in. Framing
    therefore re-anchors zoom to the frame's own centre for its duration
    (``setPadding`` plus ``around: 'center'``).

    The pointer is parked in a corner precisely so a pointer-anchored zoom
    would fail this loudly.
    """
    page, _worker = _boot(pwa_page)
    _open_framing(page)
    _zoom_out_until_capped(page)
    _settle_readout(page)

    area = page.locator("#map-frame-area").bounding_box()
    box = page.locator("#map").bounding_box()
    assert area is not None and box is not None
    page.mouse.move(box["x"] + box["width"] * 0.12, box["y"] + box["height"] * 0.85)

    offsets = []
    for _ in range(6):
        page.mouse.wheel(0, 120)
        page.wait_for_timeout(120)
        _settle_readout(page)
        frame = page.locator("#map-frame-rect").bounding_box()
        assert frame is not None
        offsets.append(
            (
                abs(
                    (frame["x"] + frame["width"] / 2) - (area["x"] + area["width"] / 2)
                ),
                abs(
                    (frame["y"] + frame["height"] / 2)
                    - (area["y"] + area["height"] / 2)
                ),
            )
        )

    worst = max(max(pair) for pair in offsets)
    assert worst <= 2, (
        f"the frame drifted {worst:.1f}px off the centre of its area while "
        f"zooming with the pointer in a corner: {offsets}"
    )


def test_zooming_back_in_releases_the_ground_lock(pwa_page: PwaPage) -> None:
    """Zooming in until the natural frame fits hands sizing back to the stylesheet.

    Lock and release share one threshold — the point at which the
    gutter-inset frame's own footprint equals the ceiling — so the frame
    grows continuously back to filling its area rather than jumping. The
    observable is the inline geometry map.js writes only while capped.
    """
    page, _worker = _boot(pwa_page)
    _open_framing(page)
    _zoom_out_until_capped(page)

    for _ in range(8):
        page.evaluate("() => MAP.setZoom(Math.min(MAP.getZoom() + 1, 16))")
        _settle_readout(page)
        if not page.evaluate(
            "() => document.getElementById('map-frame-rect').style.width"
        ):
            break
    else:
        raise AssertionError("the ground lock never released on zooming back in")

    # Released means all three inline properties are gone, not just width —
    # a stale transform would leave the frame offset from its own area.
    assert (
        page.evaluate(
            """() => {
                const s = document.getElementById('map-frame-rect').style;
                return [s.width, s.height, s.transform].join('|');
            }"""
        )
        == "||"
    )


def test_framing_strips_the_map_furniture_and_cancel_restores_it(
    pwa_page: PwaPage,
) -> None:
    """Framing hides every control/ribbon/legend; Cancel puts them all back.

    Framing is a modal act with two answers, both on the CTA sheet, so the
    controls that steer the map for other purposes are hidden — otherwise
    they sit lit inside the cutout and read as part of the area being
    chosen. Asserted on computed style rather than the body class so this
    fails if the class stops driving the CSS, not just if it stops being
    set.
    """
    page, _worker = _boot(pwa_page)
    furniture = [
        "#season-ribbon",
        "#map-date-ribbon",
        "#map-utility-cluster",
        "#map-controls-br",
        "#map-legend",
    ]
    before = {sel: _display(page, sel) for sel in furniture}
    assert all(value != "none" for value in before.values()), before

    _open_framing(page)

    assert all(_display(page, sel) == "none" for sel in furniture), {
        sel: _display(page, sel) for sel in furniture
    }

    page.click("#map-frame-cancel")
    _wait_for_overlay_closed(page)

    assert {sel: _display(page, sel) for sel in furniture} == before


def test_readout_tracks_the_frame_as_the_map_moves(pwa_page: PwaPage) -> None:
    """The "up to N MB" readout updates live as the framed area changes."""
    page, _worker = _boot(pwa_page)
    _open_framing(page)
    before = _readout_text(page)

    page.evaluate("() => MAP.setZoom(Math.min(MAP.getZoom() + 3, 17))")
    page.wait_for_function(
        """(before) => document.getElementById('map-frame-readout').innerText !== before""",
        arg=before,
    )

    after = _readout_text(page)
    assert after != before
    assert "MB" in after


def test_frame_shrinks_to_hold_the_area_at_the_download_ceiling(
    pwa_page: PwaPage,
) -> None:
    """Zooming out past the ceiling shrinks the frame instead of going red.

    Ground area per pixel quadruples with every zoom level out, so a frame
    pinned at the maximum downloadable area must halve in size per level.
    Asserted as a ratio rather than fixed pixel sizes so the test does not
    encode the viewport or the CSS gutter — and with generous tolerance,
    since tile quantisation makes the true ceiling a step function.
    """
    page, _worker = _boot(pwa_page)
    _open_framing(page)

    def frame_width() -> float:
        box = page.locator("#map-frame-rect").bounding_box()
        assert box is not None
        # float(), not cast(): bounding_box()'s dict values resolve to Any,
        # and the same playwright-stub asymmetry that _readout_text
        # documents applies here too.
        return float(box["width"])

    def zoom_to(level: float) -> None:
        page.evaluate("(z) => MAP.setZoom(z)", level)
        page.wait_for_function(
            "() => document.getElementById('map-frame-readout').innerText.length > 0"
        )

    # Well inside the ceiling: the frame fills its area and carries no
    # inline size at all — sizing is the stylesheet's job until it bites.
    zoom_to(12)
    natural = frame_width()
    assert (
        page.evaluate("() => document.getElementById('map-frame-rect').style.width")
        == ""
    )

    # Two levels out from wherever the cap starts biting, the frame must be
    # about half the width it had one level in, and the readout must stay
    # under the ceiling rather than flipping to the too-large state.
    zoom_to(8)
    wide = frame_width()
    zoom_to(7)
    narrow = frame_width()

    assert wide < natural, "the cap should have shrunk the frame below its area"
    assert 0.35 < narrow / wide < 0.7, (
        f"expected roughly a halving per zoom level, got {narrow}/{wide}"
    )
    assert "too large" not in _readout_text(page).lower()
    assert page.locator("#map-frame-confirm").is_enabled()

    # Zooming back in releases the cap and hands sizing back to the CSS.
    zoom_to(12)
    assert (
        page.evaluate("() => document.getElementById('map-frame-rect').style.width")
        == ""
    )
    assert frame_width() == natural


def test_download_warms_pinned_and_notifies_the_sync_dashboard(
    pwa_page: PwaPage,
) -> None:
    """Confirming warms with pinned:true and reaches done.

    Also covers the "notify the layers sync dashboard" invariant —
    ``window.pwaLayerSyncStatus?.refresh()`` must run after every completed
    download, region or custom-area alike.

    SNOW-632: a completed download no longer closes the overlay — the CTA
    repaints in place instead ("X MB downloaded", Download hidden, Cancel
    relabelled Close). That is asserted here too; the dedicated completion
    tests below cover the shape of it in more depth.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _arm_sync_dashboard_probe(page)

    _confirm_download(page, worker)

    assert page.locator("#map-frame-overlay").is_visible()
    assert "downloaded" in _readout_text(page).lower()
    assert page.locator("#map-frame-confirm").is_hidden()
    urls = cast("list[str]", worker.evaluate("() => self.__snow521Urls || []"))
    assert any(url.startswith(_STUB_TEMPLATE.split("{")[0]) for url in urls)
    assert any("country=ch" in url for url in urls)
    assert worker.evaluate("() => self.__snow521Pinned") is True
    _assert_sync_dashboard_refreshed(page)

    area = _last_custom_area(page)
    assert area is not None
    assert "bbox" in area and "band" in area and "centre_tile" in area
    assert area["ordinal"] == 1
    # SNOW-635: unrenamed, so no name was ever written for it.
    assert "name" not in area


def test_reload_and_reopening_framing_does_not_recentre_on_a_prior_area(
    pwa_page: PwaPage,
) -> None:
    """A completed download survives reload; re-opening framing does not move the map.

    SNOW-635: dropped the old "reopen at the saved area" convenience —
    with any number of custom areas possibly on disk, jumping to one of
    them on open would be arbitrary. Framing now always starts from
    wherever the map already is, exactly like a first-ever open.

    SNOW-632: no longer waits for the overlay to close after confirming —
    it stays open showing the completion state now. Irrelevant here either
    way: the very next step is a full page reload (`_boot`), which wipes
    all client-side state regardless of what the overlay was doing.

    SNOW-634: the roundel's ``done`` no longer depends on the active
    basemap's tile template (there is nothing left to re-probe on
    ``snowdesk:basemap-changed`` — see ``_renderControl``), so unlike the
    per-region control this needs no forced re-probe after ``_boot`` stubs
    the template; ``basemapDownloadedAreas()`` answers straight from
    IndexedDB on boot.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker)
    area = _last_custom_area(page)
    assert area is not None

    page, worker = _boot(pwa_page)
    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)

    centre_before = page.evaluate(
        "() => { const c = MAP.getCenter(); return [c.lng, c.lat]; }"
    )
    _open_framing(page)
    centre_after = page.evaluate(
        "() => { const c = MAP.getCenter(); return [c.lng, c.lat]; }"
    )

    # The map's centre is exactly what it was before framing opened — not
    # merely "near" the downloaded area's bbox, which a small default view
    # could coincidentally satisfy.
    assert centre_after == pytest.approx(centre_before)


def test_move_then_cancel_leaves_the_only_area_intact(pwa_page: PwaPage) -> None:
    """Moving the frame and Cancelling touches neither the recorded area nor the cache.

    SNOW-632: the setup download no longer closes the overlay on its own —
    ``_close_completed_overlay`` below closes THAT session explicitly
    (via the CTA's own Close) before the second ``_open_framing`` can
    click the roundel, which framing hides for as long as its own
    overlay stays open.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker)
    area_before = _last_custom_area(page)
    assert area_before is not None
    url_before = _centre_tile_url(page, area_before["centre_tile"])
    assert _pinned_cache_has(page, url_before)

    _close_completed_overlay(page)
    _open_framing(page)
    _move_the_frame(page)
    page.click("#map-frame-cancel")
    _wait_for_overlay_closed(page)

    assert _custom_areas(page) == [area_before]
    assert _pinned_cache_has(page, url_before)


def test_two_confirmed_areas_are_both_available_offline(pwa_page: PwaPage) -> None:
    """SNOW-635: confirming a MOVED frame downloads a SECOND, independent area.

    Through SNOW-632 this evicted the first area's pinned tiles before
    warming the new set — there was only ever one custom area, keyed on
    one shared bucket id. That eviction is gone: every confirmed download
    now mints its own id (``generateCustomAreaId``), so a second download
    is a second AREA, not a replacement, and both remain fully cached.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker)
    area_before = _last_custom_area(page)
    assert area_before is not None
    url_before = _centre_tile_url(page, area_before["centre_tile"])
    assert _pinned_cache_has(page, url_before)

    _close_completed_overlay(page)
    _open_framing(page)
    _move_the_frame(page)
    _confirm_download(page, worker)

    areas = _custom_areas(page)
    assert len(areas) == 2
    area_after = _last_custom_area(page)
    assert area_after is not None
    assert area_after["id"] != area_before["id"]
    assert area_after["ordinal"] == area_before["ordinal"] + 1
    url_after = _centre_tile_url(page, area_after["centre_tile"])

    # BOTH areas' tiles remain — nothing was evicted by this confirm.
    assert _pinned_cache_has(page, url_before)
    assert _pinned_cache_has(page, url_after)


_TEMPLATE_B = "https://tiles-b.example.invalid/{z}/{x}/{y}.pbf"


def test_switching_basemap_then_confirming_creates_a_second_area(
    pwa_page: PwaPage,
) -> None:
    """SNOW-635: confirming at the SAME bbox under a DIFFERENT basemap also adds, not replaces.

    ``test_two_confirmed_areas_are_both_available_offline`` above covers a
    bbox change; this covers a basemap change with the frame left
    untouched — through SNOW-632 that used to evict the old basemap's
    tiles from the ONE shared bucket. There is no shared bucket left to
    protect: this confirm downloads its own fresh area under the new
    basemap, and the first area (still holding the OLD basemap's tiles)
    is untouched.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker)
    area_before = _last_custom_area(page)
    assert area_before is not None
    assert area_before["template"] == _STUB_TEMPLATE
    url_before = _centre_tile_url(page, area_before["centre_tile"])
    assert _pinned_cache_has(page, url_before)

    # Close this session, switch the ACTIVE basemap, and re-open framing
    # without moving the map — the frame lands back on the SAME ground.
    _close_completed_overlay(page)
    _stub_active_basemap_template(page, template=_TEMPLATE_B)
    _open_framing(page)
    _confirm_download(page, worker)

    areas = _custom_areas(page)
    assert len(areas) == 2
    area_after = _last_custom_area(page)
    assert area_after is not None
    assert area_after["id"] != area_before["id"]
    assert area_after["template"] == _TEMPLATE_B
    url_after = (
        _TEMPLATE_B.replace("{z}", str(area_after["centre_tile"]["z"]))
        .replace("{x}", str(area_after["centre_tile"]["x"]))
        .replace("{y}", str(area_after["centre_tile"]["y"]))
    )

    # The first area's OLD-basemap tiles survive; the second area's
    # NEW-basemap tiles are there too — neither bucket was touched by the
    # other's confirm.
    assert _pinned_cache_has(page, url_before)
    assert _pinned_cache_has(page, url_after)


def test_failed_download_keeps_the_frame_up_and_says_why(pwa_page: PwaPage) -> None:
    """SNOW-568: a failed run reports itself and leaves the framed area alone.

    The bug this covers: every failure reverted the roundel to ``idle``
    and closed the framing overlay, which is exactly what a Cancel looks
    like — so a download that fetched nothing was indistinguishable from
    one the user never started. The framed bbox has to survive too, or the
    "try again" the message offers means re-framing from scratch.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)

    _confirm_download(page, worker, ok=0, failed=9, reason="network")

    assert page.locator("#map-frame-overlay").is_visible()
    expect(page.locator("#map-download-error-toast")).to_be_visible()
    expect(page.locator("#map-download-error-toast-quota")).to_be_hidden()
    # Retrying is one click on the still-live CTA bar, not a re-frame.
    assert page.locator("#map-frame-confirm").is_enabled()
    # Nothing was downloaded, so nothing may claim to have been saved.
    assert _last_custom_area(page) is None


def test_failed_download_toast_clears_the_cta_bar(pwa_page: PwaPage) -> None:
    """SNOW-568: the toast is lifted above the CTA sheet it would otherwise cover.

    Both dock at the foot of the viewport. A toast telling the user to
    retry, sitting on top of the Download button, would be self-defeating.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)

    _confirm_download(page, worker, ok=0, failed=9, reason="network")

    toast = page.locator("#map-download-error-toast").bounding_box()
    cta = page.locator("#map-frame-cta").bounding_box()
    assert toast is not None and cta is not None
    assert toast["y"] + toast["height"] <= cta["y"], (
        "the failure toast must sit clear of the CTA bar's Cancel/Download buttons"
    )


def test_quota_failure_shows_the_storage_message(pwa_page: PwaPage) -> None:
    """SNOW-568: a ``quota`` reason gets the "free up space" copy instead."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)

    _confirm_download(page, worker, ok=0, failed=9, reason="quota")

    expect(page.locator("#map-download-error-toast-quota")).to_be_visible()
    expect(page.locator("#map-download-error-toast")).to_be_hidden()


def test_retry_after_a_failure_completes_the_download(pwa_page: PwaPage) -> None:
    """SNOW-568: a second Download click on the surviving frame succeeds."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker, ok=0, failed=9, reason="network")
    expect(page.locator("#map-download-error-toast")).to_be_visible()

    _confirm_download(page, worker, ok=1, failed=0)

    # SNOW-632: the retry's own success no longer closes the overlay —
    # it repaints the CTA in place instead.
    assert page.locator("#map-frame-overlay").is_visible()
    expect(page.locator("#map-download-error-toast")).to_be_hidden()
    assert _last_custom_area(page) is not None


def test_cancelling_after_a_failure_clears_the_message(pwa_page: PwaPage) -> None:
    """SNOW-568: dismissing framing abandons the attempt, and its toast with it."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker, ok=0, failed=9, reason="network")
    expect(page.locator("#map-download-error-toast")).to_be_visible()

    page.click("#map-frame-cancel")

    _wait_for_overlay_closed(page)
    expect(page.locator("#map-download-error-toast")).to_be_hidden()


# SNOW-632: live progress readout, Download/pan/zoom locked while busy,
# Cancel actually cancelling, and the completed-run CTA state that
# replaced the old "success closes the overlay" behaviour.


def test_busy_readout_shows_percentage_and_megabytes(pwa_page: PwaPage) -> None:
    """The CTA readout carries both the tile-count percentage and actual MB."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)

    _stub_warm_cache(
        worker,
        ok=1,
        failed=0,
        progress_steps=[(1, 4), (2, 4), (3, 4), (4, 4)],
        step_delay_ms=150,
        bytes_total=4 * 1024 * 1024,
    )
    page.click("#map-frame-confirm")
    _wait_for_run_state(page, "busy")

    page.wait_for_function(
        """() => {
            const t = document.getElementById('map-frame-readout').innerText;
            return /%/.test(t) && /MB/.test(t);
        }"""
    )
    text = _readout_text(page)
    assert "%" in text
    assert "MB" in text

    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)


def test_download_disabled_and_map_inert_while_busy(pwa_page: PwaPage) -> None:
    """SNOW-632 requirements 2/3: Download disables, pan/zoom freeze, Cancel stays live."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)

    _stub_warm_cache(
        worker,
        ok=1,
        failed=0,
        progress_steps=[(1, 3), (2, 3), (3, 3)],
        step_delay_ms=200,
    )
    page.click("#map-frame-confirm")
    _wait_for_run_state(page, "busy")

    assert page.locator("#map-frame-confirm").is_disabled()
    assert page.evaluate("() => MAP.dragPan.isEnabled()") is False
    assert page.evaluate("() => MAP.scrollZoom.isEnabled()") is False
    assert page.evaluate("() => MAP.touchZoomRotate.isEnabled()") is False
    # Requirement 4: Cancel is the one thing NOT disabled mid-run.
    assert page.locator("#map-frame-cancel").is_enabled()

    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)
    # The run settled with framing still open — pan/zoom are handed back
    # to framing's own anchored state (_unlockMapAfterRun), not left inert.
    assert page.evaluate("() => MAP.dragPan.isEnabled()") is True


def test_cancel_mid_run_returns_to_idle_and_refreshes_the_sync_dashboard(
    pwa_page: PwaPage,
) -> None:
    """SNOW-632 requirement 4: Cancel mid-run actually stops the download.

    The roundel must land on ``idle``, never ``done`` — a cancelled run is
    neither success nor failure, and the probe checks the WHOLE saved
    area's tile set, so claiming done here would claim more than landed.

    Deterministic by construction, not by a generous wall-clock margin:
    the stub (``pause_after_step=0``) holds the run right after the first
    progress tick lands, this test waits for that pause to actually be in
    effect before clicking Cancel, and waits again for the SW to have
    genuinely recorded the cancel (``self.__snow521ShouldCancel()`` reading
    true) before releasing the pause — closing the race a fixed
    ``wait_for_timeout`` against the stub's own step spacing left open
    under a loaded CI runner (SNOW-632 review finding).
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _arm_sync_dashboard_probe(page)

    _stub_warm_cache(
        worker,
        ok=4,
        failed=0,
        progress_steps=[(1, 4), (2, 4), (3, 4), (4, 4)],
        cancellable=True,
        pause_after_step=0,
    )
    page.click("#map-frame-confirm")
    _wait_for_run_state(page, "busy")
    # Wait for the stub to actually be paused mid-run, rather than assuming
    # the first tick landed within some fixed window.
    _wait_for_worker_flag(worker, "() => self.__snow521Paused === true")

    page.click("#map-frame-cancel")
    _wait_for_overlay_closed(page)
    # Confirm sw.js's real 'warm-cache-cancel' listener has recorded the
    # cancel before letting the stub's loop re-check shouldCancel() — this
    # is the step a blind sleep was standing in for.
    _wait_for_worker_flag(
        worker, "() => !!(self.__snow521ShouldCancel && self.__snow521ShouldCancel())"
    )
    worker.evaluate("() => { if (self.__snow521Resume) self.__snow521Resume(); }")

    _wait_for_state(page, "idle", selector=_CONTROL, timeout=10000)
    assert _last_custom_area(page) is None
    _assert_sync_dashboard_refreshed(page)


def test_completed_download_leaves_overlay_open_with_a_working_close(
    pwa_page: PwaPage,
) -> None:
    """SNOW-632 requirement 5: overlay stays open, "X MB downloaded", Close works."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)

    _stub_warm_cache(worker, ok=1, failed=0, bytes_total=6 * 1024 * 1024)
    page.click("#map-frame-confirm")
    _wait_for_run_state(page, "busy")
    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)

    assert page.locator("#map-frame-overlay").is_visible()
    text = _readout_text(page)
    assert "downloaded" in text.lower()
    assert "6.0 MB" in text
    assert page.locator("#map-frame-confirm").is_hidden()
    close_btn = page.locator("#map-frame-cancel")
    assert close_btn.is_enabled()
    assert close_btn.inner_text().strip().lower() == "close"

    close_btn.click()
    _wait_for_overlay_closed(page)


def test_reopening_framing_after_completion_resets_the_cta(pwa_page: PwaPage) -> None:
    """A Close-relabelled/hidden CTA must not leak into the next framing session."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker, ok=1, failed=0)
    assert page.locator("#map-frame-confirm").is_hidden()

    # Close this session (framing hides the roundel's own container for as
    # long as its overlay is up — SNOW-632's completed run no longer
    # closes it automatically, so the roundel is not there to click until
    # this happens), then re-click the (now 'done') roundel — openFraming
    # must reset the CTA back to its pre-download shape, not carry the
    # previous run's Close state into this new session.
    _close_completed_overlay(page)
    _open_framing(page)

    expect(page.locator("#map-frame-confirm")).to_be_visible()
    # SNOW-632 review finding: _updateReadout always writes
    # confirmBtn.disabled = overCeiling || !navigator.onLine, but nothing
    # asserted the reset actually re-enables it.
    expect(page.locator("#map-frame-confirm")).not_to_be_disabled()
    assert page.locator("#map-frame-cancel").inner_text().strip().lower() == "cancel"
    assert "downloaded" not in _readout_text(page).lower()


def test_budget_banner_shows_the_standing_total_and_grows_on_completion(
    pwa_page: PwaPage,
) -> None:
    """The top banner reports the total across every pinned area against the budget."""
    page, worker = _boot(pwa_page)

    # Seed a pre-existing REGION download (a distinct area from the custom
    # one this test downloads below) so the standing total starts
    # non-zero — basemapDownloadedAreas() unions basemap.regions with
    # basemap.customAreas (map.js), so a region-shaped row is enough.
    page.evaluate(
        """() => window.pwaDb.put('meta:app', {
            key: 'basemap.regions',
            value: [{
                region_id: 'CH-9999',
                name: 'Seed',
                bytes: 10 * 1024 * 1024,
                savedAt: new Date().toISOString(),
            }],
        })"""
    )

    _open_framing(page)
    _wait_for_budget_banner(page)
    before = _instruction_text(page)
    assert "10.0 MB" in before
    assert "500 MB" in before

    _frame_a_downloadable_area(page)
    _stub_warm_cache(worker, ok=1, failed=0, bytes_total=5 * 1024 * 1024)
    page.click("#map-frame-confirm")
    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)

    page.wait_for_function(
        """(before) => document.getElementById('map-frame-instruction').innerText !== before""",
        arg=before,
    )
    after = _instruction_text(page)
    assert "15.0 MB" in after
    assert "500 MB" in after


def test_budget_banner_adds_a_new_areas_live_progress_cleanly(
    pwa_page: PwaPage,
) -> None:
    """SNOW-635: a brand-new area's live progress adds to the baseline, excluding nothing.

    Through SNOW-632 this covered a bug in the opposite direction — a
    RE-download of the single existing custom area double-counted its own
    recorded share, since the live banner rendered
    ``bannerBaselineBytes + liveBytes`` without first taking the area's own
    existing bytes back out. SNOW-635 removes the scenario that bug needed
    (there is no longer a single existing custom area a confirm could ever
    be "re-downloading" — every confirm mints a fresh id): `handleConfirm`
    keys the exclusion off the run's OWN generated id
    (``currentRunAreaId``), which a brand-new area never has an existing
    record under, so it always resolves to "nothing to exclude" — see
    ``_refreshBudgetBanner``'s own comment. This asserts that side directly:
    a new area's live bytes land cleanly ON TOP of the standing baseline,
    with nothing subtracted.

    Held mid-run via the stub's ``pause_after_step`` handshake so the
    assertion lands on a known progress tick rather than a sleep.
    """
    page, worker = _boot(pwa_page)

    # A pre-existing download of ANOTHER area (a region), so the baseline
    # has a component this run must not touch.
    page.evaluate(
        """() => window.pwaDb.put('meta:app', {
            key: 'basemap.regions',
            value: [{
                region_id: 'CH-9999',
                name: 'Seed',
                bytes: 10 * 1024 * 1024,
                savedAt: new Date().toISOString(),
            }],
        })"""
    )

    # And a pre-existing CUSTOM area, so the baseline this run's own
    # progress must add on top of already has a custom component too.
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _stub_warm_cache(worker, ok=1, failed=0, bytes_total=20 * 1024 * 1024)
    page.click("#map-frame-confirm")
    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)
    _close_completed_overlay(page)

    # Baseline is now 10 MB (region) + 20 MB (custom) = 30 MB.
    _open_framing(page)
    _wait_for_budget_banner(page)
    assert "30.0 MB" in _instruction_text(page)

    # Confirm a THIRD, brand-new area (the frame has not moved, but that no
    # longer matters — see the module docstring), paused after the first
    # progress tick.
    _stub_warm_cache(
        worker,
        ok=1,
        failed=0,
        bytes_total=20 * 1024 * 1024,
        progress_steps=[(1, 2), (2, 2)],
        pause_after_step=0,
    )
    page.click("#map-frame-confirm")
    _wait_for_run_state(page, "busy")
    _wait_for_worker_flag(worker, "() => self.__snow521Paused === true")

    # Half the run's 20 MB has landed, on top of the untouched 30 MB
    # baseline — 40 MB, not 20 MB (which the old re-download exclusion
    # would have wrongly produced here).
    page.wait_for_function(
        """() => /\\b40\\.0 MB\\b/.test(
            document.getElementById('map-frame-instruction').innerText
        )""",
        timeout=10000,
    )

    worker.evaluate("() => { if (self.__snow521Resume) self.__snow521Resume(); }")
    # SNOW-635 review: the ROUNDEL's own "done" (``_CONTROL``) already read
    # done throughout this run — the first two areas kept it there — so it
    # is not a valid "this run has settled" signal for a second-or-later
    # confirm; nor, it turns out, is the banner text alone: a live "50.0 MB"
    # progress readout can coincidentally match the SAME text the settled
    # total renders, before `finish()` has actually appended the record.
    # The overlay's own run-state is the one signal that genuinely
    # transitions busy -> done exactly once per run (set synchronously by
    # `paintRun`, strictly after `_appendCustomArea` has been awaited) —
    # the same thing `_confirm_download` already waits for.
    _wait_for_run_state(page, "done", timeout=10000)

    # Settles at 30 + 20 = 50 MB — a genuinely new area's full total added,
    # never replacing anything.
    page.wait_for_function(
        """() => /\\b50\\.0 MB\\b/.test(
            document.getElementById('map-frame-instruction').innerText
        )""",
        timeout=10000,
    )
    assert len(_custom_areas(page)) == 2


def test_confirming_twice_at_the_same_bbox_records_two_independent_totals(
    pwa_page: PwaPage,
) -> None:
    """SNOW-635: a same-bbox repeat is a SECOND independent area, not a re-record.

    Through SNOW-632 this covered a bug where confirming Download twice at
    the SAME bbox — re-fetching identical tile URLs into the ONE shared
    bucket every custom-area download used before this ticket — doubled
    the recorded byte total, because the original code accumulated the
    run's reported ``bytes`` onto the previous record regardless.

    That bucket is no longer shared: every confirm mints its own id
    (``generateCustomAreaId``) and its own bucket, so a same-bbox repeat
    downloads a second area from scratch rather than re-recording the
    first. The regression this now guards is the same shape one level up
    — each area's own reported total must stand on its own, neither
    summed with nor overwritten by the other's.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)

    first_bytes = 60 * 1024 * 1024
    _stub_warm_cache(worker, ok=1, failed=0, bytes_total=first_bytes)
    page.click("#map-frame-confirm")
    _wait_for_run_state(page, "busy")
    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)
    first = _last_custom_area(page)
    assert first is not None
    assert first["bytes"] == first_bytes

    # Close this session and re-open framing without moving the map — the
    # frame lands back on the SAME ground, so this confirm is the
    # same-bbox repeat the original bug describes.
    _close_completed_overlay(page)
    _open_framing(page)
    second_bytes = 25 * 1024 * 1024
    _stub_warm_cache(worker, ok=1, failed=0, bytes_total=second_bytes)
    page.click("#map-frame-confirm")
    _wait_for_run_state(page, "busy")
    # SNOW-635 review: NOT `_wait_for_state(page, "done", selector=_CONTROL)`
    # — the roundel already reads "done" from the FIRST area and never
    # stops doing so, so that wait is a same-instant no-op here, not a
    # genuine "this run has settled" signal. The overlay's own run-state
    # is what actually transitions busy -> done exactly once per run,
    # strictly after `_appendCustomArea` has been awaited inside `finish`.
    _wait_for_run_state(page, "done", timeout=10000)

    areas = _custom_areas(page)
    assert len(areas) == 2
    second = _last_custom_area(page)
    assert second is not None
    assert second["id"] != first["id"]
    # Each area's own figure — neither summed nor overwritten by the other.
    assert second["bytes"] == second_bytes
    assert next(a for a in areas if a["id"] == first["id"])["bytes"] == first_bytes


def _readout_mb(page: Page) -> int:
    """The "Up to N MB" estimate currently shown in the frame readout."""
    match = re.search(r"Up to (\d+) MB", _readout_text(page))
    assert match, f"unexpected readout text: {_readout_text(page)!r}"
    return int(match.group(1))


def test_confirming_a_second_area_over_a_full_budget_evicts_the_first(
    pwa_page: PwaPage,
) -> None:
    """SNOW-635: the standing BUDGET can still evict one of the user's OWN custom areas.

    Every bbox/template-driven eviction the custom-area control used to run
    on its own confirm is gone (see the module docstring) — but the shared
    standing byte budget (SNOW-586) is not, and two GENUINE custom areas
    can now compete for it in a way that was structurally impossible
    before this ticket (there was never more than one to compete). This
    forces exactly that: download one area under a budget sized to hold
    only it, then confirm a second — the pre-flight for the second finds
    the first already on disk and cannot fit both, raising the SAME
    whole-area-eviction confirm banner the region control uses
    (``#map-download-evict-confirm`` — ``confirmBasemapEviction`` is
    shared), naming the first area and choosing to evict it to make room
    for the second.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    estimate_mb = _readout_mb(page)
    estimate_bytes = estimate_mb * 1024 * 1024

    # The first area's own reported bytes match its estimate exactly, so
    # the budget below can be pinned to precisely that figure.
    _stub_warm_cache(worker, ok=1, failed=0, bytes_total=estimate_bytes)
    page.click("#map-frame-confirm")
    _wait_for_run_state(page, "busy")
    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)
    first = _last_custom_area(page)
    assert first is not None
    assert first["bytes"] == estimate_bytes

    # A budget sized to hold exactly the first area, and nothing else.
    _set_budget_mb(page, estimate_mb)

    # Re-open framing without moving the map, so the second area's own
    # estimate is the SAME figure — the standing total (first area alone)
    # already exactly fills the budget, so a second, same-sized area can
    # only fit by evicting the first.
    _close_completed_overlay(page)
    _open_framing(page)
    _wait_for_budget_banner(page)
    _stub_warm_cache(worker, ok=1, failed=0, bytes_total=estimate_bytes)
    page.click("#map-frame-confirm")

    banner = page.locator("#map-download-evict-confirm")
    banner.wait_for(state="visible", timeout=10000)
    body = page.locator("#map-download-evict-confirm-body")
    # The banner names the user's OWN prior custom area — the only thing
    # old enough to be picked, and the only area on disk at all.
    assert (body.text_content() or "").strip() != ""

    page.click("#map-download-evict-confirm-cta")
    # SNOW-635 review: the roundel (`_CONTROL`) reads "done" throughout —
    # the first area is on disk for the whole exchange, eviction included
    # — so waiting on it here would resolve immediately, before the second
    # run (and the eviction that preceded it) has actually settled. The
    # overlay's own run-state is the genuine per-run busy -> done signal.
    _wait_for_run_state(page, "done", timeout=10000)

    areas = _custom_areas(page)
    assert len(areas) == 1, (
        "the first area should have been evicted, not kept alongside the second"
    )
    assert areas[0]["id"] != first["id"]
