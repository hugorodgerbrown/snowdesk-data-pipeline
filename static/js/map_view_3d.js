// @ts-check
/*
 * static/js/map_view_3d.js — /map/'s 3-D view (SNOW-1449, SNOW-1455,
 * SNOW-1452, SNOW-1463).
 *
 * Loaded only for a signed-in reader in the map_3d flag (SNOW-1451)
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
 *   4. the pixel ratio is capped (2x, or 1.5x on a canvas over 2.5 million
 *      device pixels, fitted again on each resize; SNOW-1464) and the
 *      camera eases to the opening tilt,
 *      with the pitch ceiling opened and the zoom floored at z9. A
 *      ?view=3d boot whose link named a tilt (`?at=` with a pitch above 0,
 *      published by map.js as snowdeskMapState.linkedPitch) keeps that
 *      pitch instead, including after the basemap swap (SNOW-1450);
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
 *     a drawing. Its intervals go to map_3d_core.createFrameWatch, which
 *     trips on five failing ~2 s windows in a row (frameWindowFails). An
 *     interval either of whose frames saw tiles loading
 *     (!map.areTilesLoaded()) is not timed: close in, every pan fetches
 *     and drapes new tiles, and those frames run long on a phone that
 *     draws well (SNOW-1463). Timing starts after the first moveend that
 *     follows each tilt in, so the entering ease over a fresh style is
 *     not counted; the first interval of each run is dropped, and a
 *     hidden tab is never counted. Each closed window goes to the debug
 *     trace as `map 3d.window` while it records.
 *     A trip asks rather than drops (SNOW-1463): the map stays in 3-D,
 *     timing stops, and the toast says "3D is running slowly here" with
 *     "Switch to 2D" and "Keep 3D", and no timer. Switch to 2D goes flat
 *     for the visit (`left-slow`, then `why-slow` on the switch). Keep 3D,
 *     the ×, or another line taking the toast's place all leave the
 *     watchdog silenced for the visit, playback included; nothing is
 *     stored. Leaving 3-D by any route takes the prompt away. During
 *     timelapse playback a trip still goes flat at once with no prompt,
 *     because playback cannot wait for a tap; that exit's basemap swap
 *     also stops the timelapse (SNOW-58).
 *
 * OFFLINE (SNOW-1453). A download with terrain data pins the elevation
 * tiles too, and stamps its record with their template, so offline 3-D
 * stays available inside a downloaded area: the boxes of the areas
 * stamped with today's template (basemap_download_core.elevationAreaBoxes
 * over pwaBasemapAreas.downloadedAreas()) go to availability as
 * `offlineAreas`. They are read at boot and again on every connectivity
 * change and snowdesk:downloads-changed; nothing probes the cache. Going
 * offline inside one keeps 3-D; a moveend offline with the centre outside
 * every box leaves it with a line, and panning back in makes it available
 * again without entering it. The read is asynchronous (IndexedDB), so an
 * offline ?view=3d boot waits for the first one before it decides, with
 * the switch disabled for `offline` until then; an online boot is
 * unchanged.
 *
 * THE 3D TEST (SNOW-1456). map_3d_test.js drives this map through three
 * more hooks. `canTest()` says whether the test may run here at all: not
 * offline (it needs tiles), not on a device the gate keeps out or with
 * the WebGL context lost, not while a route is drawn. `beginTest()` saves
 * the camera and the view, enters 3-D even when the watchdog has laid it
 * flat for the visit (every other gate still holds), and suspends the
 * watchdog until `endTest({verdict})`, which puts the camera and the view
 * back. A `smooth` or `stutter` verdict lifts the watchdog's latch, so
 * 3-D can be turned on again this visit; any other ending keeps it. The
 * `why-slow` line a tap on the disabled switch shows carries a "Test this
 * device" link that asks for the test (snowdesk:map-3d-test-requested).
 *
 * Publishes `window.pwaMapView` (get / enter / exit / canTest / beginTest /
 * endTest): map.js reads the live view to retry the right basemap on a
 * reconnect, and the basemap picker hears `snowdesk:map-view-changed` to
 * disable its rows in 3-D.
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
  /** How long an offline ?view=3d boot waits for the downloaded areas. */
  const BOOT_BOXES_WAIT_MS = 2000;
  const COVERAGE = terrainCore ? terrainCore.COVERAGE_BOUNDS : null;

  const STRINGS = self.pwaStrings.read('map-view-strings-template', {
    basemap: '3D uses the swisstopo winter map; yours comes back in 2D.',
    'why-zoom': 'Zoom in to see 3D',
    'why-offline': '3D needs a connection or a downloaded area',
    'why-coverage': '3D covers Switzerland for now',
    'why-editing': 'Finish drawing the route to see 3D',
    'left-offline': 'Back in 2D: offline, 3D works only in a downloaded area.',
    'left-coverage': 'Back in 2D: 3D covers Switzerland for now.',
    'why-device': '3D is not available on this device',
    'why-slow': '3D is too slow on this device',
    'why-webgl': '3D comes back once the map\'s graphics have reset',
    'left-webgl': 'The map\'s graphics reset, so it is flat again.',
    'left-slow': 'Back in 2D: 3D is too slow on this device.',
    'zoom-floor': '3D stops here. Switch to 2D to zoom out.',
    'test-device': 'Test this device',
    'slow-prompt': '3D is running slowly here',
    'switch-2d': 'Switch to 2D',
    'keep-3d': 'Keep 3D',
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
  /**
   * The reader was asked whether to keep 3-D on a slow device (SNOW-1463):
   * the watchdog does not time anything again this visit. Never stored.
   */
  let silenced = false;
  /** The slow prompt's buttons, while they are on the toast. */
  let slowPrompt = /** @type {?HTMLElement} */ (null);
  /** The map's WebGL context is lost and not yet restored. */
  let contextLost = false;
  /** The 3D test is running (beginTest to endTest): the watchdog rests. */
  let testing = false;
  /**
   * The view and camera beginTest found, which endTest puts back.
   *
   * @type {?{view: string, camera: {center: [number, number], zoom: number, pitch: number, bearing: number}}}
   */
  let beforeTest = null;
  /** deviceGate's answer, worked out once at boot; null lets 3-D in. */
  let gated = /** @type {?string} */ (null);
  /** The elevation tiles' template, which an area's stamp must match. */
  const DEM_TEMPLATE = button.dataset.demTileUrl || '';
  /**
   * SNOW-1453: the boxes of the downloaded areas holding today's elevation
   * tiles, where 3-D is available offline. Empty until the first read lands.
   *
   * @type {Array<ReadonlyArray<number>>}
   */
  let offlineAreas = [];
  /** The read of the boxes now running, the latest one started. */
  let boxesRead = /** @type {Promise<void>} */ (Promise.resolve());
  /** Counts the reads, so an earlier one landing late is dropped. */
  let boxesGeneration = 0;

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
    // The test measures the device the watchdog judged; its latch waits.
    tooSlow: tooSlow && !testing,
    contextLost,
    editing: drawing,
    centre: map.getCenter(),
    bounds: COVERAGE,
    offlineAreas,
  });

  /**
   * Offline, with the centre outside every downloaded area that holds the
   * elevation tiles: what lays 3-D flat once the map has gone offline.
   *
   * @returns {boolean}
   */
  const offlineOutside = () =>
    !online() && !offlineAreas.some((box) => viewCore.covers(map.getCenter(), box));

  /** @returns {Promise<Array<ReadonlyArray<number>>>} The boxes, read afresh. */
  const readBoxes = async () => {
    const areas = win.pwaBasemapAreas;
    const downloads = win.pwaBasemapDownloadCore;
    if (!DEM_TEMPLATE || !areas || typeof areas.downloadedAreas !== 'function') return [];
    if (!downloads || typeof downloads.elevationAreaBoxes !== 'function') return [];
    try {
      // The device's own areas only: the account's would cost a request.
      const held = await areas.downloadedAreas({ deviceOnly: true });
      return downloads.elevationAreaBoxes(held, DEM_TEMPLATE);
    } catch (_err) {
      return [];
    }
  };

  /**
   * Read the boxes again; when the read lands, 3-D in use offline outside
   * them is laid flat and the switch is brought up to date.
   *
   * @returns {Promise<void>}
   */
  const refreshBoxes = () => {
    boxesGeneration += 1;
    const generation = boxesGeneration;
    boxesRead = readBoxes().then((boxes) => {
      if (generation !== boxesGeneration) return;
      offlineAreas = boxes;
      if (view === THREE_D && offlineOutside()) exit({ reason: viewCore.REASONS.OFFLINE });
      else sync();
    });
    return boxesRead;
  };

  /** @returns {Promise<void>} Once the latest read of the boxes has landed. */
  const boxesSettled = async () => {
    let read;
    do {
      read = boxesRead;
      await read;
    } while (read !== boxesRead);
  };

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

  /**
   * One closed watchdog window, onto the debug trace while it records.
   *
   * @param {object} closed What createFrameWatch's onWindow hears.
   */
  const traceWindow = (closed) => {
    const log = win.pwaDebugLog;
    if (!log || !log.on || typeof log.record !== 'function') return;
    log.record('map', '3d.window', closed);
  };

  const watch = core.createFrameWatch({ onWindow: traceWindow });
  /** A move has ended since the last tilt in, so moves are timed now. */
  let timingArmed = false;
  /** @type {?number} */
  let frameRequest = null;
  /** The last frame's timestamp in this run; null at the start of one. */
  let lastFrameAt = /** @type {?number} */ (null);
  /** The run's first interval has been dropped. */
  let firstDropped = false;
  /** The last frame saw tiles still loading. */
  let lastFrameLoading = false;

  /** @returns {boolean} Whether a move now is one the watchdog times. */
  const timing = () =>
    view === THREE_D && !suspended && timingArmed && !testing && !silenced && !document.hidden;

  /** @returns {boolean} Whether the map is still fetching or draping tiles. */
  const tilesLoading = () => typeof map.areTilesLoaded === 'function' && !map.areTilesLoaded();

  /** Stop the loop; the next run starts afresh. */
  const stopFrames = () => {
    if (frameRequest !== null) window.cancelAnimationFrame(frameRequest);
    frameRequest = null;
    lastFrameAt = null;
    firstDropped = false;
    lastFrameLoading = false;
  };

  /** Go flat for the visit: the watchdog's own exit, or Switch to 2D. */
  const dropForSlow = () => {
    tooSlow = true;
    exit({ reason: viewCore.REASONS.SLOW });
  };

  /**
   * The watch tripped. During playback, flat at once; otherwise ask, and
   * time nothing more whatever the answer.
   */
  const tripped = () => {
    if (playing) {
      dropForSlow();
      return;
    }
    silenced = true;
    stopFrames();
    askSlow();
  };

  /** @param {number} at The frame's timestamp. */
  const onFrame = (at) => {
    frameRequest = null;
    if (!timing() || !(moving || playing)) {
      stopFrames();
      return;
    }
    const loading = tilesLoading();
    if (lastFrameAt !== null) {
      // The first interval of a run carries the gesture's own start-up.
      // An interval either end of which saw tiles loading is skipped.
      if (!firstDropped) firstDropped = true;
      else if (watch.push(at - lastFrameAt, loading || lastFrameLoading)) {
        tripped();
        return;
      }
    }
    lastFrameAt = at;
    lastFrameLoading = loading;
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

  /**
   * Tilt the relief in, with the ceiling open. Its ease is not timed.
   *
   * @param {number} [pitch] Where to tilt to: the opening tilt unless a
   *   boot keeps a linked one.
   */
  const tiltIn = (pitch = core.OPENING_CAMERA.pitch) => {
    timingArmed = false;
    map.setMaxPitch(core.MAX_PITCH);
    map.easeTo({ pitch, duration: EASE_MS });
    // easeTo stops an ease still running, and that fires its moveend here;
    // the move this starts is the one whose end arms the timing.
    timingArmed = false;
  };

  /**
   * The tilt the boot camera's own link asked for (map.js publishes it on
   * snowdeskMapState before it strips `?at=`), or null for none or 0.
   *
   * @returns {number|null}
   */
  const linkedPitch = () => {
    const value = state ? state.linkedPitch : null;
    return typeof value === 'number' && value > 0 ? Math.min(value, core.MAX_PITCH) : null;
  };

  /**
   * Draw 3D at the ratio this canvas's size calls for (SNOW-1464): the
   * device's, capped at 2x, and 1.5x on a large canvas.
   *
   * @returns {void}
   */
  const fitPixelRatio = () => {
    const container = map.getContainer();
    const ratio = core.pixelRatioFor(window.devicePixelRatio || 1, container.clientWidth, container.clientHeight);
    if (ratio !== map.getPixelRatio()) map.setPixelRatio(ratio);
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
      if (!options.boot && why.reason === viewCore.REASONS.SLOW) offerTest();
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
      fitPixelRatio();
    }
    const swapped = swapBasemap(viewCore.BASEMAP_KEY);
    // A swap brings a new style; its styledata installs the relief.
    if (!swapped) installNow();
    // SNOW-1450: a boot from a link that asked for a tilt keeps it; every
    // other entry tilts to the opening pitch.
    const pitch = options.boot ? linkedPitch() : null;
    const tilt = () => (pitch === null ? tiltIn() : tiltIn(pitch));
    if (options.gesture) map.setMaxPitch(core.MAX_PITCH);
    else tilt();
    // A setStyle stops a camera ease where it stands (measured in
    // Chromium), so the tilt runs again once the new style is in.
    if (swapped && !options.gesture) {
      map.once('style.load', () => {
        if (view === THREE_D && !suspended) tilt();
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
    hideSlowPrompt();
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

  // Offline, 3-D stays inside a downloaded area that holds the elevation
  // tiles. The exit waits for the boxes to be read again (`refreshBoxes`
  // decides it), so an area the reader downloaded or synced since the last
  // read keeps them in 3-D rather than being judged on the old list.
  const onConnectivity = () => {
    sync();
    refreshBoxes();
  };
  document.addEventListener('snowdesk:connectivity-changed', onConnectivity);
  window.addEventListener('offline', onConnectivity);
  window.addEventListener('online', onConnectivity);
  // Every area-record write fires it: a download, a sync's stamp, a removal.
  document.addEventListener('snowdesk:downloads-changed', () => {
    refreshBoxes();
  });

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
    // Offline, panned off every downloaded area: there is no elevation to
    // draw. Panning back in makes 3-D available again, but does not enter.
    if (view === THREE_D && offlineOutside()) {
      exit({ reason: viewCore.REASONS.OFFLINE });
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

  // A rotated iPad or a resized window can cross the large-canvas line. A
  // setPixelRatio resizes too, and the second pass finds nothing to change.
  map.on('resize', () => {
    if (view !== THREE_D || contextLost || typeof map.setPixelRatio !== 'function') return;
    fitPixelRatio();
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

  // ---- The 3D test (SNOW-1456) -------------------------------------------

  /**
   * Whether the 3D test may run here, and if not, why. The camera gates
   * (zoom, coverage) do not count: the test flies its own camera over
   * Saas-Fee.
   *
   * @returns {{ok: boolean, reason: ?string}}
   */
  const canTest = () => {
    /** @param {string} reason */
    const no = (reason) => ({ ok: false, reason });
    if (gated !== null) return no(viewCore.REASONS.DEVICE);
    if (contextLost) return no(viewCore.REASONS.WEBGL);
    if (!online()) return no(viewCore.REASONS.OFFLINE);
    if (drawing) return no(viewCore.REASONS.EDITING);
    return { ok: true, reason: null };
  };

  /**
   * Save the camera and the view, and enter 3-D for the test with the
   * watchdog resting.
   *
   * @returns {boolean} Whether the map is in 3-D for the test.
   */
  const beginTest = () => {
    if (testing) return view === THREE_D;
    if (!canTest().ok) return false;
    const centre = map.getCenter();
    beforeTest = {
      view,
      camera: {
        center: [centre.lng, centre.lat],
        zoom: map.getZoom(),
        pitch: map.getPitch(),
        bearing: map.getBearing(),
      },
    };
    testing = true;
    stopFrames();
    if (view === THREE_D) return true;
    // Over Saas-Fee first, where the camera gates are met.
    map.jumpTo({ center: core.OPENING_CAMERA.center.slice(), zoom: core.OPENING_CAMERA.zoom });
    if (enter()) return true;
    const saved = beforeTest;
    testing = false;
    beforeTest = null;
    map.jumpTo({ ...saved.camera });
    sync();
    return false;
  };

  /**
   * End the test: the watchdog wakes, a passing verdict lifts its latch,
   * and the view and camera beginTest found come back.
   *
   * @param {{verdict?: string}} [result]
   * @returns {void}
   */
  const endTest = (result = {}) => {
    if (!testing) return;
    testing = false;
    const verdict = result && result.verdict;
    if (verdict === 'smooth' || verdict === 'stutter') tooSlow = false;
    watch.reset();
    const saved = beforeTest;
    beforeTest = null;
    // Flat before the test: flat again, with no line (exit has no reason).
    if (saved && saved.view !== THREE_D && view === THREE_D) exit();
    if (saved) map.jumpTo({ ...saved.camera });
    sync();
  };

  /** @returns {{toast: HTMLElement, body: Element}|null} The map toast's parts. */
  const toastParts = () => {
    const toast = document.getElementById('map-sheet-toast');
    const body = toast ? toast.querySelector('[data-toast-body]') : null;
    return toast && body ? { toast, body } : null;
  };

  /**
   * A text button for the line the toast is showing.
   *
   * @param {string} label
   * @param {string} hook The data attribute that names it.
   * @param {() => void} onClick
   * @returns {HTMLButtonElement}
   */
  const toastAction = (label, hook, onClick) => {
    const action = document.createElement('button');
    action.type = 'button';
    action.className = 'ml-2 font-semibold underline underline-offset-2';
    action.setAttribute(hook, '');
    action.textContent = label;
    action.addEventListener('click', onClick);
    return action;
  };

  /**
   * Put a "Test this device" link on the line the toast is showing (the
   * `why-slow` line), which asks for the 3D test.
   */
  const offerTest = () => {
    if (!canTest().ok) return;
    const parts = toastParts();
    if (!parts) return;
    parts.body.append(' ', toastAction(STRINGS['test-device'], 'data-map-3d-test-open', () => {
      parts.toast.classList.add('hidden');
      document.dispatchEvent(new CustomEvent('snowdesk:map-3d-test-requested'));
    }));
  };

  /**
   * Ask whether to keep 3-D (SNOW-1463): the line and its two buttons, on
   * a toast with no timer. The watchdog is silenced before this is asked,
   * so a prompt that goes unanswered is Keep 3D.
   */
  function askSlow() {
    if (win.MapSheet && typeof win.MapSheet.toast === 'function') {
      win.MapSheet.toast(STRINGS['slow-prompt'], { sticky: true });
    }
    const parts = toastParts();
    if (!parts) return;
    const group = document.createElement('span');
    group.setAttribute('data-map-3d-slow-prompt', '');
    group.className = 'whitespace-nowrap';
    group.append(
      toastAction(STRINGS['switch-2d'], 'data-map-3d-switch-2d', () => {
        hideSlowPrompt();
        dropForSlow();
      }),
      toastAction(STRINGS['keep-3d'], 'data-map-3d-keep', hideSlowPrompt),
    );
    parts.body.append(' ', group);
    slowPrompt = group;
  }

  /** Take the prompt away, if it is still the line the toast shows. */
  function hideSlowPrompt() {
    const group = slowPrompt;
    slowPrompt = null;
    if (!group || !group.isConnected) return;
    const parts = toastParts();
    if (parts) parts.toast.classList.add('hidden');
    group.remove();
  }

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
  // enter (offline outside a downloaded area, or a device the gate keeps
  // out) lays the map flat on the reader's basemap.
  const bootInto3d = () => {
    if (view === THREE_D) return;
    if (!enter({ boot: true })) {
      map.jumpTo({ pitch: 0 });
      swapBasemap(preferredKey());
      announce(FLAT);
    }
  };
  refreshBoxes();
  if (viewCore.readView(location.search) === THREE_D) {
    // Online the boot is synchronous, as it always was. Offline it waits for
    // the first read of the boxes, with the switch disabled until then, so
    // nobody tilts in before the downloaded areas are known.
    // The wait is capped: a read that never lands lays the map flat
    // through the same path, rather than leaving the switch disabled.
    if (online()) bootInto3d();
    else {
      Promise.race([
        boxesSettled(),
        new Promise((resolve) => setTimeout(resolve, BOOT_BOXES_WAIT_MS)),
      ]).then(bootInto3d);
    }
  }
  sync();

  window.pwaMapView = Object.freeze({
    get: () => view,
    enter: () => enter(),
    exit: () => exit(),
    canTest,
    beginTest,
    endTest,
  });
})();
