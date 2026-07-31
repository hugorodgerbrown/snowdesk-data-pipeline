"""
tests/e2e/test_custom_download_area.py — Playwright regression tests for the
"Download a custom area" basemap control (SNOW-522).

Companion to ``test_cache_this_area.py``'s per-region control: this one has
no fixed region to size ahead of time, so clicking
``#map-custom-download-control`` opens a framing overlay
(``#map-frame-overlay``) instead of downloading immediately — a
Google-Maps-style dim mask with a fixed, centred frame the user pans/zooms
the map underneath, with a live "up to N MB" readout (computed entirely
client-side — ``static/js/basemap_download_core.js``, no network round
trip) and a docked Cancel/Download CTA bar.

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
``pinned: true``, reaches ``done``, and notifies the layers sync
dashboard; reload + click the (probed) green roundel re-opens framing at
the saved area; moving the frame then Cancelling leaves the saved area
untouched; moving the frame then confirming evicts the old area's tiles
from the pinned cache before warming the new set.

SNOW-568 adds the failure path, which had no coverage because it had no
behaviour: every failed run reverted the roundel to ``idle`` and closed
the overlay, indistinguishable from a Cancel. A failed run now holds the
frame up, paints ``error``, and raises a toast (lifted clear of the CTA
sheet it shares the foot of the viewport with) whose copy depends on
whether the cause was the storage quota or anything else — covered here
along with retrying and cancelling out of that state.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from playwright.sync_api import Page, Worker as SWWorker, expect

from tests.e2e.conftest import PwaPage
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
    """Click the roundel and wait for the overlay to reveal itself."""
    page.click(_CONTROL)
    page.wait_for_selector("#map-frame-overlay:not([hidden])")


def _wait_for_overlay_closed(page: Page) -> None:
    page.wait_for_selector("#map-frame-overlay[hidden]", state="attached")


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


def _force_done_reprobe(page: Page) -> None:
    """Re-run the control's done-probe now that the tile template is stubbed.

    The probe keys off the ACTIVE basemap's tile template, which ``_boot``
    only stubs *after* the map is ready — by which point the control has
    already painted from a probe that had no template to look up. The real
    app re-probes on ``snowdesk:basemap-changed`` (the download is
    per-basemap), and the control listens for it, so dispatching it is the
    supported way to ask for a fresh probe. ``test_cache_this_area.py``
    gets the same effect for the region control by re-selecting the region.
    """
    page.evaluate(
        "() => document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'))"
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
    """
    _stub_warm_cache(worker, ok=ok, failed=failed, reason=reason)
    page.click("#map-frame-confirm")
    _wait_for_state(page, "busy", selector=_CONTROL)
    expected = "done" if (ok > 0 and failed == 0) else "error"
    _wait_for_state(page, expected, selector=_CONTROL, timeout=10000)


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


def _saved_area(page: Page) -> dict[str, Any] | None:
    """The persisted ``basemap.customArea`` meta:app row's value, or None."""
    row = page.evaluate("() => window.pwaDb.get('meta:app', 'basemap.customArea')")
    return cast("dict[str, Any] | None", row["value"] if row else None)


def _centre_tile_url(page: Page, centre_tile: dict[str, Any]) -> str:
    """The done-probe URL for `centre_tile`, under the stubbed template."""
    return cast(
        str,
        page.evaluate(
            """({ template, centreTile }) =>
                self.pwaBasemapDownloadCore.centreTileURL(template, { centre_tile: centreTile })
            """,
            {"template": _STUB_TEMPLATE, "centreTile": centre_tile},
        ),
    )


def _pinned_cache_has(page: Page, url: str) -> bool:
    """Whether `url` is present in the (single) prefix-matched pinned cache."""
    return bool(
        page.evaluate(
            """async ({ url, prefix }) => {
                const names = await caches.keys();
                const name = names.find((n) => n.startsWith(prefix));
                if (!name) return false;
                const cache = await caches.open(name);
                return !!(await cache.match(url));
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

    # The instruction bar names the task. It precedes the frame in DOM
    # order, so it needs its own stacking context to escape the frame's
    # box-shadow mask — assert it is lifted, since a silently-dimmed
    # instruction still "renders" and would pass a bare visibility check.
    instruction = page.locator("#map-frame-instruction")
    assert instruction.is_visible()
    assert "offline" in instruction.inner_text().lower()
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
    """Confirming warms with pinned:true, reaches done, and closes the overlay.

    Also covers the "notify the layers sync dashboard" invariant —
    ``window.pwaLayerSyncStatus?.refresh()`` must run after every completed
    download, region or custom-area alike.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _arm_sync_dashboard_probe(page)

    _confirm_download(page, worker)

    _wait_for_overlay_closed(page)
    urls = cast("list[str]", worker.evaluate("() => self.__snow521Urls || []"))
    assert any(url.startswith(_STUB_TEMPLATE.split("{")[0]) for url in urls)
    assert any("country=ch" in url for url in urls)
    assert worker.evaluate("() => self.__snow521Pinned") is True
    _assert_sync_dashboard_refreshed(page)

    area = _saved_area(page)
    assert area is not None
    assert "bbox" in area and "band" in area and "centre_tile" in area


def test_reload_and_click_done_reopens_at_the_saved_area(pwa_page: PwaPage) -> None:
    """A completed download survives reload; clicking the green roundel
    re-opens framing with the map recentred on the saved area.
    """
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker)
    _wait_for_overlay_closed(page)
    area = _saved_area(page)
    assert area is not None

    page, worker = _boot(pwa_page)
    _force_done_reprobe(page)
    _wait_for_state(page, "done", selector=_CONTROL, timeout=10000)

    _open_framing(page)

    west, south, east, north = area["bbox"]
    centre = page.evaluate(
        "() => { const c = MAP.getCenter(); return [c.lng, c.lat]; }"
    )
    # fitBounds is not pixel-exact, but the map's centre after re-opening
    # at the saved area must land inside (or very near) its footprint.
    assert west - 0.01 <= centre[0] <= east + 0.01
    assert south - 0.01 <= centre[1] <= north + 0.01


def test_move_then_cancel_leaves_the_saved_area_intact(pwa_page: PwaPage) -> None:
    """Moving the frame and Cancelling touches neither the saved row nor the cache."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker)
    _wait_for_overlay_closed(page)
    area_before = _saved_area(page)
    assert area_before is not None
    url_before = _centre_tile_url(page, area_before["centre_tile"])
    assert _pinned_cache_has(page, url_before)

    _open_framing(page)
    _move_the_frame(page)
    page.click("#map-frame-cancel")
    _wait_for_overlay_closed(page)

    assert _saved_area(page) == area_before
    assert _pinned_cache_has(page, url_before)


def test_move_then_confirm_evicts_the_old_areas_tiles(pwa_page: PwaPage) -> None:
    """Confirming a moved frame evicts the previous area's pinned tiles first."""
    page, worker = _boot(pwa_page)
    _open_framing(page)
    _frame_a_downloadable_area(page)
    _confirm_download(page, worker)
    _wait_for_overlay_closed(page)
    area_before = _saved_area(page)
    assert area_before is not None
    url_before = _centre_tile_url(page, area_before["centre_tile"])
    assert _pinned_cache_has(page, url_before)

    _open_framing(page)
    _move_the_frame(page)
    _confirm_download(page, worker)
    _wait_for_overlay_closed(page)

    area_after = _saved_area(page)
    assert area_after is not None
    assert area_after != area_before
    url_after = _centre_tile_url(page, area_after["centre_tile"])

    assert not _pinned_cache_has(page, url_before), (
        "the old area's tiles should have been evicted before the new set warmed"
    )
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
    assert _saved_area(page) is None


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

    _wait_for_overlay_closed(page)
    expect(page.locator("#map-download-error-toast")).to_be_hidden()
    assert _saved_area(page) is not None


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
