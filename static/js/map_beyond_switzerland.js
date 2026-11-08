// @ts-check
/*
 * static/js/map_beyond_switzerland.js — the winter map past the Swiss
 * border, on /map/ (SNOW-1460).
 *
 * Loaded only for a reader in the map_beyond_switzerland flag
 * (public/map.html), after the map bundle and the 3-D view, and after the
 * four files it reads at parse time: swisstopo_extent.js,
 * beyond_switzerland_core.js, osm_clip_core.js and alpine_relief_core.js.
 * It does nothing unless `#map` carries data-map-beyond-switzerland. What
 * it owns:
 *
 *   - THE PROTOCOLS. `osmclip://` and `alpinerelief://`, registered once
 *     per MapLibre namespace (the map_3d_terrain.registerDemProtocol
 *     pattern). Each handler posts {id, url} to its worker
 *     (osm_clip_worker.js, alpine_relief_worker.js) and resolves with the
 *     bytes of the matching reply. An abort posts {type: 'cancel', id} and
 *     rejects with an AbortError; a worker's error reply, or the worker
 *     failing, rejects. The workers start on the first tile request, each
 *     sent an `init` with the scripts it imports and the polygons it needs.
 *   - THE SPLICE. On every style.load, and on styledata (MapLibre 4.x does
 *     not always fire style.load after a setStyle; map.js reinstalls its
 *     own layers on styledata for the same reason), the winter style gets
 *     beyond_switzerland_core's plan: the OSM and relief sources and
 *     layers. At boot, after a basemap swap and after the 3-D view's swap.
 *     The plan is idempotent, and a style already holding the OSM source is
 *     skipped before the style is read, so the repeated events cost
 *     nothing. If the style has loaded already when this runs, it splices
 *     at once.
 *   - THE 3-D COVERAGE. With the 3-D view on the page, it is handed the
 *     Alpine ring (window.pwaMapView.setCoverage).
 *   - THE RELIEF'S TUNING. Every reader gets alpine_relief_core's
 *     TUNING_DEFAULTS. `tune(values)` normalises a new one, posts it to the
 *     relief worker as {type: 'tune'} if the worker has started (its init
 *     carries the current one otherwise), and gives the relief source's
 *     tiles a `?v=` of alpine_relief_core.tuningKey so MapLibre fetches
 *     them again; a later splice adds the source with the same key, so a
 *     basemap swap keeps the tuning. Only with data-relief-tuning on `#map`
 *     (a staff reader; map_relief_tuning.js is the panel) is a tuning saved
 *     on this device under TUNING_STORAGE_KEY read back at boot.
 *
 * Publishes `window.pwaBeyondSwitzerland` (splice, tune, tuning,
 * TUNING_STORAGE_KEY) for the tuning panel, the debugger and the tests.
 */
(function mapBeyondSwitzerlandInit() {
  'use strict';

  const mapEl = document.getElementById('map');
  const core = self.pwaBeyondSwitzerlandCore;
  const clipCore = self.pwaOsmClipCore;
  const reliefCore = self.pwaAlpineReliefCore;
  const extent = self.pwaSwisstopoExtent;
  const win = /** @type {any} */ (window);
  const map = win.snowdeskMap;
  if (!mapEl || mapEl.dataset.mapBeyondSwitzerland !== 'true') return;
  if (!core || !clipCore || !reliefCore || !extent || !map) return;

  const data = mapEl.dataset;

  /** Where the staff tuning panel keeps a tuning on this device. */
  const TUNING_STORAGE_KEY = 'snowdesk.map.reliefTuning';

  /** The relief's tuning now; the defaults until a staff reader sets one. */
  let tuning = reliefCore.TUNING_DEFAULTS;
  /** Whether `tuning` was set, so the relief tiles carry its key. */
  let tuned = false;

  /**
   * One worker, started on the first request, answering {id, url} with
   * {id, data} or {id, error}.
   *
   * @param {string} workerUrl
   * @param {() => object} init The `init` message, built when it starts.
   * @returns {{request: (url: string, abortController?: AbortController) => Promise<ArrayBuffer>, post: (message: object) => boolean}}
   */
  function workerChannel(workerUrl, init) {
    /** @type {?Worker} */
    let worker = null;
    let nextId = 1;
    /** @type {Map<number, {resolve: (data: ArrayBuffer) => void, reject: (err: Error) => void}>} */
    const pending = new Map();

    /** @param {MessageEvent} event */
    const onMessage = (event) => {
      const reply = event.data || {};
      const entry = pending.get(reply.id);
      if (!entry) return;
      pending.delete(reply.id);
      if (reply.error) entry.reject(new Error(reply.error));
      else entry.resolve(reply.data);
    };

    /** The worker itself failed: every request in flight rejects. */
    const onError = () => {
      const failed = [...pending.values()];
      pending.clear();
      if (worker) worker.terminate();
      worker = null;
      for (const entry of failed) entry.reject(new Error(`beyond_switzerland: ${workerUrl} failed`));
    };

    /** @returns {Worker} */
    const ensure = () => {
      if (worker) return worker;
      worker = new Worker(workerUrl);
      worker.addEventListener('message', onMessage);
      worker.addEventListener('error', onError);
      worker.postMessage(init());
      return worker;
    };

    return {
      /** Post to the worker if it has started; an unstarted one's init carries it. */
      post(message) {
        if (!worker) return false;
        worker.postMessage(message);
        return true;
      },
      request(url, abortController) {
        const signal = abortController ? abortController.signal : null;
        if (signal && signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));
        const target = ensure();
        const id = nextId;
        nextId += 1;
        return new Promise((resolve, reject) => {
          pending.set(id, { resolve, reject });
          if (signal) {
            signal.addEventListener('abort', () => {
              if (!pending.has(id)) return;
              pending.delete(id);
              target.postMessage({ type: 'cancel', id });
              reject(new DOMException('Aborted', 'AbortError'));
            }, { once: true });
          }
          target.postMessage({ id, url });
        });
      },
    };
  }

  const osm = workerChannel(data.osmClipWorkerUrl || '', () => ({
    type: 'init',
    kitUrl: data.vectorTileKitUrl || '',
    coreUrl: data.osmClipCoreUrl || '',
    styleUrl: data.osmStyleUrl || '',
    extent: extent.EXTENT,
  }));
  const relief = workerChannel(data.alpineReliefWorkerUrl || '', () => ({
    type: 'init',
    coreUrl: data.alpineReliefCoreUrl || '',
    demTileUrl: data.demTileUrl || '',
    relief: extent.DETAILED_RELIEF,
    tuning,
  }));

  /**
   * The relief source's tiles with the current tuning's key.
   *
   * @returns {string[]}
   */
  function reliefTiles() {
    const key = reliefCore.tuningKey(tuning);
    return core.reliefSource().tiles.map((/** @type {string} */ url) => `${url}?v=${key}`);
  }

  /** Point the relief source on the map at the current tuning's tiles. */
  function applyTuning() {
    const source = map.getSource(core.RELIEF_SOURCE_ID);
    if (source && typeof source.setTiles === 'function') source.setTiles(reliefTiles());
  }

  /**
   * Change the relief's tuning: what the worker paints with, and the tiles'
   * key, so MapLibre paints them again.
   *
   * @param {unknown} values Anything normaliseTuning reads.
   * @returns {Readonly<Record<string, number>>} The tuning now in use.
   */
  function tune(values) {
    tuning = reliefCore.normaliseTuning(values);
    tuned = true;
    relief.post({ type: 'tune', tuning });
    applyTuning();
    return tuning;
  }

  /** @type {WeakSet<object>} The MapLibre namespaces the protocols are on. */
  const registered = new WeakSet();

  /**
   * Register both protocols on a MapLibre namespace, once.
   *
   * @param {any} maplibre
   * @returns {boolean} Whether this call registered them.
   */
  function registerProtocols(maplibre) {
    if (!maplibre || typeof maplibre.addProtocol !== 'function' || registered.has(maplibre)) return false;
    registered.add(maplibre);
    maplibre.addProtocol(
      clipCore.PROTOCOL,
      (/** @type {{url: string}} */ params, /** @type {AbortController} */ abortController) =>
        osm.request(params.url, abortController).then((bytes) => ({ data: bytes })),
    );
    maplibre.addProtocol(
      reliefCore.PROTOCOL,
      (/** @type {{url: string}} */ params, /** @type {AbortController} */ abortController) =>
        relief.request(params.url, abortController).then((bytes) => ({ data: bytes })),
    );
    return true;
  }

  /**
   * Add the plan to the map's style, if it is the winter style and not
   * spliced yet.
   *
   * @returns {boolean} Whether anything was added.
   */
  function splice() {
    // No isStyleLoaded() guard: in MapLibre 4.x it stays false until every
    // source's tiles and the sprite are in, and once the map goes idle no
    // further styledata is promised, so a guarded splice could never run.
    // map_view_3d's reinstall works the same way: try, and let a style
    // mid-swap throw into the catch; the next style.load splices.
    let style;
    try {
      style = map.getStyle();
    } catch (_err) {
      return false;
    }
    // plan() refuses a style that already has the OSM source, so it is
    // planned as if the source were absent; the adds below skip what exists.
    const sources = Object.assign({}, style && style.sources);
    delete sources[core.OSM_SOURCE_ID];
    const plan = core.plan(Object.assign({}, style, { sources }));
    if (!plan) return false;
    // A tuned relief keeps its key across a basemap swap.
    const reliefDef = plan.sources[core.RELIEF_SOURCE_ID];
    if (tuned && reliefDef) plan.sources[core.RELIEF_SOURCE_ID] = Object.assign({}, reliefDef, { tiles: reliefTiles() });
    const last = plan.layers.length ? plan.layers[plan.layers.length - 1].layer.id : null;
    // Done once the last planned layer is in, not once the source is: an
    // add that threw partway leaves the source but not the rest.
    if (map.getSource(core.OSM_SOURCE_ID) && (!last || map.getLayer(last))) return false;
    try {
      for (const [id, source] of Object.entries(plan.sources)) {
        if (!map.getSource(id)) map.addSource(id, source);
      }
      for (const { layer, beforeId } of plan.layers) {
        if (map.getLayer(layer.id)) continue;
        map.addLayer(layer, beforeId && map.getLayer(beforeId) ? beforeId : undefined);
      }
    } catch (err) {
      console.warn('[map] SNOW-1460: splice failed; retried on the next style event', err);
      return false;
    }
    return true;
  }

  // A staff reader's saved tuning, before the first splice adds the source.
  if (data.reliefTuning !== undefined) {
    try {
      const saved = window.localStorage.getItem(TUNING_STORAGE_KEY);
      if (saved) {
        tuning = reliefCore.normaliseTuning(JSON.parse(saved));
        tuned = true;
      }
    } catch (_err) {
      // No storage, or junk in it: the defaults.
    }
  }

  registerProtocols(win.maplibregl);
  map.on('style.load', splice);
  map.on('styledata', splice);
  splice();

  if (win.pwaMapView && typeof win.pwaMapView.setCoverage === 'function') {
    win.pwaMapView.setCoverage(core.ALPINE_GATE);
  }

  window.pwaBeyondSwitzerland = Object.freeze({
    splice,
    tune,
    tuning: () => tuning,
    TUNING_STORAGE_KEY,
  });
})();
