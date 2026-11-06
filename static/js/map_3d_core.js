// @ts-check
/*
 * static/js/map_3d_core.js — the 3-D terrain map's pure parts (SNOW-1438).
 *
 * static/js/map_3d.js draws /map/3d/, a sandbox behind the map_3d flag: the
 * relief in 3-D with MapLibre's own terrain, and Steep terrain, SAC's ski
 * tours, the reader's own routes and the resorts draped over it
 * (docs/decisions/a-3d-map-is-maplibre-terrain.md). What can be decided
 * without a map or a DOM is here, so Vitest can pin it:
 *
 *   - the elevation source and terrain spec handed to MapLibre, and the
 *     loader behind it that falls back from Mapterhorn to AWS;
 *   - the opening camera;
 *   - the box of tours asked for around the camera — a tilted view's own
 *     bounds reach the horizon, far past what /api/ski-tours.geojson
 *     answers (SKI_NETWORK_MAX_SPAN_DEG, 0.5°), so the box is the centre
 *     plus a fixed half-span;
 *   - the bounds of a tapped line, which the camera flies to. A tapped
 *     MapLibre feature is only the drawn fragment (clipped at tile edges)
 *     and a tour arrives as one feature per leg, so the bounds are the
 *     union of every loaded feature carrying the tapped uuid (or, for a
 *     route someone shared, its share `token`: routes.geojson gives a
 *     non-owner no uuid);
 *   - the layer switches' defaults, and FOEN's wildlife areas as swisstopo
 *     WMTS rasters;
 *   - the shared view: the camera and the switches, written to and read
 *     from the page's query string (`?at=lng,lat,zoom,pitch,bearing&layers=…`),
 *     so a copied link opens on the same view for whoever follows it, and
 *     the link itself: the page's address with the token that opens it, or
 *     a route share link into 3-D (`/routes/s/<token>/3d/?…`);
 *   - the frame-time probe (`?probe=1`, SNOW-1448): the camera passes it
 *     flies and the summary of the frame intervals it records, measured
 *     against the proposed budget of a median frame under 33 ms;
 *   - /map/'s frame watchdog (SNOW-1455): the budget for one window of
 *     about two seconds of moving time (a median under 33 ms), judged by
 *     frameWindowFails, and the watch that trips on three failing windows
 *     in a row. Loosened on 6 Nov 2026: the first cut also failed a p95
 *     over 66 ms after two windows, and laid flat the very phone whose
 *     probe run had passed (Hugo), because tiles arriving mid-gesture
 *     stall a frame without the map being slow. The probe's own
 *     verdict (frameStats().withinBudget) is not what the watch reads;
 *   - the main thread's half of the Steep terrain worker's protocol
 *     (map_3d_steep_worker.js): each tile request gets an id, the reply
 *     with that id settles it, and an abort tells the worker and rejects.
 *
 * Exports (frozen `self.pwaMap3dCore`):
 *
 *   DEM_SOURCE_ID / TERRAIN_EXAGGERATION / OPENING_CAMERA / MAX_PITCH / MAX_PIXEL_RATIO
 *   prefetchDemTiles(centre, mapZoom) / tileUrl(template, tile)
 *   skySpec() / sunlightPaint()
 *   workerTileBridge(worker)
 *   TOURS_MIN_ZOOM / TOURS_HALF_SPAN_DEG / COLOURS
 *   DEM_TILE_SIZE / DEM_PROTOCOL / DEM_FALLBACK_TILE_URL / DEM_FALLBACK_AFTER
 *   demSource() / parseDemUrl(url) / createDemLoader(options)
 *   terrainSpec()
 *   toursBbox(centre, halfSpan?)
 *   lineBounds(geometry)
 *   boundsFor(collection, key, value)
 *   LAYER_KEYS / DEFAULT_LAYERS / WILDLIFE_LAYERS
 *   wildlifeTileUrl(layer)
 *   viewQuery(camera, layers)
 *   parseView(search)
 *   pageUrl(origin, pathname, tokens, query)
 *   routeShare3dUrl(shareUrl, query)
 *   FRAME_BUDGET_MS / PROBE_PASSES / frameStats(intervals, budgetMs?)
 *   FRAME_WINDOW_MS / FRAME_WINDOWS_TO_TRIP
 *   frameWindowFails(stats) / createFrameWatch()
 */
(function () {
  'use strict';

  /** The raster-dem source's id. */
  const DEM_SOURCE_ID = 'map-3d-dem';

  /** The elevation tiles' size in pixels, and the deepest zoom asked for. */
  const DEM_TILE_SIZE = 512;
  const DEM_MAX_ZOOM = 15;

  /**
   * The page's elevation protocol, the fallback tiles (AWS's open Terrarium
   * set: 256 px, global, z0–15, no token) and how many Mapterhorn failures
   * in a row send every later tile straight to them.
   */
  const DEM_PROTOCOL = 'map3ddem';
  const DEM_FALLBACK_TILE_URL = 'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png';
  const DEM_FALLBACK_AFTER = 3;

  /**
   * How much the relief is stretched upwards. 1 is true scale; a little
   * more reads better on a phone-sized screen, and more than 1.5 starts to
   * make 30° look like 40°, which on a map about steepness is a lie.
   */
  const TERRAIN_EXAGGERATION = 1.3;

  /** Where the page opens: over Saas-Fee, looking up the Allalin. */
  const OPENING_CAMERA = Object.freeze({
    center: Object.freeze([7.87, 46.07]),
    zoom: 12.6,
    pitch: 64,
    bearing: 215,
  });

  /**
   * The canvas's pixel-ratio ceiling. A 3x phone draws 2.25x the pixels of
   * 2x for detail nobody sees on a tilted map, and every frame pays for it.
   */
  const MAX_PIXEL_RATIO = 2;

  /** MapLibre's ceiling is 85; past this the horizon eats the screen. */
  const MAX_PITCH = 80;

  /** Tours are asked for from here up, as on /map/ (ski_tours_core MINZOOM is 9; the box is small). */
  const TOURS_MIN_ZOOM = 10;

  /**
   * Half the side of the box of tours asked for, in degrees. The endpoint
   * refuses a span over 0.5°, so this stays under 0.25.
   */
  const TOURS_HALF_SPAN_DEG = 0.24;

  /**
   * Line and pin colours. MapLibre paint values, so they are hex here
   * (AGENTS.md design-system rule 3 exempts paint properties). They match
   * /map/: SAC's climb and descent blues (route_legs_core), a reader's own
   * route in fuchsia (route_drawing_core) and the resort pin.
   */
  const COLOURS = Object.freeze({
    tourClimb: '#1d4ed8',
    tourDescent: '#38bdf8',
    route: '#c026d3',
    casing: '#ffffff',
    resort: '#1a1a1a',
  });

  /**
   * The sky and the haze toward the horizon: an alpine blue overhead fading
   * to a pale horizon, with the haze kept off the near ground so the
   * foreground stays crisp. MapLibre paint values, so hex here.
   *
   * @returns {Record<string, unknown>}
   */
  function skySpec() {
    return {
      'sky-color': '#5b9bd5',
      'horizon-color': '#dbe9f6',
      'fog-color': '#e6eef7',
      'sky-horizon-blend': 0.8,
      'horizon-fog-blend': 0.9,
      'fog-ground-blend': 0.15,
      'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 10, 1, 14, 0.6],
    };
  }

  /**
   * The sunlight layer's paint: a north-west sun fixed to the map (so the
   * light does not swing as the camera turns), soft: enough to model the
   * ridges, with shade light enough that swisstopo's hachures read through.
   *
   * @returns {Record<string, unknown>}
   */
  function sunlightPaint() {
    return {
      'hillshade-exaggeration': 0.3,
      'hillshade-illumination-anchor': 'map',
      'hillshade-illumination-direction': 315,
      'hillshade-shadow-color': '#4a5878',
      'hillshade-highlight-color': '#ffffff',
      'hillshade-accent-color': '#7a8799',
    };
  }

  /**
   * The elevation source. Its tiles go through the page's `map3ddem://`
   * protocol (createDemLoader): Mapterhorn's Terrarium-encoded 512 px
   * tiles first (in production our own copy of them, MAP_3D_DEM_TILE_URL),
   * AWS's when they fail. Mapterhorn goes to z17 over
   * Switzerland; z15 (about 2 m a pixel) is past what the terrain mesh can
   * show, and each zoom past it quadruples the tiles.
   *
   * @returns {{type: 'raster-dem', tiles: string[], encoding: 'terrarium', tileSize: number, maxzoom: number, attribution: string}}
   */
  function demSource() {
    return {
      type: 'raster-dem',
      tiles: [`${DEM_PROTOCOL}://{z}/{x}/{y}`],
      encoding: 'terrarium',
      tileSize: DEM_TILE_SIZE,
      maxzoom: DEM_MAX_ZOOM,
      attribution: '<a href="https://mapterhorn.com/attribution" target="_blank" rel="noopener">© Mapterhorn</a>'
        + ' · <a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md" target="_blank" rel="noopener">Terrain Tiles</a>',
    };
  }

  /**
   * A `map3ddem://z/x/y` URL's tile, or null for anything else.
   *
   * @param {string} url
   * @returns {{z: number, x: number, y: number}|null}
   */
  function parseDemUrl(url) {
    const match = new RegExp(`^${DEM_PROTOCOL}://(\\d+)/(\\d+)/(\\d+)$`).exec(url);
    if (!match) return null;
    return { z: Number(match[1]), x: Number(match[2]), y: Number(match[3]) };
  }

  /**
   * Loads one elevation tile for the `map3ddem://` protocol: Mapterhorn
   * first, then AWS's Terrarium tile of the same z/x/y. A Mapterhorn tile is
   * 512 px and an AWS one 256 px over the same ground, so the AWS tile is
   * enlarged (`enlarge`, nearest-neighbour: Terrarium heights are encoded
   * across the three channels, so blending neighbours would invent heights).
   *
   * A 404 is a hole in Mapterhorn's pyramid, and a 204 a tile outside our
   * copy's supported area (SNOW-1444); either falls back for that tile
   * only. A network failure or a 5xx counts as Mapterhorn being down: after
   * DEM_FALLBACK_AFTER in a row the loader goes straight to AWS for the rest
   * of the visit, so an outage does not cost every tile a failed request. A
   * Mapterhorn success before then resets the count; one that lands after
   * (a request already in flight) does not lift the latch.
   *
   * SNOW-1453: a 504 carrying `X-SW-Cache: miss` is the service worker's
   * own answer offline for a tile it does not hold
   * (`_synthesizedGatewayTimeout`), not Mapterhorn being down. It falls
   * back for that tile without counting, like a 404: otherwise three
   * misses past a downloaded area's edge would latch the loader to AWS,
   * and the area's pinned tiles would never be asked for again.
   *
   * @param {{primary: string, fallback: string, fetchTile: (url: string, signal: AbortSignal) => Promise<Response>, enlarge: (blob: Blob) => Promise<any>}} options
   * @returns {{load: (tile: {z: number, x: number, y: number}, signal: AbortSignal) => Promise<{data: any}>, usingFallback: () => boolean}}
   */
  function createDemLoader(options) {
    let failures = 0;
    // Once set, never cleared: a Mapterhorn request started before the
    // latch may still succeed after it, and must not reopen Mapterhorn.
    let latched = false;
    const fail = () => {
      failures += 1;
      if (failures >= DEM_FALLBACK_AFTER) latched = true;
    };
    return {
      async load(tile, signal) {
        if (!latched) {
          try {
            const response = await options.fetchTile(tileUrl(options.primary, tile), signal);
            // SNOW-1444: our copy covers the supported area only, and its
            // Worker answers 204 No Content for a tile outside it, the way
            // a PMTiles server answers for a tile the archive lacks. Like a
            // 404 that is "not here", so it falls back without counting.
            if (response.ok && response.status !== 204) {
              if (!latched) failures = 0;
              return { data: await response.arrayBuffer() };
            }
            if (response.status !== 404 && response.status !== 204 && !isOfflineMiss(response)) fail();
          } catch (err) {
            if (signal.aborted) throw err;
            fail();
          }
        }
        const response = await options.fetchTile(tileUrl(options.fallback, tile), signal);
        if (!response.ok) {
          throw new Error(`map_3d: no elevation tile ${tile.z}/${tile.x}/${tile.y} (${response.status})`);
        }
        return { data: await options.enlarge(await response.blob()) };
      },
      usingFallback: () => latched,
    };
  }

  /**
   * Whether a response is the service worker's offline miss: a 504 it
   * made up because it holds no copy and may not use the network
   * (static/js/sw.js `_synthesizedGatewayTimeout`, `X-SW-Cache: miss`).
   *
   * @param {Response} response
   * @returns {boolean}
   */
  function isOfflineMiss(response) {
    return response.status === 504 && !!response.headers && response.headers.get('X-SW-Cache') === 'miss';
  }

  /**
   * The elevation tiles a 3-D view around a point will ask for first, so
   * /map/'s 3D roundel can warm the browser's cache before the page opens.
   * MapLibre draws 512 px terrain about two zooms below the map's own, so
   * this is the 3x3 block at that zoom around the centre.
   *
   * @param {{lng: number, lat: number}} centre
   * @param {number} mapZoom The camera zoom the 3-D map will open at.
   * @returns {Array<{z: number, x: number, y: number}>}
   */
  function prefetchDemTiles(centre, mapZoom) {
    const z = Math.max(0, Math.min(DEM_MAX_ZOOM, Math.round(mapZoom) - 2));
    const n = 2 ** z;
    const lat = Math.max(-85, Math.min(85, centre.lat)) * Math.PI / 180;
    const cx = Math.floor(((centre.lng + 180) / 360) * n);
    const cy = Math.floor(((1 - Math.asinh(Math.tan(lat)) / Math.PI) / 2) * n);
    /** @type {Array<{z: number, x: number, y: number}>} */
    const out = [];
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dx = -1; dx <= 1; dx += 1) {
        const y = cy + dy;
        if (y < 0 || y >= n) continue;
        out.push({ z, x: (((cx + dx) % n) + n) % n, y });
      }
    }
    return out;
  }

  /**
   * One tile's URL from an XYZ template.
   *
   * @param {string} template
   * @param {{z: number, x: number, y: number}} tile
   * @returns {string}
   */
  function tileUrl(template, tile) {
    return template.replace('{z}', String(tile.z)).replace('{x}', String(tile.x)).replace('{y}', String(tile.y));
  }

  /**
   * The argument to `map.setTerrain`.
   *
   * @returns {{source: string, exaggeration: number}}
   */
  function terrainSpec() {
    return { source: DEM_SOURCE_ID, exaggeration: TERRAIN_EXAGGERATION };
  }

  /**
   * The `?bbox=` for the tours around a point, as `west,south,east,north`.
   *
   * @param {{lng: number, lat: number}} centre
   * @param {number} [halfSpan] Degrees either side; defaults to TOURS_HALF_SPAN_DEG.
   * @returns {string}
   */
  function toursBbox(centre, halfSpan) {
    const h = Math.min(typeof halfSpan === 'number' ? halfSpan : TOURS_HALF_SPAN_DEG, 0.25);
    const round = (/** @type {number} */ n) => Math.round(n * 1e5) / 1e5;
    return [
      round(centre.lng - h),
      round(Math.max(centre.lat - h, -90)),
      round(centre.lng + h),
      round(Math.min(centre.lat + h, 90)),
    ].join(',');
  }

  /**
   * The bounds of a line, `[[west, south], [east, north]]`, or null for a
   * geometry with no line in it.
   *
   * @param {{type: string, coordinates: any}|null|undefined} geometry
   * @returns {[[number, number], [number, number]]|null}
   */
  function lineBounds(geometry) {
    if (!geometry) return null;
    /** @type {number[][]} */
    let points = [];
    if (geometry.type === 'LineString') points = geometry.coordinates;
    else if (geometry.type === 'MultiLineString') points = [].concat(...geometry.coordinates);
    else return null;
    if (!Array.isArray(points) || points.length === 0) return null;
    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;
    for (const p of points) {
      if (p[0] < west) west = p[0];
      if (p[0] > east) east = p[0];
      if (p[1] < south) south = p[1];
      if (p[1] > north) north = p[1];
    }
    return [[west, south], [east, north]];
  }

  /**
   * The union of the line bounds of every feature in a collection whose
   * property `key` is `value`, or null when none has a line.
   *
   * @param {{features?: Array<{geometry?: any, properties?: any}>}|null|undefined} collection
   * @param {string} key `uuid`, or `token` for a shared route.
   * @param {unknown} value
   * @returns {[[number, number], [number, number]]|null}
   */
  function boundsFor(collection, key, value) {
    if (!collection || !Array.isArray(collection.features) || !value) return null;
    /** @type {[[number, number], [number, number]]|null} */
    let out = null;
    for (const feature of collection.features) {
      if (!feature.properties || feature.properties[key] !== value) continue;
      const b = lineBounds(feature.geometry);
      if (!b) continue;
      out = out
        ? [
          [Math.min(out[0][0], b[0][0]), Math.min(out[0][1], b[0][1])],
          [Math.max(out[1][0], b[1][0]), Math.max(out[1][1], b[1][1])],
        ]
        : b;
    }
    return out;
  }

  /** The page's layer switches, in the order the link lists them. */
  const LAYER_KEYS = Object.freeze(['steep', 'tours', 'wildlife']);

  /** Each switch's state when the link does not say: wildlife off, as on /map/. */
  const DEFAULT_LAYERS = Object.freeze({ steep: true, tours: true, wildlife: false });

  /**
   * FOEN's wildlife areas, as swisstopo's WMTS draws them: the wildlife rest
   * zones and the federal game reserves. Open government data, public and
   * token-less; the CSP already allows wmts.geo.admin.ch (SNOW-1260).
   */
  const WILDLIFE_LAYERS = Object.freeze([
    'ch.bafu.wrz-wildruhezonen_portal',
    'ch.bafu.wrz-jagdbanngebiete_select',
  ]);

  /**
   * The XYZ template of one swisstopo WMTS layer in Web Mercator.
   *
   * @param {string} layer The geo.admin layer id.
   * @returns {string}
   */
  function wildlifeTileUrl(layer) {
    return `https://wmts.geo.admin.ch/1.0.0/${layer}/default/current/3857/{z}/{x}/{y}.png`;
  }

  /**
   * The query string that reproduces a view: `at=lng,lat,zoom,pitch,bearing`
   * and `layers=` listing the switches that are on (empty when none is).
   *
   * @param {{center: {lng: number, lat: number}, zoom: number, pitch: number, bearing: number}} camera
   * @param {Record<string, boolean>} layers
   * @returns {string} Without the leading `?`.
   */
  function viewQuery(camera, layers) {
    const fixed = (/** @type {number} */ n, /** @type {number} */ d) => String(Number(n.toFixed(d)));
    // MapLibre's centre can sit in another world copy past ±180; wrap it
    // back, or parseView would refuse the link.
    const lng = ((((camera.center.lng + 180) % 360) + 360) % 360) - 180;
    const at = [
      fixed(lng, 5),
      fixed(camera.center.lat, 5),
      fixed(camera.zoom, 2),
      fixed(camera.pitch, 1),
      fixed(camera.bearing, 1),
    ].join(',');
    const on = LAYER_KEYS.filter((key) => layers[key]);
    return `at=${at}&layers=${on.join(',')}`;
  }

  /**
   * Read a view back from a query string. Anything malformed or out of
   * range is dropped rather than guessed at, so a mangled link still opens
   * the map.
   *
   * @param {string} search The page's `location.search`.
   * @returns {{camera: {center: [number, number], zoom: number, pitch: number, bearing: number}|null, layers: Record<string, boolean>}}
   */
  function parseView(search) {
    const params = new URLSearchParams(search);
    /** @type {{center: [number, number], zoom: number, pitch: number, bearing: number}|null} */
    let camera = null;
    const at = params.get('at');
    if (at) {
      const n = at.split(',').map(Number);
      const ok = n.length === 5 && n.every(Number.isFinite)
        && Math.abs(n[0]) <= 180 && Math.abs(n[1]) <= 90
        && n[2] >= 0 && n[2] <= 22 && n[3] >= 0 && n[3] <= MAX_PITCH;
      if (ok) camera = { center: [n[0], n[1]], zoom: n[2], pitch: n[3], bearing: n[4] };
    }
    /** @type {Record<string, boolean>} */
    const layers = { ...DEFAULT_LAYERS };
    const listed = params.get('layers');
    if (listed !== null) {
      const on = listed.split(',');
      for (const key of LAYER_KEYS) layers[key] = on.includes(key);
    }
    return { camera, layers };
  }

  /**
   * The main thread's half of the Steep terrain worker's protocol: one
   * `request(tile, signal)` per MapLibre tile. Each request posts
   * `{type: 'tile', id, z, x, y}` and settles on the worker's reply with
   * that id — `{data: bitmap}`, or an Error for `{id, error}`. An abort
   * posts `{type: 'abort', id}` and rejects with an AbortError; a reply that
   * arrives after it is dropped and its bitmap closed.
   *
   * @param {{postMessage: (msg: any) => void, addEventListener: (type: string, fn: (event: any) => void) => void}} worker
   * @returns {{request: (tile: {z: number, x: number, y: number}, signal: AbortSignal) => Promise<{data: any}>, pendingCount: () => number}}
   */
  function workerTileBridge(worker) {
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
    return {
      request(tile, signal) {
        const id = nextId;
        nextId += 1;
        const answer = /** @type {Promise<{data: any}>} */ (
          new Promise((resolve, reject) => pending.set(id, { resolve, reject }))
        );
        signal.addEventListener('abort', () => {
          const waiter = pending.get(id);
          pending.delete(id);
          worker.postMessage({ type: 'abort', id });
          if (waiter) waiter.reject(new DOMException('Aborted', 'AbortError'));
        });
        worker.postMessage({ type: 'tile', id, z: tile.z, x: tile.x, y: tile.y });
        return answer;
      },
      pendingCount: () => pending.size,
    };
  }

  /**
   * The page's own address for a view, carrying the token that opens it.
   *
   * `share` is the reader's Map3dShare token (apps/map/models.py), which
   * opens the page past the flag; `routeShare` is the route share the page
   * was opened on, which keeps that route on it. Either may be empty.
   *
   * @param {string} origin `location.origin`.
   * @param {string} pathname `location.pathname`.
   * @param {{share?: string, routeShare?: string}} tokens
   * @param {string} query viewQuery's output.
   * @returns {string}
   */
  function pageUrl(origin, pathname, tokens, query) {
    const params = [];
    if (tokens.share) params.push(`share=${encodeURIComponent(tokens.share)}`);
    if (tokens.routeShare) params.push(`route_share=${encodeURIComponent(tokens.routeShare)}`);
    params.push(query);
    return `${origin}${pathname}?${params.join('&')}`;
  }

  /**
   * A route share link into 3-D: `/routes/s/<token>/` (what routes'
   * share-create answers) plus `3d/` and the view, so the friend opens on
   * the sharer's camera and switches (routes:share_redirect_3d).
   *
   * @param {string} shareUrl The absolute route share link.
   * @param {string} query viewQuery's output.
   * @returns {string}
   */
  function routeShare3dUrl(shareUrl, query) {
    const url = new URL('3d/', shareUrl.endsWith('/') ? shareUrl : `${shareUrl}/`);
    url.search = query;
    return url.toString();
  }

  /**
   * The proposed frame budget (SNOW-1448): a median frame under 33 ms, which
   * is 30 frames a second, on a mid-range phone.
   */
  const FRAME_BUDGET_MS = 33;

  /**
   * The probe's camera passes. Each opens over Saas-Fee at OPENING_CAMERA's
   * tilt and heading, waits for every tile to arrive, then turns the camera
   * through a quarter circle at a steady rate while the frames are timed.
   * The turn brings new ground into view, so the pass pays for tiles and
   * draping as a reader's own panning does. The z9 pass is where the 3-D
   * control is proposed to switch off: the most ground a tilted view draws.
   */
  const PROBE_PASSES = Object.freeze([
    Object.freeze({ label: 'Saas-Fee z12.6', zoom: OPENING_CAMERA.zoom, turnDeg: 90, durationMs: 12000 }),
    Object.freeze({ label: 'Valais z9', zoom: 9, turnDeg: 90, durationMs: 12000 }),
  ]);

  /**
   * Summarise the intervals between animation frames, in milliseconds.
   *
   * The median is the budget's measure. The 95th percentile and the share of
   * frames over budget say whether a good median hides stutter. Percentiles
   * are nearest-rank, so every figure is an interval that happened.
   *
   * @param {number[]} intervals Milliseconds between consecutive frames.
   * @param {number} [budgetMs] The budget the frames are held to.
   * @returns {{frames: number, medianMs: number, p95Ms: number, worstMs: number,
   *   overBudget: number, fps: number, withinBudget: boolean}|null}
   *   Null when there are no usable intervals.
   */
  function frameStats(intervals, budgetMs) {
    const budget = budgetMs || FRAME_BUDGET_MS;
    const sorted = (intervals || [])
      .filter((ms) => Number.isFinite(ms) && ms > 0)
      .sort((a, b) => a - b);
    if (!sorted.length) return null;
    /** @param {number} p */
    const rank = (p) => sorted[Math.min(sorted.length - 1, Math.max(0, Math.ceil(p * sorted.length) - 1))];
    const round = (/** @type {number} */ ms) => Math.round(ms * 10) / 10;
    const medianMs = rank(0.5);
    const over = sorted.filter((ms) => ms > budget).length;
    return {
      frames: sorted.length,
      medianMs: round(medianMs),
      p95Ms: round(rank(0.95)),
      worstMs: round(sorted[sorted.length - 1]),
      overBudget: Math.round((over / sorted.length) * 100) / 100,
      fps: Math.round(1000 / medianMs),
      withinBudget: medianMs < budget,
    };
  }

  /** How much moving time one watchdog window holds, in milliseconds. */
  const FRAME_WINDOW_MS = 2000;

  /**
   * How many failing windows in a row lay /map/'s 3-D flat: about six
   * seconds of moving time under 30 fps.
   */
  const FRAME_WINDOWS_TO_TRIP = 3;

  /**
   * Whether one window of frames is over the budget: a median of
   * FRAME_BUDGET_MS or more. The p95 is not judged: a stall while tiles
   * arrive and drape lifts it on a phone that draws 3-D well (Hugo's
   * Mali-G715 run: median 16.7 ms, p95 50 ms over Saas-Fee), and only a
   * map that is slow throughout lifts the median. Reads frameStats'
   * output; no frames is not a failure.
   *
   * @param {{medianMs: number}|null|undefined} stats
   * @returns {boolean}
   */
  function frameWindowFails(stats) {
    if (!stats) return false;
    return stats.medianMs >= FRAME_BUDGET_MS;
  }

  /**
   * The frame watchdog's state. Intervals are pushed one at a time; once
   * they add up to FRAME_WINDOW_MS the window closes and is judged by
   * frameWindowFails. FRAME_WINDOWS_TO_TRIP failing windows in a row trip
   * it, and a passing window starts the count again. A window carries over
   * from one gesture to the next, and so does the count; only `reset`
   * clears them. Once tripped, it stays tripped until `reset`.
   *
   * @returns {{push: (intervalMs: number) => boolean, reset: () => void}}
   *   `push` answers whether the watch has tripped.
   */
  function createFrameWatch() {
    /** @type {number[]} */
    let frames = [];
    let elapsed = 0;
    let failing = 0;
    let tripped = false;
    return {
      push(intervalMs) {
        if (tripped) return true;
        if (!Number.isFinite(intervalMs) || intervalMs <= 0) return false;
        frames.push(intervalMs);
        elapsed += intervalMs;
        if (elapsed < FRAME_WINDOW_MS) return false;
        failing = frameWindowFails(frameStats(frames)) ? failing + 1 : 0;
        frames = [];
        elapsed = 0;
        if (failing >= FRAME_WINDOWS_TO_TRIP) tripped = true;
        return tripped;
      },
      reset() {
        frames = [];
        elapsed = 0;
        failing = 0;
        tripped = false;
      },
    };
  }

  self.pwaMap3dCore = Object.freeze({
    FRAME_BUDGET_MS: FRAME_BUDGET_MS,
    FRAME_WINDOW_MS: FRAME_WINDOW_MS,
    FRAME_WINDOWS_TO_TRIP: FRAME_WINDOWS_TO_TRIP,
    frameWindowFails: frameWindowFails,
    createFrameWatch: createFrameWatch,
    PROBE_PASSES: PROBE_PASSES,
    frameStats: frameStats,
    DEM_SOURCE_ID: DEM_SOURCE_ID,
    TERRAIN_EXAGGERATION: TERRAIN_EXAGGERATION,
    OPENING_CAMERA: OPENING_CAMERA,
    MAX_PITCH: MAX_PITCH,
    MAX_PIXEL_RATIO: MAX_PIXEL_RATIO,
    prefetchDemTiles: prefetchDemTiles,
    skySpec: skySpec,
    sunlightPaint: sunlightPaint,
    workerTileBridge: workerTileBridge,
    tileUrl: tileUrl,
    TOURS_MIN_ZOOM: TOURS_MIN_ZOOM,
    TOURS_HALF_SPAN_DEG: TOURS_HALF_SPAN_DEG,
    COLOURS: COLOURS,
    DEM_TILE_SIZE: DEM_TILE_SIZE,
    DEM_PROTOCOL: DEM_PROTOCOL,
    DEM_FALLBACK_TILE_URL: DEM_FALLBACK_TILE_URL,
    DEM_FALLBACK_AFTER: DEM_FALLBACK_AFTER,
    demSource: demSource,
    parseDemUrl: parseDemUrl,
    createDemLoader: createDemLoader,
    terrainSpec: terrainSpec,
    toursBbox: toursBbox,
    lineBounds: lineBounds,
    boundsFor: boundsFor,
    LAYER_KEYS: LAYER_KEYS,
    DEFAULT_LAYERS: DEFAULT_LAYERS,
    WILDLIFE_LAYERS: WILDLIFE_LAYERS,
    wildlifeTileUrl: wildlifeTileUrl,
    viewQuery: viewQuery,
    parseView: parseView,
    pageUrl: pageUrl,
    routeShare3dUrl: routeShare3dUrl,
  });
})();
