/*
 * static/js/map_custom_download.js — the "Download a custom area" roundel
 * and its framing overlay.
 *
 * SNOW-610, step 3: extracted verbatim from map.js, where it was the
 * single largest IIFE at 1,228 lines. The sibling of
 * static/js/map_region_download.js: that control downloads a region whose
 * tile coverage the server precomputed, this one downloads whatever the
 * user frames, sizing it client-side via `pwaBasemapDownloadCore.buildBlob`
 * once per animation frame.
 *
 * Both share `runPinnedDownload` and everything around it from
 * static/js/map_basemap_downloads.js. Seeing the two side by side as files
 * is the point of the split — the SNOW-606 review found five parallel
 * members across them, and a sixth is now a diff between two files rather
 * than a repetition inside one.
 *
 * LOAD ORDER: runs at parse time, binds DOM from _map_embed.html, loads
 * after map.js — the execution order it had as the seventh IIFE there.
 */

// SNOW-522: "Download a custom area" — takes the bottom-right utility-stack
// slot the per-region control (above) vacated when it moved back into the
// ribbon header. Unlike that control there is no fixed region to size
// ahead of time: clicking the roundel opens a framing overlay
// (#map-frame-overlay, _map_embed.html) — a Google-Maps-style dim mask
// with a centred frame. The user pans/zooms the map underneath it; the
// live "up to N MB" readout is recomputed once per animation frame for as
// long as the SELECTION is changing, entirely client-side, via
// pwaBasemapDownloadCore.buildBlob (static/js/basemap_download_core.js —
// see its header for why this tile math is a deliberate re-port of the
// server-side module, not drift).
//
// Frame geometry — the frame's four corners (read from its own
// getBoundingClientRect(), never a hardcoded size: the dimensions live in
// CSS) are unprojected ALL FOUR, not just two opposite ones, and the bbox
// is the min/max over the lot: MapLibre supports rotation, so a rotated
// view makes the frame a non-axis-aligned quad on the map, and two
// corners alone would under-cover it. _screenBoxForBBox does the same in
// reverse, for the same reason.
//
// Two regimes, and knowing which one you are in explains everything about
// how the frame behaves (SNOW-567 — see _updateSelection):
//
//   Under the ceiling, the frame is a viewport-anchored reticle. It fills
//   its gutter-inset area, and the selection is whatever ground happens to
//   be beneath it — so both pan and zoom change the selection.
//
//   Once the ceiling caps the area, the selection LOCKS to the ground and
//   the frame becomes a projection of it. Zooming then recomputes nothing
//   at all: the same bbox covers the same tiles, so the frame just tracks
//   its own terrain (and scales with the map, as any map feature does)
//   while the estimate holds still. Panning is what re-aims a locked
//   selection, moving it back under the frame at the same size.
//
// Persistence — SNOW-635: any number of custom areas can exist at once.
// A confirmed download is appended to IndexedDB's meta:app store under
// 'basemap.customAreas' (an ARRAY — see `_appendCustomArea`/
// `_readCustomAreas`, and their docstrings for the lazy migration from the
// old single-row 'basemap.customArea'), each entry {id, ordinal, name?,
// bbox, band, centre_tile, template, basemapKey, bytes, savedAt}. `id` is
// minted fresh
// per download (`generateCustomAreaId`) rather than the single fixed
// `CUSTOM_AREA_ID` every earlier version shared — that shared id is why a
// second download used to silently replace the first. `name` is written
// ONLY by a rename (map_downloads_manager.js's Rename control); an
// unrenamed area's default label ("Custom area N") is derived from
// `ordinal` on every READ instead — `basemapDownloadedAreas()` fills it
// into the in-memory result, never onto this stored record — which is
// what keeps it translatable instead of frozen in whatever language was
// active at download time.
//
// The roundel's "done" state (SNOW-634) does not probe any one area's own
// bbox against the pinned cache's WHOLE tile set — that was `_probeDone`,
// deleted before this ticket. Clicking the roundel opens the downloads
// sheet (public/partials/_map_downloads_sheet.html), which lists EVERY
// downloaded area, so "done" means "the device holds at least one
// downloaded area" (basemapDownloadedAreas(), filtered to non-orphaned —
// see _renderControl below), region or custom, of however many there are.
//
// openFraming no longer re-centres the map on a saved area on open — with
// several custom areas possibly on disk, picking one to jump to would be
// arbitrary, so framing always starts from wherever the map currently
// sits. The "Download a custom area" trigger lives in the sheet
// ([data-downloads-add], map_downloads_manager.js), not the roundel's own
// click.
//
// SNOW-586 gave a confirmed run TWO distinct evictions it might trigger:
// (1) replacing the single saved area's own bucket outright when the frame
// moved or the basemap changed, and (2) making room under the standing
// byte budget by evicting OTHER areas entirely (`planBasemapDownloadBudget`
// / `planEviction`, always confirmed first via `confirmBasemapEviction`).
// SNOW-635 removes (1): a fresh id never collides with an existing bucket,
// so a confirmed run has nothing of the user's own left to replace —
// moving the frame or switching basemap before re-confirming now downloads
// a SECOND, independent area rather than replacing the first. Only (2)
// remains, exactly as mapDownloadControlInit's own region `beforeWarm`
// already works for a genuine same-region re-download.
//
// Offline-integrity: SNOW-634 relaxed the first half of this — the
// roundel now opens the downloads sheet unconditionally, which is exactly
// where storage pressure is felt, so browsing/deleting what is already
// downloaded needs no connection. Confirming a NEW download is still
// refused offline, both by the sheet's own add-trigger (a toast, not the
// overlay — map_downloads_manager.js) and by this overlay's own Download
// button once framing is open, mirroring mapDownloadControlInit's offline
// handling there.
//
// SNOW-632: a confirmed run now OWNS the overlay for its duration rather
// than handing straight back to the map. The CTA readout shows live
// progress by BOTH measures — "42% · 6.1 MB", tile count and actual
// on-disk bytes — Download disables, and dragPan plus every zoom handler
// freeze (extending _anchorZoomOnTheFrame/_releaseZoomAnchor's own
// anchor/release pair — see _lockMapForRun/_unlockMapAfterRun) so neither
// gesture can shift the selection out from under tiles already being
// fetched. Cancel stays live throughout — it is the one thing NOT
// disabled — and, mid-run, actually stops the download
// (window.pwaWarmCacheCancel(), posted from the overlay:dismissed
// listener below) rather than merely hiding a run that carries on
// unseen. A run that settles `cancelled` (basemap_download_runner.js's
// `finish` docstring) is neither success nor failure: it records nothing,
// so the roundel — re-derived from storage once `paintRun` (SNOW-634's
// rename of `setState`) calls `renderControl()` on settling — reads idle,
// never done. A SUCCESSFUL run no longer closes the overlay either — see
// paintRun's 'done' branch — it repaints the CTA in
// place ("23.4 MB downloaded", Download hidden, Cancel relabelled Close)
// and leaves closing it to the user, on the same dismiss idiom Cancel
// always used. The top instruction bar becomes a standing-budget banner
// while framing is open — "39 MB / 500 MB downloaded" across every
// pinned area, not just this one (_renderBudgetBanner) — read from
// IndexedDB once per framing session and once more when a run settles,
// never per progress tick.
(function mapCustomDownloadControlInit() {
  const btn = document.getElementById('map-custom-download-control');
  const overlayEl = document.getElementById('map-frame-overlay');
  const frameAreaEl = document.getElementById('map-frame-area');
  const frameRectEl = document.getElementById('map-frame-rect');
  const readoutEl = document.getElementById('map-frame-readout');
  const confirmBtn = document.getElementById('map-frame-confirm');
  // SNOW-632: Cancel doubles as Close once a run has completed (see
  // paintRun's 'done' branch) — its own click still goes through the
  // shared data-action="dismiss" idiom, so no new listener is needed, but
  // its label has to be readable/restorable, hence the lookup and the
  // captured default below. The instruction bar becomes the standing
  // download-budget banner while framing is open (see
  // _renderBudgetBanner) — optional, since a missing element degrades to
  // "no banner", never a hard failure.
  const cancelBtn = document.getElementById('map-frame-cancel');
  const instructionEl = document.getElementById('map-frame-instruction');
  if (!btn || !overlayEl || !frameAreaEl || !frameRectEl || !readoutEl || !confirmBtn || !cancelBtn) {
    return;
  }

  // SNOW-632: Cancel's own translated label ({% trans "Cancel" %} in the
  // template), captured once so it can be restored after a run relabels
  // the button to "Close" — see paintRun's 'done' branch and openFraming's
  // reset.
  const CANCEL_LABEL_DEFAULT = cancelBtn.textContent;

  // SNOW-635: the id `handleConfirm` mints for the run currently in
  // flight, or null between runs — see `_refreshBudgetBanner`'s own
  // comment for why this replaces a `CUSTOM_AREA_ID` lookup there.
  let currentRunAreaId = null;

  // SNOW-634: is-a-run-in-flight, replacing the six `btn.dataset.
  // downloadState === 'busy'` reads this file used to have. The roundel's
  // own `data-download-state` is now only ever 'idle'/'done' — derived
  // from storage by `_renderControl`, never told what to paint by a run —
  // so it stopped being a channel a run in flight could use at all.
  // Mirrored onto `#map-frame-overlay` as `data-run-state` by `paintRun`,
  // which IS visible for the run's whole life (`.map-framing` hides
  // `#map-controls-br`, the roundel's own container, for as long as the
  // overlay is open — see this IIFE's own header comment) and is what an
  // e2e test now reads to synchronise on a run in progress.
  let runState = 'idle';

  // The live bbox/blob for whatever the frame currently covers, while the
  // overlay is open — null when it is closed.
  let pendingBbox = null;
  let pendingBlob = null;

  // The 'move' and 'resize' listeners registered while framing, so they can
  // be removed on close. Null when not framing.
  let moveHandler = null;
  let resizeHandler = null;

  // SNOW-632: the standing download-budget banner's cached inputs — the
  // total bytes already recorded across every pinned area, and the
  // effective budget, both in bytes. Read once per framing session
  // (openFraming) and once more when a run settles (finish), never per
  // progress tick — see _refreshBudgetBanner's own comment for why.
  let bannerBaselineBytes = 0;
  let bannerBudgetBytes = 0;
  // SNOW-632 review finding: true once the pair above has been read from
  // IndexedDB at least once. openFraming fires _refreshBudgetBanner()
  // without awaiting it, so a run confirmed before that read resolves
  // would otherwise paint against the zero bannerBudgetBytes has not yet
  // been given a real value — "X MB / 0 MB downloaded". _renderBudgetBanner
  // checks this and holds the pre-existing instruction text instead.
  let bannerBudgetKnown = false;

  // This area's OWN share of `bannerBaselineBytes`. A run replaces that
  // share rather than adding to it (finish records the run's own bytes —
  // see docs/decisions/per-area-pinned-basemap-caches.md), so the live
  // banner has to take it back out or it counts the area twice for the
  // duration: baseline 62.6 MB + 18.4 MB landed read as 80.9 MB used
  // when the true figure was 18.4 MB, snapping back only once `finish`
  // re-read the records. Mirrors what `planEviction` already does for
  // budget planning, where the incoming area is excluded from the
  // standing total for exactly the same reason.
  let bannerOwnAreaBytes = 0;

  /**
   * `bytes` as a display string, via basemap_manage_core.js's
   * `formatMegabytes` — nearest-MB rounding, since this is always an
   * ACTUAL size already on disk, never buildBlob's round-UP estimate (see
   * that module's header for why the two must not be conflated).
   * Defensive: `pwaBasemapManageCore` is loaded on the map page (SNOW-570
   * put it there for the layers-menu sync dashboard), but map.js already
   * treats it as optional elsewhere (see basemapDownloadedAreas) — follow
   * suit rather than assume.
   *
   * @param {number} bytes
   * @returns {string}
   */
  function _formatBytes(bytes) {
    const manage = self.pwaBasemapManageCore;
    return manage && typeof manage.formatMegabytes === 'function'
      ? manage.formatMegabytes(bytes)
      : '0 MB';
  }

  /**
   * Drive a run: the map lock/unlock edges, the CTA's live "42% · 6.1 MB"
   * readout, and the done/error CTA repaint. Renamed from `setState`
   * (SNOW-634) — that function used to ALSO paint the roundel
   * (data-download-state, the busy fill percentage, an aria-label/title
   * drawn from a five-entry state map), but that channel was never
   * visible: `.map-framing` hides `#map-controls-br` — the roundel's own
   * container — for the overlay's entire open life (static/css/map.css),
   * and since SNOW-632 a successful run leaves the overlay open, so
   * 'busy'/'error' were painted onto an element nobody could see. The
   * roundel's own state now comes from `_renderControl`, re-derived from
   * storage rather than told what to paint — see its own docstring below.
   *
   * Two things still only change on the busy transition EDGES — never on
   * a call that merely repeats the current state:
   *
   *   Entering busy: locks the map underneath the overlay
   *   (_lockMapForRun) and starts the CTA's live "42% · 6.1 MB" readout.
   *
   *   Leaving busy: unlocks the map (_unlockMapAfterRun), asks the
   *   roundel to re-derive itself from storage now the run has settled
   *   (`renderControl()`), and paints the CTA's outcome — "23.4 MB
   *   downloaded" with Download hidden and Cancel relabelled Close for a
   *   success, or a restored "Up to N MB" readout (via _updateReadout)
   *   for anything else.
   *
   * The edge check matters: this function used to ALSO be how the
   * background probe repainted 'done'/'idle'/'offline' whenever the
   * roundel was re-evaluated (boot, a basemap switch, a connectivity
   * flip) — which can happen while the overlay is open for an unrelated
   * reason (the user reopened an already-'done' saved area). Gating the
   * CTA-specific work on `wasBusy` — true only for the single call where
   * a REAL run just ended — keeps that background path from ever
   * clobbering the CTA underneath it; the same gate still matters now
   * that the background probe is `_renderControl` calling `renderControl()`
   * rather than this function directly.
   *
   * @param {string} state - 'idle' | 'busy' | 'done' | 'error' | 'offline'.
   * @param {number} [pct] - Only meaningful for state 'busy'.
   * @param {number} [bytes] - SNOW-632: for 'busy', the run's on-disk
   *   bytes so far; for 'done', its final total. Unused otherwise.
   * @returns {void}
   */
  function paintRun(state, pct, bytes) {
    const wasBusy = runState === 'busy';
    runState = state;
    // SNOW-634: the run's own observable, now that the roundel is not —
    // visible for the run's whole life (unlike #map-controls-br), which
    // is what an e2e test (or anything else needing to observe a run in
    // flight) reads instead of the roundel.
    overlayEl.dataset.runState = state;

    // SNOW-632: lock/unlock exactly on the busy edges — every progress
    // tick repaints 'busy' but must not re-lock an already-locked map.
    if (state === 'busy' && !wasBusy) {
      _lockMapForRun();
    } else if (state !== 'busy' && wasBusy) {
      _unlockMapAfterRun();
      // SNOW-634: the run has settled — ask the roundel to re-derive its
      // own state from real storage rather than being told what to paint.
      renderControl();
    }

    // The CTA sheet's own readout/button state. See the docstring above
    // for why this is gated on the busy edges rather than on `state`
    // alone.
    if (state === 'busy') {
      if (!overlayEl.hasAttribute('hidden')) {
        confirmBtn.disabled = true;
        const busyText = self.pwaStrings.interpolate(MAP_STRINGS['frame-readout-busy'], {
          pct: `${pct || 0}%`,
          mb: _formatBytes(bytes),
        });
        if (readoutEl.textContent !== busyText) readoutEl.textContent = busyText;
        // The banner swaps this area's recorded share for the run's own
        // live bytes, against the baseline cached at openFraming — no
        // further IndexedDB read. Coerced so the opening paint('busy', 0)
        // (no bytes yet) still counts as "a run is under way, nothing
        // landed" rather than falling back to the un-excluded baseline.
        _renderBudgetBanner(Number(bytes) || 0);
      }
    } else if (wasBusy && !overlayEl.hasAttribute('hidden')) {
      if (state === 'done') {
        // Hidden via an inline style, not the `hidden` attribute:
        // .map-frame-btn sets `display: inline-flex` at the same
        // specificity as the UA's `[hidden]` rule, and — unlike
        // #map-frame-overlay, which has its own `[hidden]` override in
        // map.css — nothing here would win that tie.
        confirmBtn.style.display = 'none';
        cancelBtn.textContent = MAP_STRINGS['action-close'];
        readoutEl.classList.remove('map-frame-readout--over-ceiling');
        readoutEl.textContent = self.pwaStrings.interpolate(MAP_STRINGS['frame-readout-done'], {
          mb: _formatBytes(bytes),
        });
      } else {
        // Error, or (defensively) a cancelled run settling while the
        // overlay is somehow still open — in practice a cancel has
        // always torn framing down (and hidden the overlay) before this
        // runs. pendingBbox/pendingBlob are untouched by an error (SNOW-
        // 568 keeps the frame up for a retry).
        //
        // A ground-locked selection (the common case away from the
        // default zoomed-out view — see _updateSelection's two-regimes
        // comment) hasn't moved during a run that locked pan/zoom, so
        // _updateSelection's own "bbox unchanged, nothing to repaint"
        // optimisation (SNOW-567) would otherwise return null here and
        // leave the busy state's disabled Download/percentage text
        // sitting on screen forever. Clearing lockedBbox first forces a
        // genuine repaint of the SAME bbox — the same trick openFraming
        // itself uses before its own first paint, for the same reason.
        lockedBbox = null;
        _updateReadout();
      }
    }
  }

  /**
   * (Re)probe the roundel against real storage.
   *
   * SNOW-634: this used to probe THIS area's own saved bbox against the
   * pinned cache's actual tile contents (`_probeDone`, now deleted) —
   * "done" meant "the custom area is fully cached". The roundel now opens
   * the downloads sheet rather than framing directly, and that sheet
   * covers every downloaded area, not just this one, so "done" now means
   * "the device holds at least one downloaded area" — a user with five
   * downloaded regions and no custom area used to read `idle`, which was
   * false; there IS something to manage offline. `basemapDownloadedAreas()`
   * is the same reader `map_downloads_manager.js` lists from, so the
   * roundel and the sheet can never disagree about what "done" means. An
   * orphaned bucket (SNOW-612 — a failed part-download with no completed
   * record) is excluded: it is not "you have this area offline", it is
   * leftover quota waiting to be reclaimed from the sheet.
   *
   * Neither connectivity nor the active basemap affect this any more —
   * there is no tile-template dependency left to re-probe on
   * `snowdesk:basemap-changed`, and nothing here to grey out offline (the
   * sheet lists and deletes offline, which is exactly when storage
   * pressure is felt) — so this control no longer listens for either.
   *
   * @returns {Promise<void>}
   */
  async function _renderControl() {
    if (runState === 'busy') return;
    const areas = await basemapDownloadedAreas();
    const kept = areas.filter((area) => !area.orphaned);
    const done = kept.length > 0;
    btn.dataset.downloadState = done ? 'done' : 'idle';
    // SNOW-645: this roundel means "the device holds at least one
    // download", which can span basemaps — unlike the region control,
    // whose single record always matches the active basemap. Only paint an
    // identity colour when every kept area agrees on one non-empty key;
    // mixed or unknown falls back to the neutral green fill, same as it
    // shows today, with the sheet one tap away to itemise which is which.
    const withKey = kept.filter((area) => area.basemapKey);
    const keys = new Set(withKey.map((area) => area.basemapKey));
    const basemapKey = keys.size === 1 && withKey.length === kept.length ? [...keys][0] : null;
    if (basemapKey) {
      btn.dataset.basemapKey = basemapKey;
    } else {
      delete btn.dataset.basemapKey;
    }
    const text = done
      ? MAP_STRINGS['custom-control-done']
      : MAP_STRINGS['custom-control-idle'];
    btn.setAttribute('aria-label', text);
    btn.title = text;
  }

  // SNOW-613: overlapping renders coalesce onto one trailing pass — see
  // `coalesceRenders`. Every trigger below calls this, not `_renderControl`.
  const renderControl = coalesceRenders(_renderControl);

  /**
   * Pixel padding (top/right/bottom/left) that fits MAP.fitBounds() to
   * the frame rect's current on-screen position, rather than the whole
   * map viewport — so re-opening at a saved area puts that area under
   * the frame, not just somewhere on screen.
   *
   * @returns {{top: number, right: number, bottom: number, left: number} | null}
   */
  function _framePadding() {
    if (!MAP || typeof MAP.getContainer !== 'function') return null;
    const container = MAP.getContainer();
    if (!container) return null;
    const mapRect = container.getBoundingClientRect();
    const frameRect = frameRectEl.getBoundingClientRect();
    if (!mapRect.width || !mapRect.height) return null;
    return {
      top: Math.max(0, frameRect.top - mapRect.top),
      right: Math.max(0, mapRect.right - frameRect.right),
      bottom: Math.max(0, mapRect.bottom - frameRect.bottom),
      left: Math.max(0, frameRect.left - mapRect.left),
    };
  }

  /**
   * The bbox a `width`x`height` box centred on (cx, cy) would cover.
   *
   * Coordinates are MAP-CONTAINER-relative, the space MapLibre's own
   * project/unproject work in — never viewport-relative. Mixing the two
   * is a live bug source: anything that shifts the map within the page
   * (a scroll, furniture appearing) moves a viewport coordinate without
   * moving a container one, and a cached value in the wrong space then
   * offsets the frame by the difference.
   *
   * Pure projection maths — reads no DOM and writes none, so the caller
   * can evaluate several candidate boxes without a single reflow.
   *
   * @param {number} cx - Centre x, in map-container pixels.
   * @param {number} cy - Centre y, in map-container pixels.
   * @param {number} width - Box width in pixels.
   * @param {number} height - Box height in pixels.
   * @returns {[number, number, number, number] | null} [west, south, east,
   *   north] in degrees, or null before the map is ready.
   */
  function _bboxForBox(cx, cy, width, height) {
    if (!MAP || typeof MAP.unproject !== 'function') return null;
    const halfW = width / 2;
    const halfH = height / 2;
    const corners = [
      [cx - halfW, cy - halfH],
      [cx + halfW, cy - halfH],
      [cx + halfW, cy + halfH],
      [cx - halfW, cy + halfH],
    ];
    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;
    for (const [vx, vy] of corners) {
      const point = MAP.unproject([vx, vy]);
      if (point.lng < west) west = point.lng;
      if (point.lng > east) east = point.lng;
      if (point.lat < south) south = point.lat;
      if (point.lat > north) north = point.lat;
    }
    return [west, south, east, north];
  }

  /**
   * The frame's full-size box — the centre of .map-frame-area and its
   * content box, i.e. the gutter-inset rectangle the CSS allows. Read from
   * the area rather than the frame itself so the frame's own (possibly
   * capped) size never feeds back into the next measurement.
   *
   * @returns {{cx: number, cy: number, width: number, height: number} | null}
   */
  function _naturalFrameBox() {
    if (!frameAreaEl) return null;
    // Cached for the framing session. This is the only DOM *read* on the
    // per-frame path, and it forces a synchronous layout — which is what
    // pushed the geometry write onto a later animation frame, leaving the
    // frame trailing the canvas it is supposed to be glued to. The gutter
    // box only changes when the viewport does, so it is measured on open
    // and on resize (see _invalidateNaturalFrameBox) and read from here
    // otherwise.
    if (naturalBoxCache) return naturalBoxCache;
    if (!MAP || typeof MAP.getContainer !== 'function') return null;
    const container = MAP.getContainer();
    if (!container) return null;
    // Both rects are viewport-relative; the difference converts the gutter
    // box into the map-container space every other function here works in.
    // Cached in THAT space deliberately — a viewport-space cache silently
    // desyncs the moment anything shifts the map within the page.
    const mapRect = container.getBoundingClientRect();
    const rect = frameAreaEl.getBoundingClientRect();
    const style = getComputedStyle(frameAreaEl);
    const left = rect.left + parseFloat(style.paddingLeft || '0') - mapRect.left;
    const right = rect.right - parseFloat(style.paddingRight || '0') - mapRect.left;
    const top = rect.top + parseFloat(style.paddingTop || '0') - mapRect.top;
    const bottom = rect.bottom - parseFloat(style.paddingBottom || '0') - mapRect.top;
    const width = Math.max(0, right - left);
    const height = Math.max(0, bottom - top);
    if (!width || !height) return null;
    naturalBoxCache = { cx: (left + right) / 2, cy: (top + bottom) / 2, width, height };
    return naturalBoxCache;
  }

  // The memoised _naturalFrameBox measurement, or null when it needs
  // re-taking.
  let naturalBoxCache = null;

  // Watches .map-frame-area for any size change and drops the cache.
  //
  // A ResizeObserver rather than a list of known causes: the area is a
  // flex child sized by what is left over after the instruction bar and
  // the CTA sheet, so it moves whenever THEY reflow — and the readout
  // inside the sheet changes text as the user pans, which is enough to
  // change the sheet's height and shift the area's centre. That produced a
  // frame offset vertically by ~18px against an otherwise perfectly
  // tracked selection. Enumerating the causes is how that bug happens
  // again; observing the element is not.
  let frameAreaObserver = null;

  /**
   * Drop the cached gutter box so the next read re-measures it.
   *
   * @returns {void}
   */
  function _invalidateNaturalFrameBox() {
    naturalBoxCache = null;
  }

  // Never shrink the frame below this fraction of its natural size. A
  // frame smaller than this is not aimable, and reaching it means the
  // ceiling cannot be met at this zoom at all — the over_ceiling backstop
  // (a red readout and a disabled Download) then still applies, exactly as
  // it did before the frame could shrink.
  const MIN_FRAME_SCALE = 0.08;

  // SNOW-567: while the ceiling caps the area, the selection holds a fixed
  // ground SIZE — {lon, merc}, a longitude span and a Web Mercator y span —
  // and is re-centred on whatever the frame is over. Null when the natural
  // frame fits under the ceiling and no cap is needed.
  //
  // Holding the size rather than the whole box is what makes the frame
  // behave like a layer on the map. A viewport-anchored frame holding a
  // fixed maximum ground area has a screen size proportional to 2**zoom
  // (the same reason a scale bar changes length), so it MUST resize as you
  // zoom; deriving it from a fixed footprint instead makes the frame a
  // projection of a fixed piece of ground, so it tracks the terrain and
  // needs no recomputation at all while zooming — an unchanged box covers
  // an unchanged set of tiles, so even the MB readout holds still.
  let lockedSize = null;

  // The last box that produced a readout, so an unchanged one can skip the
  // tile math. Purely a memo of lockedSize projected onto the current
  // frame centre — never the source of truth for the selection's size.
  let lockedBbox = null;

  /**
   * The on-screen box (viewport pixels) that `bbox` currently projects to.
   *
   * All FOUR corners are projected and the axis-aligned bounds taken over
   * the lot, mirroring _bboxForBox's own handling of the reverse
   * direction: MapLibre supports rotation, so a rotated view turns a
   * lat/lon rectangle into a non-axis-aligned quad on screen.
   *
   * @param {[number, number, number, number]} bbox
   * @returns {{cx: number, cy: number, width: number, height: number} | null}
   */
  function _screenBoxForBBox(bbox) {
    if (!MAP || typeof MAP.project !== 'function') return null;
    const [west, south, east, north] = bbox;
    let left = Infinity;
    let top = Infinity;
    let right = -Infinity;
    let bottom = -Infinity;
    for (const corner of [[west, north], [east, north], [east, south], [west, south]]) {
      const point = MAP.project(corner);
      left = Math.min(left, point.x);
      right = Math.max(right, point.x);
      top = Math.min(top, point.y);
      bottom = Math.max(bottom, point.y);
    }
    return {
      cx: (left + right) / 2,
      cy: (top + bottom) / 2,
      width: right - left,
      height: bottom - top,
    };
  }

  /**
   * Draw the frame at `screenBox`, expressed relative to `natural` (the
   * gutter-inset box the stylesheet centres it in).
   *
   * The offset is a transform rather than a position change so the element
   * stays flex-centred in .map-frame-area and needs no layout to move —
   * which matters because the frame carries the dim mask as a 9999px
   * box-shadow spread, and moving it by layout would repaint that mask.
   * Writes are skipped when nothing changed.
   *
   * @param {{cx: number, cy: number, width: number, height: number}} screenBox
   * @param {{cx: number, cy: number, width: number, height: number}} natural
   * @returns {void}
   */
  function _placeFrame(screenBox, natural) {
    // Sub-pixel, deliberately. The canvas underneath moves at sub-pixel
    // precision, so rounding the frame to whole pixels makes it snap
    // against a smoothly-moving map — a stutter of up to a pixel per frame
    // that reads as judder however well the geometry itself tracks.
    const width = `${screenBox.width}px`;
    const height = `${screenBox.height}px`;
    const dx = screenBox.cx - natural.cx;
    const dy = screenBox.cy - natural.cy;
    const transform = dx === 0 && dy === 0 ? '' : `translate3d(${dx}px, ${dy}px, 0)`;
    if (frameRectEl.style.width !== width) frameRectEl.style.width = width;
    if (frameRectEl.style.height !== height) frameRectEl.style.height = height;
    if (frameRectEl.style.transform !== transform) frameRectEl.style.transform = transform;
  }

  /**
   * Hand the frame's geometry back to the stylesheet — it fills its area
   * again, and a viewport resize keeps working.
   *
   * @returns {void}
   */
  function _releaseFrame() {
    frameRectEl.style.removeProperty('width');
    frameRectEl.style.removeProperty('height');
    frameRectEl.style.removeProperty('transform');
  }

  /**
   * `bbox`'s size as a ground footprint that is independent of zoom: a
   * longitude span, and a span in Web Mercator y (NOT degrees of latitude,
   * which are not linear in the projection).
   *
   * @param {[number, number, number, number]} bbox
   * @returns {{lon: number, merc: number}}
   */
  function _groundSizeOf(bbox) {
    const [west, south, east, north] = bbox;
    return {
      lon: east - west,
      merc:
        maplibregl.MercatorCoordinate.fromLngLat({ lng: west, lat: south }).y -
        maplibregl.MercatorCoordinate.fromLngLat({ lng: west, lat: north }).y,
    };
  }

  /**
   * The bbox of ground size `size` centred on whatever the frame is over
   * right now.
   *
   * Holding the SIZE and re-deriving the centre each time — rather than
   * translating the previous bbox — is what keeps this stable: there is no
   * project/unproject round trip whose rounding could accumulate over the
   * hundreds of updates a long gesture produces.
   *
   * @param {{lon: number, merc: number}} size
   * @param {{cx: number, cy: number}} frameCentre - Map-container pixels.
   * @returns {[number, number, number, number] | null}
   */
  function _bboxOfSizeUnderFrame(size, frameCentre) {
    if (!MAP || typeof MAP.unproject !== 'function') return null;
    const centre = MAP.unproject([frameCentre.cx, frameCentre.cy]);
    const centreMerc = maplibregl.MercatorCoordinate.fromLngLat(centre);
    const halfMerc = size.merc / 2;
    // Clamp to the projection's valid range: a box hanging off the top or
    // bottom of the Mercator world has no latitude to convert back to.
    const northY = Math.max(0, centreMerc.y - halfMerc);
    const southY = Math.min(1, centreMerc.y + halfMerc);
    const north = new maplibregl.MercatorCoordinate(centreMerc.x, northY, 0).toLngLat().lat;
    const south = new maplibregl.MercatorCoordinate(centreMerc.x, southY, 0).toLngLat().lat;
    return [centre.lng - size.lon / 2, south, centre.lng + size.lon / 2, north];
  }

  /**
   * Bring the frame and the selection up to date with the map, and return
   * the selection — or null when it is unchanged and there is nothing for
   * the caller to repaint.
   *
   * Two cases:
   *
   * - **Under the ceiling.** No cap is needed. The frame fills its area
   *   under stylesheet control, and the selection is whatever ground it
   *   covers.
   * - **Capped.** The selection holds a fixed ground SIZE — the largest
   *   that fits the ceiling — centred on whatever the frame is over. The
   *   frame is then drawn where that box projects to.
   *
   * There is deliberately no pan-versus-zoom branch. Framing re-anchors
   * zoom to the frame's own centre (_anchorZoomOnTheFrame), so a zoom
   * cannot change the ground under the frame: re-deriving the selection
   * from that centre every update is a no-op through a whole zoom
   * gesture — same box, same tiles, an estimate that does not so much as
   * flicker — and moves it only when a pan (or the map clamping against
   * maxBounds) genuinely puts different ground under the frame. Trying to
   * tell the two gestures apart instead is what previously left the
   * selection yanked back mid-zoom, and no float comparison of zoom and
   * centre survived contact with a real inertial gesture.
   *
   * The cap threshold comes from pwaBasemapDownloadCore.budgetScaleForBBox
   * in one step — see that function for why a search against buildBlob's
   * own (floored, hence step-shaped) tile count made the frame shimmer
   * while panning (SNOW-566). Locking and releasing share that one
   * threshold, and at it the locked box and the natural frame coincide, so
   * crossing it is continuous rather than a jump.
   *
   * @returns {{bbox: [number, number, number, number], blob: Object} | null}
   *   The selection, or null when it is unchanged (or the map is not ready)
   *   and there is nothing for the caller to repaint.
   */
  function _updateSelection() {
    const core = self.pwaBasemapDownloadCore;
    const natural = _naturalFrameBox();
    if (!core || !natural) return null;

    const naturalBbox = _bboxForBox(natural.cx, natural.cy, natural.width, natural.height);
    if (!naturalBbox) return null;
    const [minZ, maxZ] = core.MICRO_BAND;
    const scale = core.budgetScaleForBBox(naturalBbox, minZ, maxZ);

    if (scale >= 1) {
      lockedSize = null;
      lockedBbox = null;
      _releaseFrame();
      return { bbox: naturalBbox, blob: core.buildBlob(naturalBbox, minZ, maxZ) };
    }

    if (lockedSize === null) {
      // Engaging the cap: adopt the largest ground footprint that fits.
      const capped = _bboxForBox(
        natural.cx,
        natural.cy,
        natural.width * Math.max(MIN_FRAME_SCALE, scale),
        natural.height * Math.max(MIN_FRAME_SCALE, scale),
      );
      if (!capped) return null;
      lockedSize = _groundSizeOf(capped);
    }

    const bbox = _bboxOfSizeUnderFrame(lockedSize, natural);
    if (!bbox) return null;
    const screenBox = _screenBoxForBBox(bbox);
    if (screenBox) _placeFrame(screenBox, natural);
    // Unchanged through a zoom, which is the common case — say so, and the
    // caller skips the tile math and leaves the readout alone.
    if (_bboxesEqual(bbox, lockedBbox)) return null;
    lockedBbox = bbox;
    return { bbox, blob: core.buildBlob(bbox, minZ, maxZ) };
  }

  /**
   * Recompute pendingBbox/pendingBlob from the frame's current on-screen
   * position and paint the readout. Cheap, local arithmetic
   * (pwaBasemapDownloadCore.buildBlob) — no network round-trip on any
   * frame of a pan or zoom, which is the whole point of computing this
   * client-side.
   *
   * @returns {void}
   */
  function _updateReadout() {
    const core = self.pwaBasemapDownloadCore;
    // Placing the frame and measuring it are the same step: the update
    // above already produced the box it settled on, so reuse its result
    // rather than re-measuring the DOM it just wrote to (which would read
    // back the pre-layout size on the same frame). A null means the
    // selection is unchanged — a zoom against a ground-locked area — and
    // there is nothing here to repaint.
    const fitted = core ? _updateSelection() : null;
    if (!core || !fitted) return;
    pendingBbox = fitted.bbox;
    pendingBlob = fitted.blob;
    const overCeiling = pendingBlob.over_ceiling;
    const text = overCeiling
      ? self.pwaStrings.interpolate(MAP_STRINGS['frame-over-ceiling'], {
          mb: core.DOWNLOAD_CEILING_MB,
        })
      : self.pwaStrings.interpolate(MAP_STRINGS['frame-up-to'], { mb: pendingBlob.mb });
    // Same no-op guard as the frame's own size write above: the estimate
    // holds still across most 'move's, and rewriting identical text still
    // costs a layout of the CTA bar.
    if (readoutEl.textContent !== text) readoutEl.textContent = text;
    readoutEl.classList.toggle('map-frame-readout--over-ceiling', overCeiling);
    // Offline-integrity: never let the CTA's own Download button start a
    // run while offline, even if the roundel that opened framing read
    // idle at the time (a connectivity change mid-session).
    confirmBtn.disabled = overCeiling || !navigator.onLine;
  }

  /**
   * Convert a bbox [west, south, east, north] to MapLibre's fitBounds
   * shape [[west, south], [east, north]].
   *
   * @param {[number, number, number, number]} bbox
   * @returns {[[number, number], [number, number]]}
   */
  function _boundsFromBBox(bbox) {
    const [west, south, east, north] = bbox;
    return [[west, south], [east, north]];
  }

  /**
   * Make every zoom gesture pivot on the frame instead of the pointer, for
   * as long as framing is open.
   *
   * MapLibre anchors a wheel or pinch zoom at the cursor: the ground under
   * the pointer stays put and everything else moves around it. With a
   * ground-locked selection that is visibly wrong — zoom out with the
   * pointer off to one side and the frame, still correctly glued to its
   * terrain, sails off towards that corner, then has to travel back when
   * you zoom in again.
   *
   * Two steps, because "the frame" is not "the map centre": the frame area
   * is the space left between the instruction bar and the CTA sheet, so
   * its centre sits above the viewport's.
   *
   *   1. ``setPadding`` tells the map that its centre is the frame's
   *      centre. Everything downstream — ``getCenter``, ``fitBounds``, and
   *      the centre-anchored zoom below — then works to the frame.
   *   2. The zoom handlers are re-enabled with ``around: 'center'``.
   *      ``scrollZoom.enable`` no-ops when the handler is already enabled
   *      (which it is, by default), hence the disable first — without it
   *      the option is accepted and silently ignored.
   *
   * The pay-off is more than cosmetic: a centre-anchored zoom leaves
   * ``getCenter()`` untouched, so "the centre moved" now means a pan and
   * nothing else, and the locked bbox stays centred under the frame with
   * no offset to apply.
   *
   * @returns {void}
   */
  function _anchorZoomOnTheFrame() {
    const natural = _naturalFrameBox();
    const container = MAP.getContainer();
    if (natural && container) {
      MAP.setPadding({
        top: Math.max(0, natural.cy - natural.height / 2),
        bottom: Math.max(0, container.clientHeight - (natural.cy + natural.height / 2)),
        left: Math.max(0, natural.cx - natural.width / 2),
        right: Math.max(0, container.clientWidth - (natural.cx + natural.width / 2)),
      });
    }
    MAP.scrollZoom.disable();
    MAP.scrollZoom.enable({ around: 'center' });
    MAP.touchZoomRotate.enable({ around: 'center' });
    // Double-click zoom has no centre-anchored mode, and it pivots on the
    // click point — the same defect by another route.
    MAP.doubleClickZoom.disable();
  }

  /**
   * Undo _anchorZoomOnTheFrame: hand the map back its own centre and its
   * default, pointer-anchored zoom handlers.
   *
   * @returns {void}
   */
  function _releaseZoomAnchor() {
    if (!MAP) return;
    MAP.setPadding({ top: 0, bottom: 0, left: 0, right: 0 });
    MAP.scrollZoom.disable();
    MAP.scrollZoom.enable();
    MAP.touchZoomRotate.enable();
    MAP.doubleClickZoom.enable();
    // SNOW-632: also the safety net for _lockMapForRun's dragPan.disable()
    // — a Cancel click mid-run tears framing (and this) down synchronously,
    // well before the run's own settle reaches _unlockMapAfterRun, so
    // without this dragPan would stay frozen for however long the
    // cancellation round trip takes. Idempotent the rest of the time:
    // dragPan is never disabled outside a run, so this is a harmless no-op
    // on every other call site.
    MAP.dragPan.enable();
  }

  /**
   * Freeze every map gesture — pan and all three zoom handlers — for the
   * duration of a confirmed run (SNOW-632, requirement 3). Framing itself
   * leaves dragPan enabled (panning is how the user re-aims a ground-
   * locked selection — see _updateSelection's header comment); this is
   * stricter and applies only while a download is actually in flight,
   * because a run has already committed to a specific tile set and must
   * not have the ground shift under it mid-fetch.
   *
   * Called from paintRun on the busy transition edge, never directly —
   * see that function's docstring.
   *
   * @returns {void}
   */
  function _lockMapForRun() {
    if (!MAP) return;
    MAP.dragPan.disable();
    MAP.scrollZoom.disable();
    MAP.touchZoomRotate.disable();
    MAP.doubleClickZoom.disable();
  }

  /**
   * Undo _lockMapForRun once a run settles. Re-anchors to the frame
   * (_anchorZoomOnTheFrame) only if framing is STILL open — a run that
   * settles after the user has already cancelled finds the overlay
   * hidden and _teardownFraming/_releaseZoomAnchor already run, and
   * re-anchoring here would resurrect the padding and handlers that
   * teardown just cleared. dragPan is re-enabled unconditionally either
   * way — _releaseZoomAnchor also does this defensively (see its own
   * comment), but the settle path is this function's own responsibility
   * whenever framing is still up.
   *
   * @returns {void}
   */
  function _unlockMapAfterRun() {
    if (!MAP) return;
    MAP.dragPan.enable();
    if (!overlayEl.hasAttribute('hidden')) {
      _anchorZoomOnTheFrame();
    }
  }

  /**
   * Render #map-frame-instruction as the standing download total against
   * the budget — "39 MB / 500 MB downloaded" — from the cached
   * `bannerBaselineBytes`/`bannerBudgetBytes`, optionally layering a live
   * run's own progress on top.
   *
   * @param {number} [liveBytes] Bytes landed by a run in progress. The
   *   area's own previously-recorded share is swapped out for this figure
   *   rather than added to it — see `bannerOwnAreaBytes`. Omitted outside
   *   a run, when the cached baseline is already the whole truth.
   * @returns {void}
   */
  function _renderBudgetBanner(liveBytes) {
    // SNOW-632 review finding: bannerBudgetBytes starts at 0, and
    // openFraming's _refreshBudgetBanner() read is unawaited — painting
    // before it resolves would show a false "X MB / 0 MB" denominator.
    // Leave whatever instruction text is already there until the real
    // budget is known.
    if (!instructionEl || !bannerBudgetKnown) return;
    // `undefined` (no run) leaves the baseline untouched; a run swaps this
    // area's recorded share for what it has landed so far. Floored at 0
    // because the two figures come from different reads and a stale
    // baseline must never render a negative total.
    const usedBytes =
      liveBytes === undefined
        ? bannerBaselineBytes
        : Math.max(0, bannerBaselineBytes - bannerOwnAreaBytes) + (Number(liveBytes) || 0);
    instructionEl.textContent = self.pwaStrings.interpolate(
      MAP_STRINGS['frame-budget-banner'],
      { used: _formatBytes(usedBytes), budget: _formatBytes(bannerBudgetBytes) },
    );
  }

  /**
   * (Re)read the standing download total and budget from IndexedDB, cache
   * them, and repaint the banner.
   *
   * Called exactly twice a run — once from openFraming, once more from
   * `finish` once the run has settled — never from the progress-tick path
   * (paintRun/_renderBudgetBanner), which repaints the SAME cached numbers
   * plus the run's own live bytes. Each read here is two `meta:app` round
   * trips (basemapDownloadedAreas covers `basemap.regions` AND
   * `basemap.customAreas`) plus a third for the budget row; a live run
   * repaints its percentage roughly once per tile, so doing this on every
   * tick would be dozens of IndexedDB reads a second for no visible gain
   * — the banner already tracks the run via `liveBytes`.
   *
   * Best-effort and async: never blocks the overlay's own appearance on
   * IndexedDB, and a failed read just leaves the previous figures in
   * place.
   *
   * @returns {Promise<void>}
   */
  async function _refreshBudgetBanner() {
    if (!instructionEl) return;
    try {
      const [areas, budgetBytes] = await Promise.all([
        basemapDownloadedAreas(),
        basemapDownloadBudgetBytes(),
      ]);
      bannerBaselineBytes = areas.reduce((sum, area) => sum + (Number(area.bytes) || 0), 0);
      // Read alongside the total, from the SAME snapshot, so the two can
      // never disagree about what this area currently contributes.
      //
      // SNOW-635: keyed off THIS RUN's own generated id
      // (`currentRunAreaId`), not the legacy `CUSTOM_AREA_ID` — every
      // confirm downloads a NEW area now, so a run's id never has an
      // existing record to exclude until it has actually recorded one.
      // Called from openFraming, before any run this session has minted an
      // id, `currentRunAreaId` is null and this always misses — resolving
      // to 0 bytes, correctly: a session that has confirmed nothing yet
      // owns no bytes to exclude from the baseline.
      const ownArea = currentRunAreaId
        ? areas.find((area) => area.id === currentRunAreaId)
        : null;
      bannerOwnAreaBytes = ownArea ? Number(ownArea.bytes) || 0 : 0;
      bannerBudgetBytes = budgetBytes;
      bannerBudgetKnown = true;
    } catch (_e) {
      // Best-effort — the banner keeps showing its previous figures (or,
      // if this is the first read and it fails, the pre-existing
      // instruction text — bannerBudgetKnown stays false).
    }
    _renderBudgetBanner();
  }

  /**
   * Open the framing overlay: reveal it, and start tracking the live
   * readout on every 'move'.
   *
   * SNOW-635: no longer moves the map to a saved area first — with any
   * number of custom areas possibly on disk, picking one to jump to would
   * be arbitrary. Framing always starts from wherever the map currently
   * sits, exactly like the very first time this control is ever opened.
   *
   * @returns {void}
   */
  function openFraming() {
    if (!MAP) return;
    overlayEl.removeAttribute('hidden');
    // SNOW-632: undo whatever the PREVIOUS session's completed run left on
    // the CTA bar (paintRun's 'done' branch hides Download and relabels
    // Cancel to Close) — without this a Close button, and no Download at
    // all, would leak into a framing session that has not downloaded
    // anything yet. _updateReadout below repaints the readout text and
    // confirmBtn's disabled state from the CURRENT selection, so nothing
    // further is needed for those.
    confirmBtn.style.removeProperty('display');
    cancelBtn.textContent = CANCEL_LABEL_DEFAULT;
    // SNOW-635: a fresh session has downloaded nothing (yet) of its own —
    // see _refreshBudgetBanner's own comment for why this is what makes
    // its "own area" lookup correctly resolve to 0 bytes below.
    currentRunAreaId = null;
    // The standing download-budget banner — read once here (never per
    // progress tick; see _refreshBudgetBanner's own comment) and rendered
    // as soon as the numbers arrive, without making the overlay's own
    // appearance wait on IndexedDB.
    _refreshBudgetBanner();
    // Strip the map furniture that has nothing to do with the area being
    // framed — the region readout names a region the download does not
    // follow, and the date ribbon/scrubber describe a day. Both would
    // otherwise sit lit inside the cutout and read as part of the
    // selection. Driven by a body class rather than per-element hidden
    // attributes so each owner module keeps sole control of its own
    // visibility state (see .map-framing in static/css/map.css).
    document.body.classList.add('map-framing');
    // Drop any cap left on the frame by the previous open, so the frame
    // measures the natural gutter rather than a shrunken frame from a zoom
    // level the map is no longer at. _updateReadout re-applies the cap for
    // the view we actually land on. The ground lock goes with it: a fresh
    // open aims from wherever the map is now, never from the area the last
    // one happened to leave locked.
    lockedSize = null;
    lockedBbox = null;
    _invalidateNaturalFrameBox();
    _releaseFrame();
    _anchorZoomOnTheFrame();
    _updateReadout();
    // Synchronously, on every 'move' — NOT deferred to the next animation
    // frame. MapLibre fires 'move' from inside its own render loop, so a
    // style write made here is composited with the very frame that moved
    // the canvas; scheduling it instead put the frame one frame behind the
    // map, which measured up to 12px of positional lag and 15px of size lag
    // mid-gesture and read as judder (SNOW-567). Settled state was correct
    // throughout, which is why only a test that samples DURING the
    // animation catches it.
    //
    // Deferring was originally there to avoid re-measuring the DOM on every
    // event; that cost is gone now the gutter box is cached, leaving this
    // path a handful of projections and a style write with no layout read
    // at all.
    moveHandler = () => {
      // SNOW-632, requirement 2: the map is locked during a run (see
      // _lockMapForRun) so this should never fire from a user gesture
      // while busy — but guard it anyway rather than trust that no
      // programmatic 'move' (e.g. _unlockMapAfterRun's own re-anchor) can
      // land here first and re-enable Download out from under a run still
      // in flight.
      if (runState === 'busy') return;
      _updateReadout();
    };
    MAP.on('move', moveHandler);
    if (typeof ResizeObserver === 'function') {
      frameAreaObserver = new ResizeObserver(() => {
        _invalidateNaturalFrameBox();
        // Dropping the cache is not enough: the frame is only ever placed
        // in response to a map 'move', so after a reflow with no movement
        // it would keep the offset it was given against the OLD gutter box
        // until the user next touched the map. Re-place it here — geometry
        // only, deliberately not the estimate, so this cannot feed back
        // into the readout that changed the sheet's height in the first
        // place.
        if (!moveHandler || !lockedBbox) return;
        const natural = _naturalFrameBox();
        const screenBox = natural ? _screenBoxForBBox(lockedBbox) : null;
        if (natural && screenBox) _placeFrame(screenBox, natural);
      });
      frameAreaObserver.observe(frameAreaEl);
    } else {
      // No ResizeObserver: fall back to the map's own resize event, which
      // covers the viewport case but not a sheet reflow.
      resizeHandler = () => _invalidateNaturalFrameBox();
      MAP.on('resize', resizeHandler);
    }
  }

  /**
   * Stop tracking the frame (removes the 'move' listener) and clear the
   * pending bbox/blob. Does NOT hide the overlay itself — that is the
   * Cancel/Close button's own job, via overlays.js's shared dismiss
   * handler, which has already hidden the overlay before dispatching
   * overlay:dismissed by the time this runs (SNOW-632: since a SUCCESSFUL
   * run no longer closes the overlay itself — see paintRun's 'done'
   * branch — dismissal is now the ONLY path that ever calls this).
   *
   * @returns {void}
   */
  function _teardownFraming() {
    // Restores the furniture openFraming stripped.
    document.body.classList.remove('map-framing');
    if (moveHandler && MAP) {
      MAP.off('move', moveHandler);
    }
    if (resizeHandler && MAP) {
      MAP.off('resize', resizeHandler);
    }
    if (frameAreaObserver) {
      frameAreaObserver.disconnect();
      frameAreaObserver = null;
    }
    moveHandler = null;
    resizeHandler = null;
    _releaseZoomAnchor();
    _invalidateNaturalFrameBox();
    lockedSize = null;
    lockedBbox = null;
    pendingBbox = null;
    pendingBlob = null;
  }

  /**
   * Whether two bboxes describe the same area, to a tolerance tight
   * enough to absorb floating-point noise from repeated
   * unproject()/fitBounds() round trips but loose enough that "the user
   * didn't touch the map" always reads as unchanged.
   *
   * @param {number[] | null | undefined} a
   * @param {number[] | null | undefined} b
   * @returns {boolean}
   */
  function _bboxesEqual(a, b) {
    if (!a || !b) return false;
    for (let i = 0; i < 4; i++) {
      if (Math.abs(a[i] - b[i]) > 1e-6) return false;
    }
    return true;
  }

  /**
   * Run the confirmed download: assemble the URL list and hand it to the
   * SW's warm-cache handler — mirrors mapDownloadControlInit's
   * handleClick, sharing its assembleBasemapDownloadFeedURLs helper.
   *
   * SNOW-635: every confirm mints a FRESH area id (`generateCustomAreaId`)
   * and downloads it as a new, independent area — there is no longer a
   * single saved area a moved frame or a changed basemap could replace, so
   * this no longer runs a `beforeWarm` eviction of its own before warming;
   * only the runner's own budget-driven eviction (of OTHER areas,
   * confirmed first) can still remove anything.
   *
   * SNOW-632: a run's outcome no longer decides whether the overlay
   * closes — only the user's own Cancel/Close click does that (see
   * paintRun's 'done' branch and the overlay:dismissed listener). A
   * SUCCESSFUL run instead repaints the CTA in place: the readout becomes
   * "23.4 MB downloaded", Download hides, and Cancel relabels to Close. A
   * CANCELLED run (the user dismissed while this was in flight) is
   * neither success nor failure — see the `cancelled` branch below.
   *
   * @returns {Promise<void>}
   */
  async function handleConfirm() {
    if (!pendingBbox || !pendingBlob || pendingBlob.over_ceiling) return;
    // SNOW-568: the overlay now stays open across a failed run (so the
    // framed area survives for a retry), and it was always open during a
    // running one — so the Download button, unlike the roundel, needs its
    // own re-entrancy guard.
    if (runState === 'busy') return;
    // Offline-integrity: never start a download offline, even if a race
    // left the button enabled at the moment of the click.
    if (!navigator.onLine) {
      paintRun('offline');
      return;
    }
    const blob = pendingBlob;
    const bbox = pendingBbox;

    const core = self.pwaBasemapDownloadCore;
    // SNOW-635: a fresh id per run — never CUSTOM_AREA_ID — is what lets
    // more than one custom area exist at once; see this function's own
    // docstring. `runPinnedDownload` re-reads the core itself and fails
    // the run properly if it is missing; this only needs it for the area
    // id, so a missing core simply yields none and the runner handles the
    // rest.
    const areaId = core ? core.generateCustomAreaId() : '';
    currentRunAreaId = areaId;

    await runPinnedDownload({
      areaId: areaId,
      mb: blob.mb,
      // This control's roundel carries no size, so the shared runner's
      // (state, pct, bytes) triple is passed straight through — SNOW-632:
      // `bytes` is what drives the CTA's live "42% · 6.1 MB" readout.
      paint: (nextState, pct, bytes) => paintRun(nextState, pct, bytes),
      loadBlob: () => blob,
      finish: async (result, runBlob, { core, progressFill, template, basemapKey }) => {
        // SNOW-632: a cancelled run is neither success nor failure — the
        // user asked it to stop, not for it to fail — so this is checked
        // BEFORE `ok`. A cancelled run always has `failed === 0` (nothing
        // the cancel skipped was ever attempted), which would otherwise
        // read as a clean success; see basemap_download_runner.js's
        // `finish` docstring for why the check has to come first.
        const cancelled = !!(result && result.cancelled);
        // "done" requires at least one success and no failures; a partial,
        // vacuous, or absent result must not claim the area is downloaded.
        //
        // SNOW-568: a run that didn't succeed now says so. It used to fall
        // back to 'idle' silently, indistinguishable from never having
        // clicked Download.
        const ok = !cancelled && !!(result && result.ok > 0 && result.failed === 0);
        if (cancelled) {
          // Overlay teardown already ran synchronously when the user
          // dismissed (see the overlay:dismissed listener below) — this
          // just settles the roundel. Partial tiles may have landed
          // before the worker honoured the cancel, so this can never
          // claim 'done': the probe checks the WHOLE saved area's tile
          // set, and painting done here would claim more than is true.
          await progressFill.finish(false);
          paintRun(navigator.onLine ? 'idle' : 'offline');
        } else if (ok) {
          // SNOW-632: `bytes` is this run's OWN reported total, recorded
          // outright — never accumulated onto anything, and never
          // re-measured from the bucket (a live tile response carries no
          // `Content-Length` under gzip, so a bucket measurement reads ~0
          // in production; see the decision doc for the curl evidence).
          // SNOW-635: this is always a brand-new bucket under a fresh id
          // (no prior run ever shared it), so the run's own total is
          // trivially the bucket's whole total — there is no prior-basemap
          // or prior-bbox leftover to have cleared first, unlike the
          // single shared area this replaces.
          const area = {
            id: areaId,
            ordinal: await _nextCustomAreaOrdinal(),
            bbox: bbox,
            band: runBlob.band,
            centre_tile: runBlob.centre_tile,
            template: template,
            basemapKey: basemapKey || null,
            bytes: Number(result.bytes) || 0,
            savedAt: new Date().toISOString(),
          };
          await _appendCustomArea(area);
          // SNOW-569's pulse still plays on success; SNOW-632 removed the
          // overlay-close that used to precede it (SNOW-568 already left
          // it open on failure, and requirement 5 now does the same for a
          // success — see paintRun's 'done' branch for what replaces it).
          await progressFill.finish(true);
          paintRun('done', undefined, area.bytes);
        } else {
          await progressFill.finish(false);
          paintRun('error');
          // A null result means there was no active worker at all — nothing
          // ran and nothing was cached, which is still a failed download
          // from the user's point of view, just one with no reason to
          // report beyond the generic line.
          revealBasemapDownloadError(result ? result.reason : null);
        }
        // SNOW-505/522: the warm-cache run has just warmed the shell +
        // pinned basemap caches; re-probe every sync dot against real
        // cache state, mirroring mapDownloadControlInit's own post-run
        // refresh — the layers menu is a live cache-state dashboard. A
        // cancelled run needs this exactly as much as a completed one:
        // partial tiles may have landed before the worker honoured the
        // cancel, and stale dots would misreport them.
        window.pwaLayerSyncStatus?.refresh();
        // SNOW-570/SNOW-587: and the cached-tiles overlay, so tiles that
        // just finished downloading appear immediately rather than at the
        // next basemap swap or reload.
        window.pwaDownloadedOverlay?.refresh();
        // SNOW-632: the standing total on disk has changed (a success
        // adds this run's bytes; a cancel or a partial failure may have
        // landed some tiles too), so the banner's cached baseline is
        // stale — re-read it once, rather than trying to derive the new
        // figure from what `finish` already knows.
        _refreshBudgetBanner();
      },
    });
  }

  // SNOW-634: the roundel now opens the downloads sheet unconditionally —
  // no busy guard (framing hides #map-controls-br, the roundel's own
  // container, for as long as it is open, so this can never be clicked
  // mid-run anyway) and no offline guard (the sheet lists and deletes
  // offline, which is exactly when storage pressure is felt). Confirming
  // a NEW download is still gated on connectivity, both by the sheet's own
  // add-trigger and by this overlay's own Download button.
  btn.addEventListener('click', () => {
    window.pwaDownloadsManager?.open();
  });

  confirmBtn.addEventListener('click', () => handleConfirm());

  // Cancel/Close both go through overlays.js's shared [data-action="dismiss"]
  // handler (it already hid the overlay and dispatched this event by the
  // time this listener runs) — teardown-only here, matching this IIFE's
  // header comment.
  document.addEventListener('overlay:dismissed', (e) => {
    if (e.detail && e.detail.overlay === overlayEl) {
      // SNOW-632: capture BEFORE teardown — a busy run is this control's
      // own sign that it is in flight, and teardown below does not touch
      // it (only paintRun, from the run's own eventual `finish`, does).
      const wasBusy = runState === 'busy';
      _teardownFraming();
      // SNOW-568: cancelling framing abandons the attempt the toast was
      // reporting on. Leaving it up would also strand it at the offset
      // that cleared the CTA sheet which has just gone away.
      clearBasemapDownloadError();
      // SNOW-632: a Cancel click while a run is in flight asks the worker
      // to stop dispatching further URLs. A safe no-op otherwise — this
      // is the SAME dismiss idiom an idle Cancel (nothing running) or a
      // post-completion Close (already settled) also go through, and
      // `pwaWarmCacheCancel` is itself a no-op with no run in flight — but
      // the check avoids posting to a service worker that isn't waiting
      // for anything.
      if (wasBusy) {
        window.pwaWarmCacheCancel?.();
      }
    }
  });

  // SNOW-634: unlike mapDownloadControlInit's own copy of this listener,
  // this control no longer listens for 'snowdesk:basemap-changed' — the
  // roundel's "done" no longer depends on the active basemap's tile
  // template (there is no tile-cache probe left to re-run; see
  // _renderControl's own docstring).
  //
  // Offline-integrity: re-validate the open CTA's Download button on every
  // connectivity transition — SNOW-632: a run in flight owns the CTA (see
  // paintRun), so this is skipped while busy, exactly like the 'move'
  // handler above. The roundel itself no longer has an offline state to
  // re-render here either (see _renderControl).
  document.addEventListener('snowdesk:connectivity-changed', () => {
    if (pendingBbox && runState !== 'busy') _updateReadout();
  });

  // Boot: probe the roundel against real storage. SNOW-634: unlike the old
  // tile-cache probe, `basemapDownloadedAreas()` needs neither MAP nor the
  // active basemap's tile template, so this doesn't wait on
  // MAP_READY_PROMISE — that used to be a SEPARATE trigger for a second,
  // map-dependent probe. SNOW-635: no longer preceded by loading a saved
  // area either — see openFraming's own docstring for why there is none
  // left to load.
  renderControl();

  window.pwaCustomAreaDownload = Object.freeze({
    // SNOW-634: the sheet's add-trigger reaches framing through this —
    // same frozen-global idiom as pwaBasemapDownloads/pwaLayersMenu/
    // pwaDownloadedOverlay.
    openFraming: openFraming,
    // So a delete in the sheet settles the roundel without waiting for
    // the next basemap switch or connectivity flip — neither of which
    // affect it any more.
    refresh: renderControl,
  });
})();
