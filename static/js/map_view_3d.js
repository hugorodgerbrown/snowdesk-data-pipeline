// @ts-check
/*
 * static/js/map_view_3d.js — /map/'s 3-D view (SNOW-1449, SNOW-1455,
 * SNOW-1452).
 *
 * Loaded only for a reader in both the map_3d and map_modes flags
 * (public/map.html), after map.js and the two 3-D helpers it reuses:
 * map_3d_core.js (the elevation source, the terrain, sky and sunlight
 * specs, the opening tilt and the pitch and pixel-ratio ceilings) and
 * map_3d_terrain.js (the elevation protocol and the idempotent install).
 * The rules that need no map are map_view_core.js's; this applies them.
 * See docs/decisions/a-3d-map-is-maplibre-terrain.md.
 *
 * ENTERING, from the 3D roundel or a tilt gesture, in either mode:
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
 * Both modes are drawn in 3-D (SNOW-1452), so a mode change keeps the view
 * and only re-reads the switch. map.js paints the danger fill as a light
 * wash in 3-D on snowdesk:map-view-changed; nothing here touches it. The
 * map leaves 3-D, with a line each, when it goes offline or is panned out
 * of Switzerland. Drawing a route
 * (snowdesk:route-draw) takes the relief and the tilt away and locks
 * tilting, on swisstopo winter; when it ends, 3-D comes back if it still
 * can, and otherwise the map leaves it.
 *
 * In 3-D the zoom stops at z9; a zoom-out at the floor says so once per
 * attempt (a burst of wheel ticks or one pinch), not once per tick. While
 * flat with 3-D available the pitch ceiling stays open, so a
 * reader's own tilt (it carries an originalEvent; an ease does not) is
 * what enters 3-D; anywhere else the ceiling is 0 and the map does not tilt.
 *
 * THE DEVICE (SNOW-1455). Three things lay 3-D flat and keep it off for
 * this visit only; nothing about the device is stored. Each goes through
 * exit({reason}), and the switch then reads the reason from available(),
 * so it is aria-disabled, a tap says why and the tilt stays locked.
 *   - The gate, worked out once at boot (map_view_core.deviceGate): no
 *     WebGL on the map's canvas, or 2 GB of memory or less on Android
 *     Chrome. A ?view=3d boot on such a device opens flat.
 *   - A lost WebGL context (MapLibre re-fires the canvas's
 *     webglcontextlost / webglcontextrestored as map events) goes flat
 *     with one line; until it is restored 3-D cannot be entered. The
 *     restore says nothing and turns the switch back on.
 *   - Frames over budget. A requestAnimationFrame loop runs between
 *     movestart and moveend, and for as long as the timelapse plays
 *     (snowdesk:timelapse-state, SNOW-1452), in 3-D and not suspended for
 *     a drawing. Its intervals go to map_3d_core.createFrameWatch; two
 *     failing ~2 s windows in a row (frameWindowFails) go flat for the
 *     visit. During playback that exit's basemap swap also stops the
 *     timelapse (SNOW-58), which is what a desktop that cannot keep up
 *     needs. Timing
 *     starts after the first moveend that follows each tilt in, so the
 *     entering ease over a fresh style is not counted; the first interval
 *     of each run is dropped, and a hidden tab is never counted.
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
  // SNOW-1457: where 3D opens is where swisstopo's raster winter map has
  // detail (swisstopo_edge_core.js GATE_OUTLINE), not the swissALTI3D box,
  // which Terrain mode's slope tiles keep.
  const edgeCore = self.pwaSwisstopoEdgeCore;
  const COVERAGE = edgeCore
    ? edgeCore.GATE_OUTLINE
    : (terrainCore ? terrainCore.COVERAGE_BOUNDS : null);

  const STRINGS = self.pwaStrings.read('map-view-strings-template', {
    basemap: '3D uses the swisstopo winter map; yours comes back in 2D.',
    'why-zoom': 'Zoom in to see 3D',
    'why-offline': '3D needs a connection',
    'why-coverage': '3D covers Switzerland and its border areas for now',
    'why-editing': 'Finish drawing the route to see 3D',
    'left-offline': 'Back in 2D: 3D needs a connection.',
    'left-coverage': 'Back in 2D: 3D covers Switzerland and its border areas for now.',
    'why-device': '3D is not available on this device',
    'why-slow': '3D is too slow on this device',
    'why-webgl': '3D comes back once the map\'s graphics have reset',
    'left-webgl': 'The map\'s graphics reset, so it is flat again.',
    'left-slow': 'Back in 2D: 3D is too slow on this device.',
    'zoom-floor': '3D stops here. Switch to 2D to zoom out.',
  });

  /** @type {string} */
  let view = FLAT;
  /** A route is being drawn. */
  let drawing = false;
  /** In 3-D, with the relief and the tilt held off for the drawing. */
  let suspended = false;
  /** The timelapse is playing (snowdesk:timelapse-state). */
  let playing = false;
  /** The camera is moving: between movestart and moveend. */
  let moving = false;
  /** @type {?number} */
  let savedMinZoom = null;
  /** @type {?number} */
  let savedPixelRatio = null;
  /**
   * The reader's pixel ratio, held back while the WebGL context is lost:
   * setPixelRatio resizes, and MapLibre takes a lost context's 0x0 drawing
   * buffer for a hardware limit (_maxCanvasSize), so it waits for the
   * restore.
   *
   * @type {?number}
   */
  let pendingPixelRatio = null;
  let lastZoomOutAt = -Infinity;
  /** The frame watchdog tripped: no more 3-D this visit. */
  let tooSlow = false;
  /** The map's WebGL context is lost and not yet restored. */
  let contextLost = false;
  /** deviceGate's answer, worked out once at boot; null lets 3-D in. */
  let gated = /** @type {?string} */ (null);

  // ---- What the page knows ----------------------------------------------

  /** @param {string} text */
  const say = (text) => {
    if (win.MapSheet && typeof win.MapSheet.toast === 'function') win.MapSheet.toast(text);
  };

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
    gated: gated !== null,
    tooSlow,
    contextLost,
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
    : viewCore.basemapFor(viewCore.readView(location.search), preferredKey());
  // The picker is the other thing that changes it.
  // Counts every swap, the picker's included, so a fetch that resolves after
  // a later swap has started cannot draw over it.
  let swapGeneration = 0;
  document.addEventListener('snowdesk:basemap-changing', (e) => {
    const detail = /** @type {CustomEvent} */ (e).detail;
    swapGeneration += 1;
    if (detail && detail.key) drawn = detail.key;
  });

  /**
   * Draw `key`, unless the map already is. The picker's stored choice is
   * not touched. An ESRI basemap, and swisstopo winter (SNOW-1457), is
   * fetched and rewritten first (map_state.js resolveBasemapStyle), so the
   * swap is awaited.
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
    const generation = swapGeneration;
    const resolve = win.resolveBasemapStyle;
    // No diff: MapLibre 4.7.1 cannot diff a style that carries terrain (it
    // warns, aborts tile requests and rebuilds anyway), and the swap
    // replaces every source the basemap has.
    Promise.resolve(typeof resolve === 'function' ? resolve(key, url) : url)
      .then((style) => {
        // A later swap started while this one was fetching; it wins.
        if (generation !== swapGeneration) return;
        map.setStyle(style, { diff: false });
      })
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
    // A toggle's name stays put ("3-D view"); aria-pressed says whether it is on.
    button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
    const blocked = !pressed && !available().enabled;
    if (blocked) button.setAttribute('aria-disabled', 'true');
    else button.removeAttribute('aria-disabled');
    const ceiling = pitchCeiling();
    if (map.getMaxPitch() !== ceiling) map.setMaxPitch(ceiling);
  };

  /**
   * @param {string} next
   * @param {string} [reason] Why the map left 3-D on its own (REASONS), if it did.
   */
  const announce = (next, reason) => {
    document.documentElement.dataset.mapView = next;
    try {
      history.replaceState(history.state, '', location.pathname + viewCore.withView(location.search, next) + location.hash);
    } catch (err) {
      console.warn('map_view_3d: could not update the address bar', err);
    }
    const detail = reason ? { view: next, reason } : { view: next };
    document.dispatchEvent(new CustomEvent('snowdesk:map-view-changed', { detail }));
  };

  // ---- The frame watchdog ----------------------------------------------

  const watch = core.createFrameWatch();
  /** A move has ended since the last tilt in, so moves are timed now. */
  let timingArmed = false;
  /** @type {?number} */
  let frameRequest = null;
  /** The last frame's timestamp in this run; null at the start of one. */
  let lastFrameAt = /** @type {?number} */ (null);
  /** The run's first interval has been dropped. */
  let firstDropped = false;

  /** @returns {boolean} Whether a move now is one the watchdog times. */
  const timing = () => view === THREE_D && !suspended && timingArmed && !document.hidden;

  /** Stop the loop; the next run starts afresh. */
  const stopFrames = () => {
    if (frameRequest !== null) window.cancelAnimationFrame(frameRequest);
    frameRequest = null;
    lastFrameAt = null;
    firstDropped = false;
  };

  /** @param {number} at The frame's timestamp. */
  const onFrame = (at) => {
    frameRequest = null;
    if (!timing() || !(moving || playing)) {
      stopFrames();
      return;
    }
    if (lastFrameAt !== null) {
      // The first interval of a run carries the gesture's own start-up.
      if (!firstDropped) firstDropped = true;
      else if (watch.push(at - lastFrameAt)) {
        tooSlow = true;
        exit({ reason: viewCore.REASONS.SLOW });
        return;
      }
    }
    lastFrameAt = at;
    frameRequest = window.requestAnimationFrame(onFrame);
  };

  /** Start timing a move or playback, unless a run is on already. */
  const startFrames = () => {
    if (frameRequest !== null || !timing()) return;
    lastFrameAt = null;
    firstDropped = false;
    frameRequest = window.requestAnimationFrame(onFrame);
  };

  /** @type {Record<string, string>} */
  const WHY = {
    zoom: STRINGS['why-zoom'],
    offline: STRINGS['why-offline'],
    coverage: STRINGS['why-coverage'],
    editing: STRINGS['why-editing'],
    device: STRINGS['why-device'],
    slow: STRINGS['why-slow'],
    webgl: STRINGS['why-webgl'],
  };

  /** @type {Record<string, string>} */
  const LEFT = {
    offline: STRINGS['left-offline'],
    coverage: STRINGS['left-coverage'],
    webgl: STRINGS['left-webgl'],
    slow: STRINGS['left-slow'],
  };

  /** Tilt the relief in, with the ceiling open. Its ease is not timed. */
  const tiltIn = () => {
    timingArmed = false;
    map.setMaxPitch(core.MAX_PITCH);
    map.easeTo({ pitch: core.OPENING_CAMERA.pitch, duration: EASE_MS });
    // easeTo stops an ease still running, and that fires its moveend here;
    // the move this starts is the one whose end arms the timing.
    timingArmed = false;
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
    timingArmed = false;
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
    // A setStyle stops a camera ease where it stands (measured in
    // Chromium), so the tilt runs again once the new style is in.
    if (swapped && !options.gesture) {
      map.once('style.load', () => {
        if (view === THREE_D && !suspended) tiltIn();
      });
    }
    announce(THREE_D);
    sync();
    if (swapped && !options.boot) say(STRINGS.basemap);
    return true;
  };

  /**
   * Leave 3-D: flat, on the reader's basemap.
   *
   * @param {{reason?: string}} [options]
   * @returns {void}
   */
  const exit = (options = {}) => {
    if (view !== THREE_D) return;
    view = FLAT;
    suspended = false;
    stopFrames();
    watch.reset();
    timingArmed = false;
    removeNow();
    map.setMinZoom(savedMinZoom === null ? 0 : savedMinZoom);
    if (savedPixelRatio !== null && typeof map.setPixelRatio === 'function') {
      if (contextLost) pendingPixelRatio = savedPixelRatio;
      else map.setPixelRatio(savedPixelRatio);
    }
    savedPixelRatio = null;
    // Flat at once, not eased: the setStyle below would stop an ease part
    // way, leaving the flat map tilted.
    map.jumpTo({ pitch: 0 });
    swapBasemap(preferredKey());
    // The reason travels with the change: a slow-frame trip stops the
    // timelapse (map_timelapse.js), even when the reader's basemap is
    // swisstopo winter and so no swap fires snowdesk:basemap-changing.
    announce(FLAT, options.reason);
    sync();
    if (options.reason && LEFT[options.reason]) say(LEFT[options.reason]);
  };

  // ---- What moves the view ----------------------------------------------

  button.addEventListener('click', (e) => {
    // A tap on the segment already raised changes nothing; a tap on the
    // other one, or Enter / Space on the switch, toggles.
    const face = e.target instanceof Element ? e.target.closest('[data-view-face]') : null;
    if (face && /** @type {HTMLElement} */ (face).dataset.viewFace === view) return;
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

  // A reader's own tilt enters 3-D, in either mode. An ease (ours, or the
  // exit's way back to flat) carries no originalEvent.
  map.on('pitch', (/** @type {any} */ e) => {
    if (view !== FLAT || !e || !e.originalEvent) return;
    if (map.getPitch() > 0) enter({ gesture: true });
  });

  // Both modes are drawn in 3-D: a mode change keeps the view.
  document.addEventListener('snowdesk:map-mode-changed', sync);

  const onConnectivity = () => {
    if (view === THREE_D && !online()) exit({ reason: 'offline' });
    else sync();
  };
  document.addEventListener('snowdesk:connectivity-changed', onConnectivity);
  window.addEventListener('offline', onConnectivity);
  window.addEventListener('online', onConnectivity);

  map.on('movestart', () => {
    moving = true;
    startFrames();
  });

  map.on('moveend', () => {
    moving = false;
    if (!playing) stopFrames();
    // The first move to end after a tilt in is that ease; the next is timed.
    if (view === THREE_D && !suspended) timingArmed = true;
    if (view === THREE_D && !viewCore.covers(map.getCenter(), COVERAGE)) {
      exit({ reason: 'coverage' });
      return;
    }
    // Playback started before the tilt-in ease ended is timed from now.
    if (playing) startFrames();
    sync();
  });

  // The timelapse plays in 3-D (it plays only on a desktop, SNOW-1452), and
  // the watchdog times it as it times a move.
  document.addEventListener('snowdesk:timelapse-state', (e) => {
    const detail = /** @type {CustomEvent} */ (e).detail;
    playing = !!(detail && detail.playing);
    if (playing) startFrames();
    else if (!moving) stopFrames();
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
      stopFrames();
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

  // A hidden tab draws nothing, and its gap is not a slow frame.
  // Playback carries on behind a hidden tab, so timing it resumes when the
  // tab comes back; a move has ended by then and starts its own run.
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopFrames();
    else if (playing) startFrames();
  });

  // ---- WebGL lost and restored ------------------------------------------

  map.on('webglcontextlost', () => {
    contextLost = true;
    if (view === THREE_D) exit({ reason: viewCore.REASONS.WEBGL });
    else sync();
  });

  map.on('webglcontextrestored', () => {
    contextLost = false;
    // Flat already (the loss laid it flat); make sure, and say nothing.
    if (view === THREE_D) exit();
    if (map.getPitch() > 0) map.jumpTo({ pitch: 0 });
    if (pendingPixelRatio !== null && typeof map.setPixelRatio === 'function') map.setPixelRatio(pendingPixelRatio);
    pendingPixelRatio = null;
    sync();
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

  /** @returns {boolean} Whether WebGL can be read from the map's canvas. */
  const canvasHasWebgl = () => {
    try {
      const canvas = map.getCanvas();
      // The context MapLibre made: asking for its own type hands it back.
      return !!(canvas && (canvas.getContext('webgl2') || canvas.getContext('webgl')));
    } catch (_err) {
      return false;
    }
  };
  // Only Chromium reports deviceMemory, so Android here means Android Chrome.
  gated = viewCore.deviceGate({
    webgl: canvasHasWebgl(),
    deviceMemory: /** @type {any} */ (navigator).deviceMemory,
    android: /Android/i.test(navigator.userAgent || ''),
  });

  document.documentElement.dataset.mapView = FLAT;
  // map.js booted the view (swisstopo winter, the stored camera) from
  // ?view=3d, in either mode, inside the coverage. A boot that still cannot
  // enter (offline, or a device the gate keeps out) lays the map flat on
  // the reader's basemap.
  if (viewCore.readView(location.search) === THREE_D) {
    if (!enter({ boot: true })) {
      map.jumpTo({ pitch: 0 });
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
