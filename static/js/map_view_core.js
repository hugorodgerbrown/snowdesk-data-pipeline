// @ts-check
/*
 * static/js/map_view_core.js — the map's two views, flat and 3-D (SNOW-1449,
 * SNOW-1455, SNOW-1452).
 *
 * The view sits beside the mode (map_mode_core.js), not inside it: the mode
 * is what the map shows, the view is how it is drawn. Both Conditions and
 * Terrain can be drawn in 3-D (SNOW-1452), which is MapLibre's own terrain
 * on the swisstopo winter map
 * (docs/decisions/a-3d-map-is-maplibre-terrain.md). Every rule that can be
 * decided without a map is here, so Vitest can pin it; static/js/map.js
 * reads it at parse time (the boot basemap, the boot camera and every
 * setStyle path), and static/js/map_view_3d.js applies it.
 *
 *   - THE URL. `?view=3d`, beside `?mode=terrain`. Nothing is stored on
 *     the device, so a bare /map/ always opens flat, and a link opens 3-D
 *     in either mode; the view never picks the mode. Flat is written by
 *     leaving the parameter out.
 *   - AVAILABILITY. 3-D needs a device that can draw it, a
 *     connection, no route being drawn, a centre inside the elevation's
 *     coverage and a zoom of at least MIN_ZOOM. The coverage box is passed
 *     in (terrain_filter_core's COVERAGE_BOUNDS), never copied here, so
 *     there is one source of it. Offline (SNOW-1453), a downloaded area
 *     whose elevation tiles are pinned stands in for the connection: the
 *     boxes are passed in (basemap_download_core's elevationAreaBoxes),
 *     and the rule checks them, never the cache.
 *   - THE DEVICE (SNOW-1455). Three things turn 3-D off for the rest of a
 *     visit, asked first so a device that cannot draw it is never told to
 *     zoom in: the gate at boot (deviceGate: no WebGL, or
 *     2 GB of memory or less on Android Chrome), frames over budget, and a
 *     lost WebGL context until it is restored. Nothing about the device is
 *     stored.
 *   - THE BASEMAP. 3-D is drawn on the swisstopo winter map; flat on the
 *     reader's own choice. The choice is read, never written.
 *   - THE CAMERA. A flat boot zeroes a stored pitch, so a tilt is never
 *     remembered on the device; the bearing is kept.
 *
 * Exports (frozen `self.pwaMapViewCore`):
 *
 *   VIEWS / URL_PARAM / MIN_ZOOM / BASEMAP_KEY / REASONS
 *   parseView(value)
 *   readView(search)
 *   withView(search, view)
 *   covers(centre, bounds)
 *   availability(input)
 *   LOW_MEMORY_GB / deviceGate(input)
 *   basemapFor(view, preferredKey)
 *   bootCamera(stored, view)
 */
(function () {
  'use strict';

  /** The two views. Flat is the default, and what an unknown value means. */
  const VIEWS = Object.freeze({ FLAT: 'flat', THREE_D: '3d' });

  /** The query parameter a link carries the view in. */
  const URL_PARAM = 'view';

  /**
   * The lowest zoom 3-D is drawn at. Below it the relief is too small to
   * read and the elevation tiles too many for a phone.
   */
  const MIN_ZOOM = 9;

  /** The basemap 3-D is drawn on (settings.BASEMAP_STYLES key). */
  const BASEMAP_KEY = 'swisstopo_winter';

  /** Why 3-D is unavailable, in the order they are asked. */
  const REASONS = Object.freeze({
    DEVICE: 'device',
    SLOW: 'slow',
    WEBGL: 'webgl',
    OFFLINE: 'offline',
    EDITING: 'editing',
    COVERAGE: 'coverage',
    ZOOM: 'zoom',
  });

  /**
   * Read one linked value as a view.
   *
   * @param {unknown} value
   * @returns {string} A member of `VIEWS`; `flat` for anything unknown.
   */
  function parseView(value) {
    return value === VIEWS.THREE_D ? VIEWS.THREE_D : VIEWS.FLAT;
  }

  /**
   * The view the map opens in: 3-D only when the link asks for it, in
   * either mode. Nothing is stored, so there is no fallback to read.
   *
   * @param {string} search `location.search`, with or without the `?`.
   * @returns {string}
   */
  function readView(search) {
    return parseView(new URLSearchParams(search || '').get(URL_PARAM));
  }

  /**
   * The query string with the view written in, every other parameter kept.
   *
   * @param {string} search `location.search`.
   * @param {string} view
   * @returns {string} `''` or `'?…'`.
   */
  function withView(search, view) {
    const params = new URLSearchParams(search || '');
    if (parseView(view) === VIEWS.THREE_D) params.set(URL_PARAM, VIEWS.THREE_D);
    else params.delete(URL_PARAM);
    const query = params.toString();
    return query ? '?' + query : '';
  }

  /**
   * Whether a point is inside a `[west, south, east, north]` box, edges
   * included. No box means no limit.
   *
   * @param {{lng: number, lat: number}|null|undefined} centre
   * @param {ReadonlyArray<number>|null|undefined} bounds
   * @returns {boolean}
   */
  function covers(centre, bounds) {
    if (!bounds) return true;
    if (!centre || !Number.isFinite(centre.lng) || !Number.isFinite(centre.lat)) return false;
    const [west, south, east, north] = bounds;
    return centre.lng >= west && centre.lng <= east && centre.lat >= south && centre.lat <= north;
  }

  /**
   * @typedef {Object} AvailabilityInput
   * @property {boolean} online
   * @property {number} zoom The camera zoom.
   * @property {boolean} [gated] deviceGate turned 3-D off at boot.
   * @property {boolean} [tooSlow] The frame watchdog tripped this visit.
   * @property {boolean} [contextLost] The map's WebGL context is lost.
   * @property {boolean} [editing] A route is being drawn.
   * @property {{lng: number, lat: number}} centre
   * @property {ReadonlyArray<number>|null} [bounds] The coverage box.
   * @property {ReadonlyArray<ReadonlyArray<number>>|null} [offlineAreas]
   *   SNOW-1453: `[west, south, east, north]` boxes of the downloaded areas
   *   whose elevation tiles are pinned. Offline, 3-D is available only with
   *   the centre inside one of them.
   */

  /**
   * Whether 3-D can be shown now, and if not, the first reason why.
   *
   * @param {AvailabilityInput} input
   * @returns {{enabled: boolean, reason: ?string}}
   */
  function availability(input) {
    /** @param {string} reason */
    const no = (reason) => ({ enabled: false, reason });
    if (input.gated) return no(REASONS.DEVICE);
    if (input.tooSlow) return no(REASONS.SLOW);
    if (input.contextLost) return no(REASONS.WEBGL);
    if (!input.online && !(input.offlineAreas || []).some((box) => covers(input.centre, box))) {
      return no(REASONS.OFFLINE);
    }
    if (input.editing) return no(REASONS.EDITING);
    if (!covers(input.centre, input.bounds)) return no(REASONS.COVERAGE);
    if (!(input.zoom >= MIN_ZOOM)) return no(REASONS.ZOOM);
    return { enabled: true, reason: null };
  }

  /**
   * The most memory, in GB, at which an Android phone is not offered 3-D.
   * `navigator.deviceMemory` rounds down to a power of two and is capped at
   * 8, so 2 is the 2 GB class and below.
   */
  const LOW_MEMORY_GB = 2;

  /**
   * @typedef {Object} DeviceGateInput
   * @property {boolean} webgl WebGL can be read from the map's canvas.
   * @property {number|undefined|null} [deviceMemory] `navigator.deviceMemory`,
   *   which only Chromium reports.
   * @property {boolean} [android] The browser is on Android.
   */

  /**
   * Whether this device is kept out of 3-D, worked out once, at boot: no
   * WebGL, or LOW_MEMORY_GB or less on Android Chrome. A desktop's memory
   * is never held against it, and a browser that does not report it passes.
   *
   * @param {DeviceGateInput} input
   * @returns {?string} `REASONS.DEVICE`, or null when 3-D may be offered.
   */
  function deviceGate(input) {
    if (!input.webgl) return REASONS.DEVICE;
    const memory = input.deviceMemory;
    if (input.android && typeof memory === 'number' && memory <= LOW_MEMORY_GB) return REASONS.DEVICE;
    return null;
  }

  /**
   * The basemap a view is drawn on: swisstopo winter in 3-D, the reader's
   * own otherwise. The preference is an input, never an output.
   *
   * @param {string} view
   * @param {string} preferredKey
   * @returns {string}
   */
  function basemapFor(view, preferredKey) {
    return parseView(view) === VIEWS.THREE_D ? BASEMAP_KEY : preferredKey;
  }

  /**
   * The stored camera the map boots on. A flat boot is looked at straight
   * down, whatever pitch was stored; the bearing is kept.
   *
   * @template {{pitch: number}} T
   * @param {T|null} stored
   * @param {string} view
   * @returns {T|null}
   */
  function bootCamera(stored, view) {
    if (!stored) return null;
    if (parseView(view) === VIEWS.THREE_D) return stored;
    return { ...stored, pitch: 0 };
  }

  self.pwaMapViewCore = Object.freeze({
    VIEWS,
    URL_PARAM,
    MIN_ZOOM,
    BASEMAP_KEY,
    REASONS,
    parseView,
    readView,
    withView,
    covers,
    availability,
    LOW_MEMORY_GB,
    deviceGate,
    basemapFor,
    bootCamera,
  });
})();
