// @ts-check
/*
 * static/js/map_view_3d.js — /map/'s 3-D view (SNOW-1449).
 *
 * Loaded only for a reader in both the map_3d and map_modes flags
 * (public/map.html), after map.js and the two 3-D helpers it reuses:
 * map_3d_core.js (the elevation source, the terrain, sky and sunlight
 * specs, the opening tilt and the pitch and pixel-ratio ceilings) and
 * map_3d_terrain.js (the elevation protocol and the idempotent install).
 * The rules that need no map are map_view_core.js's; this applies them.
 * See docs/decisions/a-3d-map-is-maplibre-terrain.md.
 *
 * ENTERING, from the 3D roundel or a tilt gesture in Terrain:
 *   1. the elevation protocol is registered, once;
 *   2. the map changes to the swisstopo winter map if it is not on it,
 *      without touching the picker's stored choice;
 *   3. the elevation source, the terrain, the sky and the north-west
 *      sunlight go on, the sunlight under the first label;
 *   4. the pixel ratio is capped and the camera eases to the opening tilt,
 *      with the pitch ceiling opened and the zoom floored at z9;
 *   5. `?view=3d` is written and, if the basemap changed, one line says so.
 * LEAVING reverses all of it: the relief and the sunlight go (the
 * elevation source stays, so a return re-reads nothing), the camera ends
 * on pitch 0 with its bearing kept, and the reader's basemap comes back
 * (the ESRI one through resolveBasemapStyle; none at all when theirs is
 * swisstopo winter). The swisstopo rock hachures are the basemap's own
 * layers and are never touched.
 *
 * Every setStyle wipes the terrain with every other source, so while in
 * 3-D it is put back on `styledata`, gated on the elevation source being
 * gone. map.js's own reinstall of the overlays is separate and untouched.
 *
 * The map leaves 3-D, with a line each, when it goes to Conditions (on
 * snowdesk:map-mode-changed, which map_mode.js dispatches synchronously, so
 * a region picked in Terrain lays the map flat before its sheet paints),
 * goes offline, or is panned out of Switzerland. Drawing a route
 * (snowdesk:route-draw) takes the relief and the tilt away and locks
 * tilting, on swisstopo winter; when it ends, 3-D comes back if it still
 * can, and otherwise the map leaves it.
 *
 * In 3-D the zoom stops at z9; a zoom-out at the floor says so once per
 * attempt (a burst of wheel ticks or one pinch), not once per tick. While
 * flat in Terrain with 3-D available the pitch ceiling stays open, so a
 * reader's own tilt (it carries an originalEvent; an ease does not) is
 * what enters 3-D; anywhere else the ceiling is 0 and the map does not tilt.
 *
 * Publishes `window.pwaMapView` (get / enter / exit): map.js reads the live
 * view to retry the right basemap on a reconnect, and the basemap picker
 * hears `snowdesk:map-view-changed` to disable its rows in 3-D.
 */
(function mapView3dInit() {
  'use strict';

  const button = document.getElementById('map-view-toggle');
  const mapEl = document.getElementById('map');
  const viewCore = self.pwaMapViewCore;
  const core = self.pwaMap3dCore;
  const terrain = self.pwaMap3dTerrain;
  const terrainCore = self.pwaTerrainFilterCore;
  const state = window.snowdeskMapState;
  const map = state ? state.map : null;
  if (!button || !mapEl || !viewCore || !core || !terrain || !map) return;
  if (mapEl.dataset.mapViewEnabled !== 'true') return;

  const { FLAT, THREE_D } = viewCore.VIEWS;
  const win = /** @type {any} */ (window);
  /** Where map_state.js keeps the reader's basemap (BASEMAP_STORAGE_KEY). */
  const BASEMAP_STORAGE_KEY = 'snowdesk.map.basemap';
  const EASE_MS = 600;
  /** Zoom-out input closer together than this is one attempt. */
  const ATTEMPT_GAP_MS = 700;
  const COVERAGE = terrainCore ? terrainCore.COVERAGE_BOUNDS : null;

  const STRINGS = self.pwaStrings.read('map-view-strings-template', {
    'show-3d': 'Show in 3-D',
    'show-2d': 'Back to 2D',
    basemap: '3-D uses the swisstopo winter map; yours comes back in 2D.',
    'why-zoom': 'Zoom in to see 3-D',
    'why-offline': '3-D needs a connection',
    'why-coverage': '3-D covers Switzerland for now',
    'why-editing': 'Finish drawing the route to see 3-D',
    'left-mode': 'Back in 2D: 3-D is part of Terrain.',
    'left-offline': 'Back in 2D: 3-D needs a connection.',
    'left-coverage': 'Back in 2D: 3-D covers Switzerland for now.',
    'zoom-floor': '3-D stops here. Switch to 2D to zoom out.',
  });

  /** @type {string} */
  let view = FLAT;
  /** A route is being drawn. */
  let drawing = false;
  /** In 3-D, with the relief and the tilt held off for the drawing. */
  let suspended = false;
  /** @type {?number} */
  let savedMinZoom = null;
  /** @type {?number} */
  let savedPixelRatio = null;
  let lastZoomOutAt = -Infinity;

  // ---- What the page knows ----------------------------------------------

  /** @param {string} text */
  const say = (text) => {
    if (win.MapSheet && typeof win.MapSheet.toast === 'function') win.MapSheet.toast(text);
  };

  /** @returns {string} The map's mode, `dated` or `terrain`. */
  const mode = () => (win.pwaMapMode ? win.pwaMapMode.get() : document.documentElement.dataset.mapMode || 'dated');

  /** @returns {boolean} */
  const online = () => {
    if (win.pwaConnectivity && typeof win.pwaConnectivity.isOnline === 'function') {
      return win.pwaConnectivity.isOnline();
    }
    return navigator.onLine !== false;
  };

  /** @returns {{enabled: boolean, reason: ?string}} */
  const available = () => viewCore.availability({
    online: online(),
    zoom: map.getZoom(),
    mode: mode(),
    editing: drawing,
    centre: map.getCenter(),
    bounds: COVERAGE,
  });

  /**
   * A basemap row's style URL.
   *
   * @param {string} key
   * @returns {string}
   */
  const urlFor = (key) => {
    const row = /** @type {?HTMLElement} */ (
      document.querySelector(`#basemap-menu [data-basemap-key="${key}"]`)
    );
    return row ? row.dataset.basemapUrl || '' : '';
  };

  /** @returns {string} The reader's own basemap, as map.js reads it. */
  const preferredKey = () => {
    let stored = null;
    try { stored = localStorage.getItem(BASEMAP_STORAGE_KEY); } catch (_err) { /* private mode */ }
    return stored && urlFor(stored) ? stored : mapEl.dataset.defaultBasemapKey || '';
  };

  const exploded = new URLSearchParams(location.search).get('layers') === 'exploded';
  /** The basemap the map is drawing (map.js booted it from the view). */
  let drawn = exploded
    ? viewCore.BASEMAP_KEY
    : viewCore.basemapFor(viewCore.readView(location.search, mode()), preferredKey());
  // The picker is the other thing that changes it.
  document.addEventListener('snowdesk:basemap-changing', (e) => {
    const detail = /** @type {CustomEvent} */ (e).detail;
    if (detail && detail.key) drawn = detail.key;
  });

  /**
   * Draw `key`, unless the map already is. The picker's stored choice is
   * not touched. An ESRI basemap is fetched and rewritten first
   * (map_state.js resolveBasemapStyle), so the swap is awaited.
   *
   * @param {string} key
   * @returns {boolean} Whether a swap started.
   */
  const swapBasemap = (key) => {
    if (!key || key === drawn) return false;
    const url = urlFor(key);
    if (!url) return false;
    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changing', { detail: { key, url } }));
    drawn = key;
    const resolve = win.resolveBasemapStyle;
    Promise.resolve(typeof resolve === 'function' ? resolve(key, url) : url)
      .then((style) => map.setStyle(style))
      .catch((/** @type {unknown} */ err) => console.warn('map_view_3d: could not change the basemap', err));
    return true;
  };

  // ---- The relief -------------------------------------------------------

  /** Put the relief on; a style still loading is retried on its styledata. */
  const installNow = () => {
    try {
      terrain.installTerrain(map);
    } catch (_err) {
      // The style is not done loading; styledata comes again when it is.
    }
  };

  const reinstall = () => {
    if (view !== THREE_D || suspended) return;
    if (map.getSource(core.DEM_SOURCE_ID) && terrain.hasTerrain(map)) return;
    installNow();
  };
  map.on('styledata', reinstall);
  map.on('style.load', reinstall);

  const removeNow = () => {
    try {
      terrain.removeTerrain(map);
    } catch (_err) {
      // Nothing of it survived a setStyle; there is nothing to take away.
    }
  };

  // ---- The controls -----------------------------------------------------

  /** @returns {number} The pitch ceiling for where the map is now. */
  const pitchCeiling = () => {
    if (view === THREE_D) return suspended ? 0 : core.MAX_PITCH;
    return available().enabled ? core.MAX_PITCH : 0;
  };

  /** The roundel's face and state, and the pitch ceiling. */
  const sync = () => {
    const pressed = view === THREE_D;
    button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    button.setAttribute('aria-label', pressed ? STRINGS['show-2d'] : STRINGS['show-3d']);
    const blocked = !pressed && !available().enabled;
    if (blocked) button.setAttribute('aria-disabled', 'true');
    else button.removeAttribute('aria-disabled');
    const ceiling = pitchCeiling();
    if (map.getMaxPitch() !== ceiling) map.setMaxPitch(ceiling);
  };

  /** @param {string} next */
  const announce = (next) => {
    document.documentElement.dataset.mapView = next;
    try {
      history.replaceState(history.state, '', location.pathname + viewCore.withView(location.search, next) + location.hash);
    } catch (err) {
      console.warn('map_view_3d: could not update the address bar', err);
    }
    document.dispatchEvent(new CustomEvent('snowdesk:map-view-changed', { detail: { view: next } }));
  };

  /** @type {Record<string, string>} */
  const WHY = {
    zoom: STRINGS['why-zoom'],
    offline: STRINGS['why-offline'],
    coverage: STRINGS['why-coverage'],
    editing: STRINGS['why-editing'],
  };

  /** @type {Record<string, string>} */
  const LEFT = {
    mode: STRINGS['left-mode'],
    offline: STRINGS['left-offline'],
    coverage: STRINGS['left-coverage'],
  };

  /** Tilt the relief in, with the ceiling open. */
  const tiltIn = () => {
    map.setMaxPitch(core.MAX_PITCH);
    map.easeTo({ pitch: core.OPENING_CAMERA.pitch, duration: EASE_MS });
  };

  /**
   * Enter 3-D, if it is available.
   *
   * @param {{gesture?: boolean, boot?: boolean}} [options]
   * @returns {boolean} Whether the map is in 3-D now.
   */
  const enter = (options = {}) => {
    if (view === THREE_D) return true;
    const why = available();
    // A boot below z9 is lifted to it by the floor, rather than refused.
    if (!why.enabled && !(options.boot && why.reason === viewCore.REASONS.ZOOM)) {
      if (!options.boot && why.reason) say(WHY[why.reason] || '');
      return false;
    }
    terrain.registerDemProtocol(win.maplibregl, button.dataset.demTileUrl || '');
    view = THREE_D;
    suspended = false;
    savedMinZoom = map.getMinZoom();
    map.setMinZoom(viewCore.MIN_ZOOM);
    if (typeof map.setPixelRatio === 'function') {
      savedPixelRatio = map.getPixelRatio();
      const capped = Math.min(window.devicePixelRatio || 1, core.MAX_PIXEL_RATIO);
      if (capped !== savedPixelRatio) map.setPixelRatio(capped);
    }
    const swapped = swapBasemap(viewCore.BASEMAP_KEY);
    // A swap brings a new style; its styledata installs the relief.
    if (!swapped) installNow();
    if (options.gesture) map.setMaxPitch(core.MAX_PITCH);
    else tiltIn();
    announce(THREE_D);
    sync();
    if (swapped && !options.boot) say(STRINGS.basemap);
    return true;
  };

  /**
   * Leave 3-D: flat, on the reader's basemap.
   *
   * @param {{reason?: string, immediate?: boolean}} [options]
   * @returns {void}
   */
  const exit = (options = {}) => {
    if (view !== THREE_D) return;
    view = FLAT;
    suspended = false;
    removeNow();
    map.setMinZoom(savedMinZoom === null ? 0 : savedMinZoom);
    if (savedPixelRatio !== null && typeof map.setPixelRatio === 'function') map.setPixelRatio(savedPixelRatio);
    savedPixelRatio = null;
    if (options.immediate) map.jumpTo({ pitch: 0 });
    else map.easeTo({ pitch: 0, duration: EASE_MS });
    swapBasemap(preferredKey());
    announce(FLAT);
    sync();
    if (options.reason && LEFT[options.reason]) say(LEFT[options.reason]);
  };

  // ---- What moves the view ----------------------------------------------

  button.addEventListener('click', () => {
    if (view === THREE_D) {
      exit();
      return;
    }
    enter();
  });

  // The elevation tiles 3-D asks for first, warmed when the reader reaches
  // for the roundel (map_3d_terrain.createPrefetcher, once per view).
  const prefetch = terrain.createPrefetcher(button.dataset.demTileUrl || '');
  for (const type of ['pointerenter', 'focus', 'touchstart']) {
    button.addEventListener(type, () => {
      if (view === FLAT && available().enabled) prefetch(map.getCenter(), map.getZoom());
    }, { passive: true });
  }

  // A reader's own tilt in Terrain enters 3-D. An ease (ours, or the
  // exit's way back to flat) carries no originalEvent.
  map.on('pitch', (/** @type {any} */ e) => {
    if (view !== FLAT || !e || !e.originalEvent) return;
    if (map.getPitch() > 0) enter({ gesture: true });
  });

  document.addEventListener('snowdesk:map-mode-changed', () => {
    if (view === THREE_D && mode() !== 'terrain') exit({ reason: 'mode', immediate: true });
    else sync();
  });

  const onConnectivity = () => {
    if (view === THREE_D && !online()) exit({ reason: 'offline', immediate: true });
    else sync();
  };
  document.addEventListener('snowdesk:connectivity-changed', onConnectivity);
  window.addEventListener('offline', onConnectivity);
  window.addEventListener('online', onConnectivity);

  map.on('moveend', () => {
    if (view === THREE_D && !viewCore.covers(map.getCenter(), COVERAGE)) {
      exit({ reason: 'coverage' });
      return;
    }
    sync();
  });

  document.addEventListener('snowdesk:route-draw', (e) => {
    const detail = /** @type {CustomEvent} */ (e).detail;
    drawing = !!(detail && detail.active);
    if (view !== THREE_D) {
      sync();
      return;
    }
    if (drawing && !suspended) {
      // Flat for the drawing, still on swisstopo winter: no setStyle.
      suspended = true;
      removeNow();
      map.jumpTo({ pitch: 0 });
      sync();
    } else if (!drawing && suspended) {
      const why = available();
      if (!why.enabled) {
        exit({ reason: why.reason || undefined });
        return;
      }
      suspended = false;
      installNow();
      tiltIn();
      sync();
    }
  });

  // ---- The zoom floor ---------------------------------------------------

  /** One zoom-out input; the first of an attempt says where 3-D stops. */
  const zoomOutAtFloor = () => {
    if (view !== THREE_D || suspended) return;
    if (map.getZoom() > viewCore.MIN_ZOOM + 0.01) return;
    const now = Date.now();
    const fresh = now - lastZoomOutAt > ATTEMPT_GAP_MS;
    lastZoomOutAt = now;
    if (fresh) say(STRINGS['zoom-floor']);
  };

  const container = map.getContainer();
  container.addEventListener('wheel', (/** @type {WheelEvent} */ e) => {
    if (e.deltaY > 0) zoomOutAtFloor();
  }, { passive: true });
  container.addEventListener('keydown', (/** @type {KeyboardEvent} */ e) => {
    if (e.key === '-' || e.key === '_') zoomOutAtFloor();
  });
  /** The two fingers' last spread, while a pinch is on. */
  let spread = 0;
  /** @param {TouchEvent} e */
  const pinchSpread = (e) => {
    const [a, b] = [e.touches[0], e.touches[1]];
    return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  };
  container.addEventListener('touchstart', (/** @type {TouchEvent} */ e) => {
    spread = e.touches.length === 2 ? pinchSpread(e) : 0;
  }, { passive: true });
  container.addEventListener('touchmove', (/** @type {TouchEvent} */ e) => {
    if (e.touches.length !== 2 || !spread) return;
    const now = pinchSpread(e);
    if (now < spread - 8) zoomOutAtFloor();
    spread = now;
  }, { passive: true });

  // ---- Boot -------------------------------------------------------------

  document.documentElement.dataset.mapView = FLAT;
  // map.js booted the view (swisstopo winter, the stored camera) and kept
  // ?view=3d only in Terrain inside the coverage. A boot that still cannot
  // enter (offline) lays the map flat on the reader's basemap.
  if (viewCore.readView(location.search, mode()) === THREE_D) {
    if (!enter({ boot: true })) {
      swapBasemap(preferredKey());
      announce(FLAT);
    }
  }
  sync();

  window.pwaMapView = Object.freeze({
    get: () => view,
    enter: () => enter(),
    exit: () => exit(),
  });
})();
