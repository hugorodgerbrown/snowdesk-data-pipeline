/*
 * static/js/map_overlay_bounds.js — how far down, and how far right, a map
 * overlay may reach (SNOW-658).
 *
 * The layers menu worked this out first. SNOW-511 capped its height so its
 * top rows could not slide behind the nav and the conditional off-season
 * banner; SNOW-656 then dropped its lower edge to just above the season
 * scrubber, because the CSS baseline it inherited was a function of the
 * roundel column's height and left a third of the map unused. Both halves
 * are MEASURED rather than declared, because everything they measure moves:
 * the banner is conditional, the safe-area insets vary by device, and the
 * column's height varies with the collapsible group.
 *
 * The three UGC sheets (#map-downloads-sheet, #favourite-sheet,
 * #report-sheet) need the same answer and had none: includes/_overlay_sheet.html
 * hard-codes a viewport corner (`sm:bottom-4 sm:right-4`), which knows
 * nothing about the scrubber below it or the roundel column beside it, so on
 * desktop a sheet opened over both. This module is that arithmetic, extracted
 * once, plus the horizontal inset the menu never needed (the menu opens from
 * INSIDE the column; a sheet has to clear it).
 *
 * ## Two coordinate bases, one geometry
 *
 * `compute()` deliberately returns VIEWPORT coordinates and leaves the
 * translation to each caller, because the two callers do not share a base:
 *
 *   - the layers menu is `position: absolute` inside #basemap-pill, so its
 *     `bottom` is measured from the pill — `pillBottom - floorY`;
 *   - a sheet is `position: fixed`, so its `bottom` is measured from the
 *     viewport — `window.innerHeight - floorY`.
 *
 * Returning a ready-made `bottom` would have to pick one of those and be
 * wrong for the other caller.
 *
 * ## Desktop only — and the mobile silence is the design, not a gap
 *
 * Below Tailwind's `sm` breakpoint (640px) this module writes NOTHING, and
 * clears anything a previous desktop-width pass wrote. That is deliberate
 * twice over.
 *
 * First, because there is nothing to compute: below `sm` the three panels
 * are full-width and docked to the bottom edge, covering the map, and that
 * IS the intended mobile behaviour — a docked sheet is the right shape on a
 * phone (the list is what is being read, there is no room to show it beside
 * anything, and the bottom edge is where the thumb is). A panel that had to
 * clear the scrubber and the roundel column on a 375px screen would be a
 * small box floating in a map the user can barely see. The two layouts and
 * the reasoning are written up in §3.7 of
 * docs/map-page-functional-spec.md, and in includes/_overlay_sheet.html's
 * own header beside the classes that draw them; expect "the panel fills the
 * screen on my phone" as a report, and read it as working.
 *
 * Second, because the mobile layout is left entirely to CSS on purpose:
 * inline styles beat the stylesheet, so a stranded value from before a
 * resize would silently pin a mobile sheet to a desktop geometry. Writing
 * nothing on a phone means the sheet's own classes are always the whole
 * answer there. Do not "complete" this module by giving it a mobile branch.
 *
 * ## What it does not do
 *
 * Positioning is recomputed on open and on resize, matching the menu. A
 * scrubber that appears or disappears WITHOUT a resize (it is server-rendered
 * and permanent on the map page today) would leave an open overlay on a stale
 * floor; the menu has always had that gap and this module does not close it,
 * rather than growing a MutationObserver for a case that cannot currently
 * arise.
 */

(function mapOverlayBoundsInit() {
  'use strict';

  // The clearance the layers menu already used above #map's top edge and
  // below its own floor (SNOW-511/SNOW-656) — kept identical so extracting
  // the arithmetic changes no number the menu writes.
  var TOP_GAP = 8;
  var BOTTOM_GAP = 8;

  // The gap between a sheet's right edge and the roundel column it must
  // clear. Matches the column's own inter-roundel gap (.map-controls-br's
  // `gap: 8px`, static/css/map.css), so the sheet reads as part of the same
  // rhythm rather than at an arbitrary distance from it.
  var SIDE_GAP = 8;

  // Tailwind's `sm` breakpoint — the width at which includes/_overlay_sheet.html
  // stops being a full-width bottom dock and becomes a floating card. Stated
  // once here rather than compared against `window.innerWidth` at each call
  // site, so there is one definition of "desktop" to keep in step with the CSS.
  var DESKTOP_QUERY = '(min-width: 640px)';

  /**
   * Every sheet that has been positioned at least once.
   *
   * Held so the resize listener below can re-place them all — including
   * clearing them on the way down to a phone width — without each caller
   * wiring a listener of its own.
   *
   * @type {Set<HTMLElement>}
   */
  var tracked = new Set();

  /**
   * Whether the viewport is wide enough for the floating-card layout.
   *
   * @returns {boolean} False when `matchMedia` is unavailable — a browser
   *   that cannot answer is treated as the mobile shape, which is the one
   *   that needs no JS to be correct.
   */
  function isDesktop() {
    if (typeof window.matchMedia !== 'function') return false;
    return window.matchMedia(DESKTOP_QUERY).matches;
  }

  /**
   * The room a map overlay may occupy, in viewport coordinates.
   *
   * @returns {{floorY: number, maxHeight: number, controlsLeft: number|null}|null}
   *   `floorY` — the lowest y an overlay's bottom edge may reach.
   *   `maxHeight` — the height that floor leaves below #map's top edge.
   *   `controlsLeft` — the left edge of the bottom-right roundel column, or
   *   null when it has no box (absent, or a display:none ancestor).
   *   Null overall when there is no #map on the page, which is the one case
   *   where a caller should leave its CSS alone entirely.
   */
  function compute() {
    var mapEl = document.getElementById('map');
    if (!mapEl) return null;
    var mapBox = mapEl.getBoundingClientRect();

    // The scrubber owns the foot of the map, so stop above it; with no
    // scrubber on the page, the map's own bottom edge is the floor.
    var scrubber = document.getElementById('season-scrubber');
    var floorY =
      (scrubber ? scrubber.getBoundingClientRect().top : mapBox.bottom) - BOTTOM_GAP;

    var controls = document.getElementById('map-controls-br');
    var controlsBox = controls ? controls.getBoundingClientRect() : null;

    return {
      floorY: floorY,
      maxHeight: Math.max(0, Math.round(floorY - mapBox.top - TOP_GAP)),
      controlsLeft: controlsBox && controlsBox.width > 0 ? controlsBox.left : null,
    };
  }

  /**
   * Drop every inline value this module writes, restoring the stylesheet.
   *
   * @param {HTMLElement} el
   * @returns {void}
   */
  function clear(el) {
    el.style.bottom = '';
    el.style.right = '';
    el.style.maxHeight = '';
  }

  /**
   * Place one fixed-position sheet inside the map's usable area.
   *
   * Opt-in per element rather than a sweep over `[data-overlay]`: the same
   * partial renders inline (`static=True`) in the component library, where a
   * fixed-position geometry would be meaningless.
   *
   * @param {HTMLElement} el The sheet container.
   * @returns {void}
   */
  function positionSheet(el) {
    if (!el) return;
    tracked.add(el);

    if (!isDesktop()) {
      clear(el);
      return;
    }

    var bounds = compute();
    if (!bounds) {
      clear(el);
      return;
    }

    // `bottom` and `right` are both measured from the viewport, the sheet
    // being `position: fixed`.
    el.style.bottom = Math.round(window.innerHeight - bounds.floorY) + 'px';
    el.style.maxHeight = bounds.maxHeight + 'px';
    if (bounds.controlsLeft === null) {
      // No column to clear — leave the partial's own `sm:right-4`.
      el.style.right = '';
    } else {
      el.style.right =
        Math.round(window.innerWidth - bounds.controlsLeft + SIDE_GAP) + 'px';
    }
  }

  // Same reason the menu re-places itself on resize: an orientation flip, a
  // mobile URL-bar show/hide or a desktop resize all move the floor, the cap
  // and the column. Every tracked sheet is re-placed, open or not — the
  // measurement reads the map chrome rather than the sheet, so a hidden sheet
  // is placed just as correctly, and this is also what clears a desktop
  // geometry off a sheet on the way down to a phone width.
  window.addEventListener('resize', function () {
    tracked.forEach(function (el) {
      positionSheet(el);
    });
  });

  window.pwaOverlayBounds = Object.freeze({
    compute: compute,
    isDesktop: isDesktop,
    positionSheet: positionSheet,
  });
})();
