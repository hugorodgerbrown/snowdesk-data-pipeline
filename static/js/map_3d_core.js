// @ts-check
/*
 * static/js/map_3d_core.js — the 3D terrain map's pure parts (SNOW-1438).
 *
 * /map/'s 3D view (map_view_3d.js, behind the map_3d and map_modes flags)
 * draws the relief with MapLibre's own terrain on the swisstopo winter map
 * (docs/decisions/a-3d-map-is-maplibre-terrain.md). SNOW-1450 retired the
 * /map/3d/ sandbox this file was first written for, and its own parts went
 * with it. What can be decided without a map or a DOM is here, so Vitest
 * can pin it:
 *
 *   - the elevation source and terrain spec handed to MapLibre, and the
 *     loader behind it that falls back from Mapterhorn to AWS;
 *   - the opening camera;
 *   - a route share link into 3D (`/routes/s/<token>/3d/`), which /map/'s
 *     route Shares copy while the map is in 3D;
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
 *   - the 3D test's verdict (SNOW-1456): each pass's intervals go
 *     through a fresh createFrameWatch, the watchdog's own rule, so the
 *     test and the fallback cannot disagree. judgePass says whether a
 *     pass was measured (one full FRAME_WINDOW_MS of frames at least),
 *     whether it kept up and how many of its windows were slow;
 *     testVerdict turns the passes into smooth, stutter, slow or
 *     unmeasured. The p95 is never read.
 *
 * Exports (frozen `self.pwaMap3dCore`):
 *
 *   DEM_SOURCE_ID / TERRAIN_EXAGGERATION / OPENING_CAMERA / MAX_PITCH / MAX_PIXEL_RATIO
 *   prefetchDemTiles(centre, mapZoom) / tileUrl(template, tile)
 *   skySpec() / sunlightPaint()
 *   DEM_TILE_SIZE / DEM_PROTOCOL / DEM_FALLBACK_TILE_URL / DEM_FALLBACK_AFTER
 *   demSource() / parseDemUrl(url) / createDemLoader(options)
 *   terrainSpec()
 *   routeShare3dUrl(shareUrl, query)
 *   FRAME_BUDGET_MS / PROBE_PASSES / frameStats(intervals, budgetMs?)
 *   FRAME_WINDOW_MS / FRAME_WINDOWS_TO_TRIP
 *   frameWindowFails(stats) / createFrameWatch()
 *   TEST_VERDICTS / judgePass(intervals) / testVerdict(passes)
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
   * A route share link into 3D: `/routes/s/<token>/` (what routes'
   * share-create answers) plus `3d/`, which opens /map/'s 3D view on the
   * route (routes:share_redirect_3d). /map/'s route Shares copy it while
   * the map is in 3D (SNOW-1450).
   *
   * @param {string} shareUrl The absolute route share link.
   * @param {string} query A query to carry, such as `at=…`, or `''`.
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

  /**
   * The verdicts a measured 3D test gives (SNOW-1456), as POST
   * /api/map-3d-test/ accepts them (apps/map/models.py Map3dDeviceTest).
   * `unmeasured` is not one: an unmeasured run is never sent.
   */
  const TEST_VERDICTS = Object.freeze(['smooth', 'stutter', 'slow']);

  /**
   * Judge one pass of the 3D test by the watchdog's own rule.
   *
   * A pass whose usable intervals add up to less than one FRAME_WINDOW_MS
   * was not measured: the watch never closed a window on it. A measured
   * pass keeps up unless a fresh createFrameWatch, fed every interval,
   * trips; `windows` and `slowWindows` count the closed windows and those
   * frameWindowFails, for the Details.
   *
   * @param {number[]} intervals Milliseconds between consecutive frames.
   * @returns {{measured: boolean, keptUp: boolean, windows: number, slowWindows: number}}
   */
  function judgePass(intervals) {
    const usable = (intervals || []).filter((ms) => Number.isFinite(ms) && ms > 0);
    const watch = createFrameWatch();
    let tripped = false;
    let windows = 0;
    let slowWindows = 0;
    /** @type {number[]} */
    let frames = [];
    let elapsed = 0;
    for (const ms of usable) {
      if (watch.push(ms)) tripped = true;
      frames.push(ms);
      elapsed += ms;
      if (elapsed < FRAME_WINDOW_MS) continue;
      windows += 1;
      if (frameWindowFails(frameStats(frames))) slowWindows += 1;
      frames = [];
      elapsed = 0;
    }
    const measured = windows > 0;
    return { measured, keptUp: measured && !tripped, windows, slowWindows };
  }

  /**
   * The 3D test's verdict over its passes, in PROBE_PASSES' order: every
   * pass keeps up, `smooth`; only the last (the z9 pass, the most ground a
   * tilted view draws) falls behind, `stutter`; otherwise `slow`. Any pass
   * not measured, or no passes at all, makes the run `unmeasured`.
   *
   * @param {Array<{intervals: number[]}>} passes
   * @returns {'smooth'|'stutter'|'slow'|'unmeasured'}
   */
  function testVerdict(passes) {
    const judged = (passes || []).map((pass) => judgePass(pass && pass.intervals));
    if (!judged.length || judged.some((pass) => !pass.measured)) return 'unmeasured';
    if (judged.every((pass) => pass.keptUp)) return 'smooth';
    const last = judged.length - 1;
    if (last > 0 && judged.slice(0, last).every((pass) => pass.keptUp)) return 'stutter';
    return 'slow';
  }

  self.pwaMap3dCore = Object.freeze({
    TEST_VERDICTS: TEST_VERDICTS,
    judgePass: judgePass,
    testVerdict: testVerdict,
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
    tileUrl: tileUrl,
    DEM_TILE_SIZE: DEM_TILE_SIZE,
    DEM_PROTOCOL: DEM_PROTOCOL,
    DEM_FALLBACK_TILE_URL: DEM_FALLBACK_TILE_URL,
    DEM_FALLBACK_AFTER: DEM_FALLBACK_AFTER,
    demSource: demSource,
    parseDemUrl: parseDemUrl,
    createDemLoader: createDemLoader,
    terrainSpec: terrainSpec,
    routeShare3dUrl: routeShare3dUrl,
  });
})();
