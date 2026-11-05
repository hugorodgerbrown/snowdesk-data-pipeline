// @ts-check
/*
 * static/js/map_3d.js — draws the 3-D terrain map at /map/3d/ (SNOW-1438).
 *
 * A sandbox behind the map_3d flag. MapLibre's own terrain (setTerrain on a
 * raster-dem source) lifts the basemap into relief, and every layer added
 * after it drapes onto the ground unchanged
 * (docs/decisions/a-3d-map-is-maplibre-terrain.md):
 *
 *   - Steep terrain: our terrain-class tiles, coloured on the device by
 *     terrain_filter_core's slope classifier and palette, through the same
 *     `terrainfilter://` protocol /map/ registers (this page is a separate
 *     document, so the name is free);
 *   - SAC's ski tours, asked for in a box around the camera on each move;
 *   - Wildlife: FOEN's wildlife rest zones and federal game reserves, as
 *     swisstopo WMTS rasters;
 *   - the reader's own routes, when signed in;
 *   - the resorts.
 *
 * A tap on a tour or a route flies the camera to the whole of it, keeping
 * the tilt: its uuid is looked up in the loaded data, because the tapped
 * feature is only a drawn fragment and a tour is one feature per leg.
 * `?route=<uuid>` opens on one of the reader's routes.
 *
 * Three switches (Steep terrain, SAC tours, Wildlife) show and hide their
 * layers. The camera and the switches are mirrored into the address bar
 * (`?at=…&layers=…`, map_3d_core.viewQuery) on every move and toggle, and
 * Share hands that URL on, so whoever opens it sees the same view. The URL
 * carries the reader's share token (`?share=`), which opens the page for
 * anyone holding the link, signed in or not.
 *
 * Every URL comes from a data attribute on #map-3d (apps/map/map_3d.py).
 * The pure parts are in map_3d_core.js.
 */
(function () {
  'use strict';

  const el = document.getElementById('map-3d');
  const core = self.pwaMap3dCore;
  const terrainCore = self.pwaTerrainFilterCore;
  const maplibre = /** @type {any} */ (self).maplibregl;
  if (!el || !core || !maplibre) return;

  const data = el.dataset;
  const COLOURS = core.COLOURS;
  const STEEP_SOURCE = 'map-3d-steep';
  const STEEP_LAYER = 'map-3d-steep-raster';
  const SUNLIGHT_LAYER = 'map-3d-sunlight';
  const TOURS_SOURCE = 'map-3d-tours';
  const ROUTES_SOURCE = 'map-3d-routes';
  const RESORTS_SOURCE = 'map-3d-resorts';
  const LINE_LAYERS = ['map-3d-tours-climb', 'map-3d-tours-descent', 'map-3d-routes-line'];
  const TOUR_LAYERS = ['map-3d-tours-casing', LINE_LAYERS[0], LINE_LAYERS[1]];
  const WILDLIFE_LAYERS = core.WILDLIFE_LAYERS.map((/** @type {string} */ id) => `map-3d-wildlife-${id}`);
  const TOURS_DEBOUNCE_MS = 300;

  // A shared link's view, else the opening camera and the default switches.
  const view = core.parseView(window.location.search);
  // Read before writeViewUrl rewrites the query string, which drops it.
  const wantedRoute = new URLSearchParams(window.location.search).get('route');
  const camera = view.camera || core.OPENING_CAMERA;
  /** @type {Record<string, boolean>} */
  const layerState = { ...view.layers };

  const map = new maplibre.Map({
    container: el,
    style: data.styleUrl,
    center: camera.center.slice(),
    zoom: camera.zoom,
    pitch: camera.pitch,
    bearing: camera.bearing,
    maxPitch: core.MAX_PITCH,
    pixelRatio: Math.min(window.devicePixelRatio || 1, core.MAX_PIXEL_RATIO),
  });

  /**
   * MapLibre's visibility value for a switch.
   *
   * @param {string} key
   * @returns {'visible'|'none'}
   */
  function visibility(key) {
    return layerState[key] ? 'visible' : 'none';
  }

  /** @returns {string} The URL that reproduces the current view. */
  function viewUrl() {
    const query = core.viewQuery(
      {
        center: map.getCenter(),
        zoom: map.getZoom(),
        pitch: map.getPitch(),
        bearing: map.getBearing(),
      },
      layerState,
    );
    // The share token (apps/map/models.py Map3dShare) opens the page for
    // whoever follows the link, past the flag and without a sign-in.
    const share = data.shareToken ? `share=${encodeURIComponent(data.shareToken)}&` : '';
    return `${window.location.origin}${window.location.pathname}?${share}${query}`;
  }

  /** Mirror the view into the address bar, so it can be copied from there too. */
  function writeViewUrl() {
    try {
      window.history.replaceState(null, '', viewUrl());
    } catch (err) {
      console.warn('map_3d: could not update the address bar', err);
    }
  }

  // ---- Steep terrain ------------------------------------------------------

  // The fetch, decode and classify of every class tile run in a worker
  // (map_3d_steep_worker.js), so a tilted view streaming tiles to the
  // horizon never stalls panning. The worker imports the two cores by their
  // hashed static URLs, which the page passes (data-steep-worker-scripts).
  const steepEnabled = !!(
    data.terrainClassTileUrl && terrainCore && data.steepWorkerUrl && typeof Worker === 'function'
  );
  const steepFilter = steepEnabled
    ? terrainCore.normaliseFilter({ ...terrainCore.DEFAULT_FILTER, mode: 'slope' })
    : null;

  if (steepEnabled) {
    const worker = new Worker(data.steepWorkerUrl || '');
    worker.postMessage({
      type: 'init',
      scripts: (data.steepWorkerScripts || '').split(' ').filter(Boolean),
      tileTemplate: data.terrainClassTileUrl,
    });
    let nextId = 1;
    /** @type {Map<number, {resolve: Function, reject: Function}>} */
    const pending = new Map();
    worker.addEventListener('message', (/** @type {MessageEvent} */ event) => {
      const { id, bitmap, error } = event.data || {};
      const waiter = pending.get(id);
      if (!waiter) {
        if (bitmap) bitmap.close();
        return;
      }
      pending.delete(id);
      if (bitmap) waiter.resolve({ data: bitmap });
      else waiter.reject(new Error(`map_3d: steep tile failed: ${error}`));
    });

    maplibre.addProtocol(
      'terrainfilter',
      /**
       * @param {{url: string}} params
       * @param {AbortController} abortController
       */
      (params, abortController) => {
        const tile = terrainCore.parseProtocolUrl(params.url);
        if (!tile) return Promise.reject(new Error(`map_3d: not a tile url: ${params.url}`));
        const id = nextId;
        nextId += 1;
        // Never an empty answer: MapLibre 4.7.1 leaves such a tile pending,
        // so the worker always paints a tile, transparent when there is none.
        const answer = new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
        abortController.signal.addEventListener('abort', () => {
          const waiter = pending.get(id);
          pending.delete(id);
          worker.postMessage({ type: 'abort', id });
          if (waiter) waiter.reject(new DOMException('Aborted', 'AbortError'));
        });
        worker.postMessage({ type: 'tile', id, z: tile.z, x: tile.x, y: tile.y });
        return answer;
      },
    );
  }

  // ---- Data ---------------------------------------------------------------

  /**
   * GET a GeoJSON feed, or null on any failure.
   *
   * @param {string} url
   * @returns {Promise<any>}
   */
  async function getJson(url) {
    try {
      const response = await fetch(url, { credentials: 'same-origin' });
      return response.ok ? await response.json() : null;
    } catch (err) {
      console.warn('map_3d: fetch failed', url, err);
      return null;
    }
  }

  const EMPTY = { type: 'FeatureCollection', features: [] };
  let toursTimer = 0;
  let toursBox = '';
  /** The last data set on each line source, by source id, for tap lookups. */
  /** @type {Record<string, any>} */
  const loaded = {};

  /** Ask for the tours around the camera, if it moved to a new box. */
  async function refreshTours() {
    if (!data.skiToursUrl || !layerState.tours || map.getZoom() < core.TOURS_MIN_ZOOM) return;
    const box = core.toursBbox(map.getCenter());
    if (box === toursBox) return;
    toursBox = box;
    const tours = await getJson(`${data.skiToursUrl}?bbox=${box}`);
    if (tours && box === toursBox) {
      loaded[TOURS_SOURCE] = tours;
      map.getSource(TOURS_SOURCE).setData(tours);
    }
  }

  /**
   * Fly to bounds, keeping the tilt and the heading.
   *
   * @param {[[number, number], [number, number]]|null} bounds
   */
  function flyToBounds(bounds) {
    if (!bounds) return;
    map.fitBounds(bounds, {
      padding: 80,
      maxZoom: 14.5,
      bearing: map.getBearing(),
      pitch: map.getPitch(),
    });
  }

  // ---- Layers -------------------------------------------------------------

  map.on('load', async () => {
    map.addSource(core.DEM_SOURCE_ID, core.demSource(data.demTileUrl));
    map.setTerrain(core.terrainSpec());
    map.setSky(core.skySpec());
    // Modelled sunlight from the north-west, under the basemap's labels and
    // over its hachures, so the relief reads as form. It shades from the
    // terrain's own elevation source, so it downloads nothing more.
    const firstLabel = map.getStyle().layers.find((/** @type {any} */ l) => l.type === 'symbol');
    map.addLayer(
      { id: SUNLIGHT_LAYER, type: 'hillshade', source: core.DEM_SOURCE_ID, paint: core.sunlightPaint() },
      firstLabel ? firstLabel.id : undefined,
    );

    if (steepEnabled) {
      map.addSource(STEEP_SOURCE, {
        type: 'raster',
        tiles: [terrainCore.protocolTileUrl(terrainCore.filterHash(steepFilter))],
        tileSize: terrainCore.TILE_SIZE,
        minzoom: terrainCore.MIN_ZOOM,
        maxzoom: terrainCore.MAX_ZOOM,
        bounds: terrainCore.COVERAGE_BOUNDS,
        attribution:
          '<a href="https://www.swisstopo.admin.ch/" target="_blank" rel="noopener">'
          + 'swisstopo — swissALTI3D</a>',
      });
      map.addLayer({
        id: STEEP_LAYER,
        type: 'raster',
        source: STEEP_SOURCE,
        minzoom: terrainCore.DRAW_MIN_ZOOM,
        layout: { visibility: visibility('steep') },
        paint: { 'raster-opacity': 1, 'raster-resampling': 'nearest', 'raster-fade-duration': 0 },
      });
    }

    core.WILDLIFE_LAYERS.forEach((/** @type {string} */ layer, /** @type {number} */ i) => {
      map.addSource(WILDLIFE_LAYERS[i], {
        type: 'raster',
        tiles: [core.wildlifeTileUrl(layer)],
        tileSize: 256,
        minzoom: 8,
        maxzoom: 18,
        bounds: [5.9, 45.8, 10.5, 47.9],
        attribution:
          '<a href="https://www.wildruhezonen.ch/" target="_blank" rel="noopener">'
          + 'Wildlife areas © BAFU / swisstopo</a>',
      });
      map.addLayer({
        id: WILDLIFE_LAYERS[i],
        type: 'raster',
        source: WILDLIFE_LAYERS[i],
        layout: { visibility: visibility('wildlife') },
        paint: { 'raster-opacity': 0.75 },
      });
    });

    const width = ['interpolate', ['linear'], ['zoom'], 10, 2, 13, 3, 15, 5];
    // The white casing under each line, 3 px wider at every stop. A zoom
    // expression must be the top level, so it is its own ramp, not width + 3.
    const casingWidth = ['interpolate', ['linear'], ['zoom'], 10, 5, 13, 6, 15, 8];
    const lineLayout = { 'line-cap': 'round', 'line-join': 'round' };
    const isTour = ['==', ['get', 'kind'], 'tour'];

    map.addSource(TOURS_SOURCE, { type: 'geojson', data: EMPTY, attribution: 'SAC ski tours © swisstopo' });
    map.addLayer({
      id: 'map-3d-tours-casing',
      type: 'line',
      source: TOURS_SOURCE,
      filter: isTour,
      layout: { ...lineLayout, visibility: visibility('tours') },
      paint: { 'line-color': COLOURS.casing, 'line-width': casingWidth },
    });
    map.addLayer({
      id: LINE_LAYERS[0],
      type: 'line',
      source: TOURS_SOURCE,
      filter: ['all', isTour, ['!=', ['get', 'climbing'], false]],
      layout: { ...lineLayout, visibility: visibility('tours') },
      paint: { 'line-color': COLOURS.tourClimb, 'line-width': width },
    });
    map.addLayer({
      id: LINE_LAYERS[1],
      type: 'line',
      source: TOURS_SOURCE,
      filter: ['all', isTour, ['==', ['get', 'climbing'], false]],
      layout: { ...lineLayout, visibility: visibility('tours') },
      paint: { 'line-color': COLOURS.tourDescent, 'line-width': width },
    });

    map.addSource(ROUTES_SOURCE, { type: 'geojson', data: EMPTY });
    map.addLayer({
      id: 'map-3d-routes-casing',
      type: 'line',
      source: ROUTES_SOURCE,
      layout: lineLayout,
      paint: { 'line-color': COLOURS.casing, 'line-width': casingWidth },
    });
    map.addLayer({
      id: LINE_LAYERS[2],
      type: 'line',
      source: ROUTES_SOURCE,
      layout: lineLayout,
      paint: { 'line-color': COLOURS.route, 'line-width': width },
    });

    map.addSource(RESORTS_SOURCE, { type: 'geojson', data: data.resortsUrl || EMPTY });
    map.addLayer({
      id: 'map-3d-resorts-pin',
      type: 'circle',
      source: RESORTS_SOURCE,
      paint: {
        'circle-radius': 6,
        'circle-color': COLOURS.resort,
        'circle-stroke-color': COLOURS.casing,
        'circle-stroke-width': 2,
      },
    });

    for (const id of LINE_LAYERS) {
      map.on('click', id, (/** @type {any} */ e) => {
        const feature = e.features && e.features[0];
        if (!feature) return;
        const source = feature.source || feature.layer && feature.layer.source;
        const uuid = feature.properties && feature.properties.uuid;
        flyToBounds(core.boundsForUuid(loaded[source], uuid) || core.lineBounds(feature.geometry));
      });
      map.on('mouseenter', id, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', id, () => { map.getCanvas().style.cursor = ''; });
    }

    map.on('moveend', () => {
      clearTimeout(toursTimer);
      toursTimer = window.setTimeout(refreshTours, TOURS_DEBOUNCE_MS);
      writeViewUrl();
    });
    refreshTours();

    if (data.routesUrl) {
      const routes = await getJson(data.routesUrl);
      if (routes) {
        loaded[ROUTES_SOURCE] = routes;
        map.getSource(ROUTES_SOURCE).setData(routes);
        if (wantedRoute) flyToBounds(core.boundsForUuid(routes, wantedRoute));
      }
    }
  });

  // ---- Layer switches -----------------------------------------------------

  /** The map layers each switch shows and hides. */
  /** @type {Record<string, string[]>} */
  const SWITCH_LAYERS = {
    steep: [STEEP_LAYER],
    tours: TOUR_LAYERS,
    wildlife: WILDLIFE_LAYERS,
  };

  for (const key of core.LAYER_KEYS) {
    const input = /** @type {HTMLInputElement|null} */ (document.getElementById(`map-3d-${key}`));
    if (!input) continue;
    input.checked = !!layerState[key];
    input.addEventListener('change', () => {
      layerState[key] = input.checked;
      for (const id of SWITCH_LAYERS[key]) {
        if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', visibility(key));
      }
      if (key === 'tours') refreshTours();
      writeViewUrl();
    });
  }

  // ---- Share --------------------------------------------------------------

  const shareButton = document.getElementById('map-3d-share');
  const shareStatus = document.getElementById('map-3d-share-status');
  const share = /** @type {any} */ (self).pwaShare;
  if (shareButton && shareStatus && share && self.pwaStrings) {
    const STRINGS = self.pwaStrings.read('map-3d-strings-template', {
      copied: 'Link copied',
      failed: 'Could not copy the link',
    });
    let statusTimer = 0;
    shareButton.addEventListener('click', async () => {
      const outcome = await share.shareOrCopy(viewUrl(), document.title, 'url');
      if (outcome !== 'copied' && outcome !== 'failed') return;
      shareStatus.textContent = STRINGS[outcome];
      clearTimeout(statusTimer);
      statusTimer = window.setTimeout(() => { shareStatus.textContent = ''; }, 2500);
    });
  }
})();
