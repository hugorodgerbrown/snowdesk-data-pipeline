/*
 * static/js/place_picker.js — SNOW-475 touch-friendly place-picker.
 *
 * Replaces the draggable-marker pattern favourites.js and report.js each
 * used independently (a maplibregl.Marker({draggable: true}) whose dragend
 * wrote the chosen coord) — on touch screens the dragged finger occludes
 * the pin it is supposed to be placing, making fine positioning unreliable.
 *
 * Instead, a single pin (#map-place-pin, added in
 * public/partials/_map_embed.html) is held at a fixed point on screen via
 * CSS (static/css/map.css). The user pans the map underneath it; the chosen
 * coordinate is read live from the map on every 'moveend', so the pin itself
 * never moves relative to the screen — only the map does.
 *
 * Pin lift (SNOW-538)
 * -------------------
 * That fixed point is the centre of the map area the calling sheet does NOT
 * cover, which is not the same as the centre of the map. Both sheets dock to
 * the bottom of the viewport and size themselves to their content, and the
 * report form (a location line, five problem buttons and Cancel) is tall
 * enough on a phone to reach past the map's vertical midpoint — so a pin
 * pinned at 50% sat behind the sheet and the user could not see the point
 * they were placing. Callers pass the sheet as ``occludedBy``; whenever it
 * covers where the pin would otherwise sit, the pin is lifted to the middle
 * of the strip of map still visible above it (``--map-place-pin-lift``,
 * read by .map-place-pin in static/css/map.css).
 *
 * The lift is mirrored into MapLibre's own ``padding`` (bottom = 2 × lift),
 * which moves the transform's centre point to exactly where the pin now is.
 * That keeps MAP.getCenter() and MAP.setCenter() meaning "the point under
 * the pin" throughout, so the coordinate read and the recenterTo write both
 * stay one-liners — no screen-space arithmetic to get subtly wrong, and
 * nothing for callers to know about. Padding is snapshotted on activate()
 * and restored on deactivate().
 *
 * Setting padding re-anchors the map's centre LngLat at the new centre
 * point rather than changing it, which is also what makes a mid-flow lift
 * change safe: the map slides, the chosen coordinate does not.
 *
 * Zoom anchoring
 * --------------
 * MapLibre zooms about the gesture by default — the wheel about the cursor,
 * a pinch about the fingers' midpoint — which moves the map's centre and so
 * moves the coordinate under a pin that has not itself moved. Zooming in to
 * place a pin accurately was therefore re-picking a different point on the
 * way in. While the picker is armed, both continuous zoom handlers are
 * re-armed with ``{around: 'center'}`` and double-click/double-tap zoom
 * (which has no such option) is turned off, so no zoom interaction can
 * change the chosen coordinate. See ``anchorZoomOnPin``.
 *
 * Exposes window.PlacePicker with three methods:
 *
 *   activate({ onChange, recenterTo, occludedBy })
 *     Clears the map down to the basemap (window.PlacementFocus.enter() —
 *     static/js/map_placement_focus.js), reveals the pin and starts
 *     tracking the coordinate under it. onChange(lat, lon)
 *     fires once immediately (the point under the pin is always a valid
 *     coordinate — this is what lets callers unblock a "confirm" affordance
 *     right away, with no separate "tap to place" step) and again on every
 *     subsequent 'moveend'. recenterTo (optional [lon, lat]) puts that
 *     coordinate under the pin before the first read — used by report.js's
 *     GPS-refine flow so the pin starts on the GPS fix rather than wherever
 *     the map already was. occludedBy (optional element) is the sheet the
 *     pin must stay clear of; omit it and the pin stays at the centre.
 *
 *   deactivate()
 *     Hides the pin, stops tracking, and restores every layer the map was
 *     showing before activate() cleared it. Idempotent — safe to call
 *     whether or not activate() was ever called.
 *
 *   isActive()
 *     True between a call to activate() and the matching deactivate().
 *
 * Loaded before favourites.js/report.js on every page that includes either
 * surface (see _favourites_surface.html / _report_surface.html) — both
 * script tags are `defer`, so document-order execution guarantees
 * window.PlacePicker exists by the time those modules run.
 */

(function () {
  'use strict';

  /** Module-level 'moveend' handler, so deactivate() can remove exactly
   * the listener activate() registered.
   * @type {(() => void)|null}
   */
  let moveEndHandler = null;

  /** Whether the picker is currently armed. @type {boolean} */
  let active = false;

  /** The sheet the pin must stay clear of, from activate()'s occludedBy.
   * @type {HTMLElement|null}
   */
  let occluder = null;

  /** Pixels the pin currently sits above the map's vertical centre.
   * @type {number}
   */
  let pinLift = 0;

  /** The map's ``padding`` before activate() applied the lift, restored on
   * deactivate(). Null whenever the picker is disarmed.
   * @type {maplibregl.PaddingOptions|null}
   */
  let basePadding = null;

  /** Callback handed to activate(), re-run whenever the pin's screen point
   * or the map moves. @type {((lat: number, lon: number) => void)|null}
   */
  let changeHandler = null;

  /** Observers that re-run the lift measurement when the sheet grows or the
   * viewport changes shape. @type {ResizeObserver|null}
   */
  let sheetObserver = null;

  /** Undoes the zoom-anchor changes activate() made, restoring MapLibre's
   * default handler configuration. Null whenever the picker is disarmed.
   * @type {(() => void)|null}
   */
  let restoreZoomHandlers = null;

  /** Return the global MAP object if available, else null. Guards against
   * the case where map.js has not yet initialised.
   * @returns {maplibregl.Map|null}
   */
  function getMap() {
    return typeof MAP !== 'undefined' ? MAP : null; // eslint-disable-line no-undef
  }

  /** @returns {HTMLElement|null} The centre-pin element, if in the DOM. */
  function getPin() {
    return document.getElementById('map-place-pin');
  }

  /** Reveal the centre-pin element, if present in the DOM. */
  function showPin() {
    const pin = getPin();
    if (pin) pin.removeAttribute('hidden');
  }

  /** Hide the centre-pin element, if present in the DOM. */
  function hidePin() {
    const pin = getPin();
    if (pin) pin.setAttribute('hidden', '');
  }

  /**
   * Pixels the pin must be lifted above the map's vertical centre to clear
   * the occluding sheet: half the height the sheet steals from the map, so
   * the pin lands in the middle of the strip still visible above it.
   *
   * Zero whenever the sheet does not actually cover the pin's default point
   * — on a wide viewport it is a bottom-right card, and the middle of the
   * map is in the clear — so desktop keeps the plain centred pin.
   *
   * @returns {number} Lift in CSS pixels; never negative.
   */
  function measurePinLift() {
    const map = getMap();
    if (!map || !occluder || occluder.hasAttribute('hidden')) return 0;

    const container = map.getContainer && map.getContainer();
    if (!container) return 0;
    const mapRect = container.getBoundingClientRect();
    const sheetRect = occluder.getBoundingClientRect();
    if (!mapRect.height || !sheetRect.height) return 0;

    const centreX = mapRect.left + mapRect.width / 2;
    const centreY = mapRect.top + mapRect.height / 2;
    const covered =
      centreX >= sheetRect.left &&
      centreX <= sheetRect.right &&
      centreY >= sheetRect.top &&
      centreY <= sheetRect.bottom;
    if (!covered) return 0;

    // Height of the map above the sheet's top edge, clamped into the map:
    // a sheet taller than the map itself leaves nothing visible, and the
    // clamp keeps the lift at most half the map's height.
    const visibleHeight = Math.min(
      Math.max(sheetRect.top - mapRect.top, 0),
      mapRect.height,
    );
    return (mapRect.height - visibleHeight) / 2;
  }

  /**
   * Adopt a new lift: move the pin (via the CSS custom property) and move
   * MapLibre's centre point to match (via bottom padding of twice the lift,
   * since padding shifts the centre by half its imbalance).
   *
   * Applying both from one place is what guarantees the pin and the
   * coordinate can never drift apart. Setting padding re-anchors the map's
   * centre LngLat at the new centre point rather than changing it, so a lift
   * adopted mid-flow slides the map and leaves the chosen point under the
   * pin — no compensating camera move needed.
   *
   * @param {number} lift — pixels above the map's vertical centre.
   */
  function applyPinLift(lift) {
    pinLift = lift;
    const pin = getPin();
    if (pin) pin.style.setProperty('--map-place-pin-lift', lift + 'px');
    const map = getMap();
    if (map && basePadding) {
      map.setPadding({ ...basePadding, bottom: basePadding.bottom + lift * 2 });
    }
  }

  /**
   * Make every zoom gesture keep the point under the pin fixed, and return
   * the undo.
   *
   * By default MapLibre zooms about the gesture: the scroll wheel about the
   * cursor, a pinch about the two fingers' midpoint, a double-tap about the
   * tap. Any of those moves the map's centre — and therefore the coordinate
   * under a pin that has not itself moved — so a user who zoomed in to
   * place a pin precisely was silently re-picking a different point on the
   * way in. Both continuous handlers take ``{around: 'center'}``, which
   * anchors them on the transform's centre; that centre is the padded one,
   * which is exactly where the pin is drawn (see applyPinLift).
   *
   * Double-click / double-tap zoom has no equivalent option, so it is
   * turned off for the duration rather than left as the one gesture that
   * can still move the chosen point.
   *
   * Each handler is only touched if it was enabled to begin with, and the
   * undo puts it back the way MapLibre had it.
   *
   * @param {maplibregl.Map} map
   * @returns {() => void} The restore function.
   */
  function anchorZoomOnPin(map) {
    const restores = [];

    // ScrollZoomHandler.enable() ignores its options when the handler is
    // already enabled, so the re-arm has to go through disable() first.
    // TwoFingersTouchZoomRotateHandler applies them either way; the same
    // shape is used for both so there is one thing to remember.
    for (const handler of [map.scrollZoom, map.touchZoomRotate]) {
      if (!handler || !handler.isEnabled || !handler.isEnabled()) continue;
      handler.disable();
      handler.enable({ around: 'center' });
      restores.push(function restore() {
        handler.disable();
        handler.enable();
      });
    }

    const doubleClick = map.doubleClickZoom;
    if (doubleClick && doubleClick.isEnabled && doubleClick.isEnabled()) {
      doubleClick.disable();
      restores.push(function restore() {
        doubleClick.enable();
      });
    }

    return function restoreAll() {
      for (const restore of restores) restore();
    };
  }

  /** Report the coordinate under the pin to activate()'s onChange — which,
   * thanks to the padding above, is simply the map's centre.
   */
  function readCoord() {
    if (!changeHandler) return;
    const map = getMap();
    if (!map) return;
    const at = map.getCenter();
    changeHandler(at.lat, at.lng);
  }

  /**
   * Re-measure the lift after a layout change (the sheet swapping in taller
   * content, an orientation change, the on-screen keyboard opening). The
   * point the user had chosen is held under the pin across the move, so a
   * sheet growing under their finger never silently re-picks a different
   * coordinate.
   */
  function refreshPinLift() {
    if (!active) return;
    const next = measurePinLift();
    if (next === pinLift) return;
    applyPinLift(next);
    readCoord();
  }

  /** Start watching for the layout changes that move the pin. */
  function observeLayout() {
    window.addEventListener('resize', refreshPinLift);
    // jsdom (and older Safari) has no ResizeObserver — the window listener
    // above still covers orientation and keyboard changes without it.
    if (occluder && typeof ResizeObserver === 'function') {
      sheetObserver = new ResizeObserver(refreshPinLift);
      sheetObserver.observe(occluder);
    }
  }

  /** Stop watching for layout changes. Idempotent. */
  function unobserveLayout() {
    window.removeEventListener('resize', refreshPinLift);
    if (sheetObserver) {
      sheetObserver.disconnect();
      sheetObserver = null;
    }
  }

  window.PlacePicker = {
    /**
     * Arm the place-picker: show the pin and start reporting the coordinate
     * under it on every pan.
     * @param {object} options
     * @param {(lat: number, lon: number) => void} options.onChange — called
     *   immediately with the point under the pin, then again on every
     *   'moveend'.
     * @param {[number, number]} [options.recenterTo] — optional [lon, lat]
     *   to move under the pin before the first onChange read.
     * @param {HTMLElement} [options.occludedBy] — the sheet the pin must
     *   stay clear of; without it the pin stays at the map's centre.
     */
    activate: function activate(options) {
      const map = getMap();
      if (!map) return;

      const recenterTo = options && options.recenterTo;

      // Guard against a stray previous activation (mirrors the
      // disarmPlacement-before-arm pattern the old marker code used).
      this.deactivate();

      changeHandler = (options && options.onChange) || null;
      occluder = (options && options.occludedBy) || null;

      // Clear the map down to the basemap before anything moves, so the
      // overlays are already gone by the time recenterTo pans — the user
      // never sees the choropleth slide across the screen on its way out.
      window.PlacementFocus?.enter();

      // Measure and place the pin before recentring: with the padding in
      // place, setCenter puts its coordinate under the pin rather than at
      // the map's geometric centre.
      showPin();
      active = true;
      basePadding = map.getPadding
        ? map.getPadding()
        : { top: 0, right: 0, bottom: 0, left: 0 };
      applyPinLift(measurePinLift());
      restoreZoomHandlers = anchorZoomOnPin(map);

      if (recenterTo) {
        map.setCenter(recenterTo);
      }

      readCoord();

      moveEndHandler = readCoord;
      map.on('moveend', moveEndHandler);
      observeLayout();
    },

    /** Disarm the place-picker: hide the pin and stop tracking the map.
     * Idempotent.
     */
    deactivate: function deactivate() {
      const map = getMap();
      if (map && moveEndHandler) {
        map.off('moveend', moveEndHandler);
      }
      moveEndHandler = null;
      unobserveLayout();
      if (restoreZoomHandlers) {
        restoreZoomHandlers();
        restoreZoomHandlers = null;
      }
      active = false;
      changeHandler = null;
      occluder = null;
      // Lift 0 also writes basePadding straight back, restoring whatever
      // padding the map had before the placement started.
      applyPinLift(0);
      basePadding = null;
      hidePin();
      // Bring back every overlay enter() hid. Idempotent on its own side,
      // so the unconditional call is safe on the never-activated path.
      window.PlacementFocus?.exit();
    },

    /**
     * @returns {boolean} True while the picker is armed.
     */
    isActive: function isActive() {
      return active;
    },
  };
}());
