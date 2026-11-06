/*
 * tests/js/test_map_3d_core.js — the 3D terrain map's pure parts (SNOW-1438).
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the DEM source is Terrarium-encoded and the terrain spec names it;
 *   - the exaggeration stays modest, so 30° does not read as 40°;
 *   - the tours box never spans more than /api/ski-tours.geojson answers
 *     (0.5°), whatever half-span is asked for;
 *   - a tapped line's bounds cover LineString and MultiLineString, and
 *     anything else is null rather than a camera move to nowhere;
 *   - a tap frames the whole route or tour (every leg with its uuid), not
 *     the clipped fragment MapLibre hands back;
 *   - the frame-time probe's summary (SNOW-1448) uses intervals that
 *     happened, ignores junk, and holds the median to the 33 ms budget;
 *   - /map/'s frame watchdog (SNOW-1455): a window fails at a median of 33 ms
 *     or a p95 over 66 ms, closes at about 2 s, and two bad ones in a row trip
 *     the watch;
 *   - the Steep terrain worker bridge settles each request on the reply
 *     with its own id, and an abort tells the worker, rejects, and closes
 *     a bitmap that arrives too late.
 */

import { describe, expect, it, vi } from 'vitest';

import '../../static/js/map_3d_core.js';

const core = window.pwaMap3dCore;

describe('demSource / terrainSpec', () => {
  it('builds a Terrarium raster-dem source on the page\'s elevation protocol', () => {
    const source = core.demSource();
    expect(source).toMatchObject({
      type: 'raster-dem',
      tiles: ['map3ddem://{z}/{x}/{y}'],
      encoding: 'terrarium',
      tileSize: 512,
      maxzoom: 15,
    });
    expect(source.attribution).toContain('Mapterhorn');
    expect(source.attribution).toContain('Terrain Tiles');
  });

  it('reads a tile back from a protocol url, and nothing else', () => {
    expect(core.parseDemUrl('map3ddem://12/2133/1458')).toEqual({ z: 12, x: 2133, y: 1458 });
    expect(core.parseDemUrl('https://tiles.example/12/2133/1458.webp')).toBeNull();
    expect(core.parseDemUrl('map3ddem://12/x/1458')).toBeNull();
  });

  it('points the terrain at the DEM source with a modest exaggeration', () => {
    const spec = core.terrainSpec();
    expect(spec.source).toBe(core.DEM_SOURCE_ID);
    expect(spec.exaggeration).toBeGreaterThanOrEqual(1);
    expect(spec.exaggeration).toBeLessThanOrEqual(1.5);
  });
});

describe('toursBbox', () => {
  /** @param {string} box */
  const spans = (box) => {
    const [w, s, e, n] = box.split(',').map(Number);
    return [e - w, n - s];
  };

  it('is the centre plus the default half-span, west,south,east,north', () => {
    expect(core.toursBbox({ lng: 7.87, lat: 46.07 })).toBe('7.63,45.83,8.11,46.31');
  });

  it('never asks for more than the endpoint answers', () => {
    for (const box of [core.toursBbox({ lng: 7, lat: 46 }, 1), core.toursBbox({ lng: 7, lat: 46 })]) {
      const [width, height] = spans(box);
      expect(width).toBeLessThanOrEqual(0.5);
      expect(height).toBeLessThanOrEqual(0.5);
    }
  });

  it('keeps latitude on the globe', () => {
    const [, s, , n] = core.toursBbox({ lng: 0, lat: 89.9 }).split(',').map(Number);
    expect(n).toBe(90);
    expect(s).toBeCloseTo(89.66, 5);
  });
});

describe('lineBounds', () => {
  it('bounds a LineString', () => {
    expect(core.lineBounds({
      type: 'LineString',
      coordinates: [[7.9, 46.1, 3000], [7.8, 46.0, 2000], [7.85, 46.2]],
    })).toEqual([[7.8, 46.0], [7.9, 46.2]]);
  });

  it('bounds every part of a MultiLineString', () => {
    expect(core.lineBounds({
      type: 'MultiLineString',
      coordinates: [[[7, 46], [7.1, 46.1]], [[6.9, 46.3], [7.2, 45.9]]],
    })).toEqual([[6.9, 45.9], [7.2, 46.3]]);
  });

  it.each([
    [null],
    [undefined],
    [{ type: 'Point', coordinates: [7, 46] }],
    [{ type: 'LineString', coordinates: [] }],
  ])('is null for %j', (geometry) => {
    expect(core.lineBounds(geometry)).toBeNull();
  });
});

describe('boundsFor', () => {
  const legs = {
    type: 'FeatureCollection',
    features: [
      { properties: { uuid: 'a' }, geometry: { type: 'LineString', coordinates: [[7, 46], [7.1, 46.1]] } },
      { properties: { uuid: 'b' }, geometry: { type: 'LineString', coordinates: [[9, 47], [9.1, 47.1]] } },
      { properties: { uuid: 'a' }, geometry: { type: 'LineString', coordinates: [[7.1, 46.1], [7.3, 45.9]] } },
      { properties: { uuid: 'a', kind: 'summit' }, geometry: { type: 'Point', coordinates: [8, 48] } },
    ],
  };

  it('unions every leg with the uuid, and skips other uuids and points', () => {
    expect(core.boundsFor(legs, 'uuid', 'a')).toEqual([[7, 45.9], [7.3, 46.1]]);
  });

  it.each([
    [legs, 'missing'],
    [legs, ''],
    [null, 'a'],
    [{}, 'a'],
  ])('is null when nothing matches (%#)', (collection, uuid) => {
    expect(core.boundsFor(collection, 'uuid', uuid)).toBeNull();
  });

  it('matches a shared route by its token, which carries no uuid', () => {
    const shared = {
      features: [
        { properties: { token: 't1', pending: true }, geometry: { type: 'LineString', coordinates: [[8, 46], [8.2, 46.3]] } },
        { properties: { uuid: 't1' }, geometry: { type: 'LineString', coordinates: [[9, 47], [9.1, 47.1]] } },
      ],
    };
    expect(core.boundsFor(shared, 'token', 't1')).toEqual([[8, 46], [8.2, 46.3]]);
  });
});

describe('pageUrl', () => {
  it('carries the Map3dShare token, then the route share, then the view', () => {
    expect(core.pageUrl('https://s.example', '/map/3d/', { share: 'a b', routeShare: 'r1' }, 'at=1&layers=')).toBe(
      'https://s.example/map/3d/?share=a%20b&route_share=r1&at=1&layers=',
    );
  });

  it('leaves out an empty token', () => {
    expect(core.pageUrl('https://s.example', '/map/3d/', { share: '', routeShare: '' }, 'at=1')).toBe(
      'https://s.example/map/3d/?at=1',
    );
  });
});

describe('routeShare3dUrl', () => {
  it('hangs 3d/ and the view off the route share link', () => {
    expect(core.routeShare3dUrl('https://s.example/routes/s/tok/', 'at=7,46,12,60,0&layers=tours')).toBe(
      'https://s.example/routes/s/tok/3d/?at=7,46,12,60,0&layers=tours',
    );
  });

  it('copes with a link without its trailing slash', () => {
    expect(core.routeShare3dUrl('https://s.example/routes/s/tok', 'at=1')).toBe(
      'https://s.example/routes/s/tok/3d/?at=1',
    );
  });
});

describe('wildlifeTileUrl', () => {
  it('builds a swisstopo WMTS Web Mercator template for each wildlife layer', () => {
    for (const layer of core.WILDLIFE_LAYERS) {
      expect(core.wildlifeTileUrl(layer)).toBe(
        `https://wmts.geo.admin.ch/1.0.0/${layer}/default/current/3857/{z}/{x}/{y}.png`,
      );
    }
  });
});

describe('viewQuery / parseView', () => {
  const camera = { center: { lng: 7.871234567, lat: 46.0712345 }, zoom: 12.6789, pitch: 64.04, bearing: -145.26 };

  it('writes the camera, rounded, and the switches that are on', () => {
    expect(core.viewQuery(camera, { steep: true, tours: false, wildlife: true })).toBe(
      'at=7.87123,46.07123,12.68,64,-145.3&layers=steep,wildlife',
    );
  });

  it('round-trips a view', () => {
    const query = core.viewQuery(camera, { steep: false, tours: true, wildlife: true });
    expect(core.parseView(`?${query}`)).toEqual({
      camera: { center: [7.87123, 46.07123], zoom: 12.68, pitch: 64, bearing: -145.3 },
      layers: { steep: false, tours: true, wildlife: true },
    });
  });

  it('keeps an empty layers list as every switch off', () => {
    expect(core.parseView('?layers=').layers).toEqual({ steep: false, tours: false, wildlife: false });
  });

  it('falls back to the defaults with no query', () => {
    expect(core.parseView('')).toEqual({ camera: null, layers: { ...core.DEFAULT_LAYERS } });
  });

  it.each([
    'at=7,46,12',
    'at=a,46,12,60,0',
    'at=200,46,12,60,0',
    'at=7,46,12,89,0',
  ])('drops a malformed camera (%s)', (query) => {
    expect(core.parseView(`?${query}`).camera).toBeNull();
  });
});

describe('viewQuery longitude', () => {
  it('wraps a centre from another world copy back into [-180, 180]', () => {
    const query = core.viewQuery(
      { center: { lng: 367.87, lat: 46.07 }, zoom: 12, pitch: 60, bearing: 0 },
      core.DEFAULT_LAYERS,
    );
    expect(core.parseView(`?${query}`).camera.center).toEqual([7.87, 46.07]);
  });
});

describe('MAX_PIXEL_RATIO', () => {
  it('caps the canvas at 2x', () => {
    expect(core.MAX_PIXEL_RATIO).toBe(2);
  });
});

describe('prefetchDemTiles / tileUrl', () => {
  it('returns the 3x3 block two zooms below the map, around the centre', () => {
    const tiles = core.prefetchDemTiles({ lng: 7.87, lat: 46.07 }, 12.6);
    expect(tiles).toHaveLength(9);
    expect(new Set(tiles.map((t) => t.z))).toEqual(new Set([11]));
    // The centre tile of z11 over Saas-Fee.
    expect(tiles[4]).toEqual({ z: 11, x: 1068, y: 728 });
  });

  it('never asks past the DEM source maxzoom', () => {
    const tiles = core.prefetchDemTiles({ lng: 7.87, lat: 46.07 }, 20);
    expect(tiles.every((t) => t.z === 15)).toBe(true);
  });

  it('wraps x across the antimeridian and drops rows off the world', () => {
    const tiles = core.prefetchDemTiles({ lng: 179.99, lat: 85 }, 4);
    expect(tiles.every((t) => t.x >= 0 && t.x < 4)).toBe(true);
    expect(tiles.every((t) => t.y >= 0)).toBe(true);
    expect(tiles.length).toBeLessThan(9);
  });

  it('fills an XYZ template', () => {
    expect(core.tileUrl('https://t.example/{z}/{x}/{y}.webp', { z: 1, x: 2, y: 3 }))
      .toBe('https://t.example/1/2/3.webp');
  });
});

describe('skySpec / sunlightPaint', () => {
  it('keeps the haze off the near ground', () => {
    const sky = core.skySpec();
    expect(sky['fog-ground-blend']).toBeLessThan(0.3);
    expect(sky['sky-color']).toMatch(/^#[0-9a-f]{6}$/);
  });

  it('lights from the north-west, fixed to the map, softly', () => {
    const paint = core.sunlightPaint();
    expect(paint['hillshade-illumination-direction']).toBe(315);
    expect(paint['hillshade-illumination-anchor']).toBe('map');
    expect(paint['hillshade-exaggeration']).toBeLessThanOrEqual(0.35);
  });
});

describe('workerTileBridge', () => {
  /** A worker stand-in: records what is posted and lets a test reply. */
  function fakeWorker() {
    let listener = null;
    return {
      posted: [],
      postMessage(msg) {
        this.posted.push(msg);
      },
      addEventListener(type, fn) {
        if (type === 'message') listener = fn;
      },
      reply(data) {
        listener({ data });
      },
    };
  }

  it('posts one tile request per call and settles each on its own id', async () => {
    const worker = fakeWorker();
    const bridge = core.workerTileBridge(worker);
    const a = bridge.request({ z: 12, x: 1, y: 2 }, new AbortController().signal);
    const b = bridge.request({ z: 12, x: 3, y: 4 }, new AbortController().signal);
    expect(worker.posted).toEqual([
      { type: 'tile', id: 1, z: 12, x: 1, y: 2 },
      { type: 'tile', id: 2, z: 12, x: 3, y: 4 },
    ]);
    const second = { close: vi.fn() };
    const first = { close: vi.fn() };
    worker.reply({ id: 2, bitmap: second });
    worker.reply({ id: 1, bitmap: first });
    await expect(a).resolves.toEqual({ data: first });
    await expect(b).resolves.toEqual({ data: second });
    expect(bridge.pendingCount()).toBe(0);
  });

  it('rejects on a worker error', async () => {
    const worker = fakeWorker();
    const bridge = core.workerTileBridge(worker);
    const answer = bridge.request({ z: 12, x: 1, y: 2 }, new AbortController().signal);
    worker.reply({ id: 1, error: 'TypeError: boom' });
    await expect(answer).rejects.toThrow('steep tile failed: TypeError: boom');
  });

  it('on abort tells the worker, rejects, and closes a late bitmap', async () => {
    const worker = fakeWorker();
    const bridge = core.workerTileBridge(worker);
    const controller = new AbortController();
    const answer = bridge.request({ z: 12, x: 1, y: 2 }, controller.signal);
    controller.abort();
    expect(worker.posted.at(-1)).toEqual({ type: 'abort', id: 1 });
    await expect(answer).rejects.toMatchObject({ name: 'AbortError' });
    const late = { close: vi.fn() };
    worker.reply({ id: 1, bitmap: late });
    expect(late.close).toHaveBeenCalledOnce();
    expect(bridge.pendingCount()).toBe(0);
  });
});

describe('createDemLoader', () => {
  const TILE = { z: 12, x: 2133, y: 1458 };
  const PRIMARY = 'https://mt.example/{z}/{x}/{y}.webp';
  const FALLBACK = 'https://aws.example/{z}/{x}/{y}.png';

  /** A Response stand-in. */
  function answer(status) {
    return {
      ok: status >= 200 && status < 300,
      status,
      arrayBuffer: async () => new ArrayBuffer(8),
      blob: async () => new Blob(['png']),
    };
  }

  /** A loader whose fetch answers from `route(url)`. */
  function loader(route) {
    const fetchTile = vi.fn(async (url) => route(url));
    const enlarge = vi.fn(async () => 'enlarged');
    return { fetchTile, enlarge, dem: core.createDemLoader({ primary: PRIMARY, fallback: FALLBACK, fetchTile, enlarge }) };
  }

  const signal = () => new AbortController().signal;

  it('answers from Mapterhorn when it is up, without touching AWS', async () => {
    const { fetchTile, enlarge, dem } = loader(() => answer(200));
    const result = await dem.load(TILE, signal());
    expect(result.data).toBeInstanceOf(ArrayBuffer);
    expect(fetchTile.mock.calls.map(([url]) => url)).toEqual(['https://mt.example/12/2133/1458.webp']);
    expect(enlarge).not.toHaveBeenCalled();
  });

  it('falls back to the same AWS tile, enlarged, on a 404 without counting it', async () => {
    const { fetchTile, enlarge, dem } = loader((url) => answer(url.startsWith('https://mt') ? 404 : 200));
    for (let i = 0; i < core.DEM_FALLBACK_AFTER + 1; i += 1) {
      expect((await dem.load(TILE, signal())).data).toBe('enlarged');
    }
    expect(fetchTile.mock.calls.at(-2)[0]).toBe('https://mt.example/12/2133/1458.webp');
    expect(fetchTile.mock.calls.at(-1)[0]).toBe('https://aws.example/12/2133/1458.png');
    expect(enlarge).toHaveBeenCalledTimes(core.DEM_FALLBACK_AFTER + 1);
    expect(dem.usingFallback()).toBe(false);
  });

  it('treats a 204 outside our copy\'s area as missing: AWS for that tile, not counted', async () => {
    const { fetchTile, enlarge, dem } = loader((url) => answer(url.startsWith('https://mt') ? 204 : 200));
    for (let i = 0; i < core.DEM_FALLBACK_AFTER + 1; i += 1) {
      expect((await dem.load(TILE, signal())).data).toBe('enlarged');
    }
    expect(fetchTile.mock.calls.at(-2)[0]).toBe('https://mt.example/12/2133/1458.webp');
    expect(fetchTile.mock.calls.at(-1)[0]).toBe('https://aws.example/12/2133/1458.png');
    expect(enlarge).toHaveBeenCalledTimes(core.DEM_FALLBACK_AFTER + 1);
    expect(dem.usingFallback()).toBe(false);
  });

  it('goes straight to AWS after Mapterhorn fails enough times in a row', async () => {
    const { fetchTile, dem } = loader((url) => {
      if (url.startsWith('https://mt')) throw new TypeError('Failed to fetch');
      return answer(200);
    });
    for (let i = 0; i < core.DEM_FALLBACK_AFTER; i += 1) await dem.load(TILE, signal());
    expect(dem.usingFallback()).toBe(true);
    fetchTile.mockClear();
    await dem.load(TILE, signal());
    expect(fetchTile.mock.calls.map(([url]) => url)).toEqual(['https://aws.example/12/2133/1458.png']);
  });

  it('keeps the latch when a request already in flight succeeds after it', async () => {
    let release;
    const late = new Promise((resolve) => { release = resolve; });
    let calls = 0;
    const { fetchTile, dem } = loader((url) => {
      if (!url.startsWith('https://mt')) return answer(200);
      calls += 1;
      if (calls === 1) return late.then(() => answer(200));
      throw new TypeError('Failed to fetch');
    });
    const slow = dem.load(TILE, signal());
    for (let i = 0; i < core.DEM_FALLBACK_AFTER; i += 1) await dem.load(TILE, signal());
    expect(dem.usingFallback()).toBe(true);
    release();
    await slow;
    expect(dem.usingFallback()).toBe(true);
    fetchTile.mockClear();
    await dem.load(TILE, signal());
    expect(fetchTile.mock.calls.map(([url]) => url)).toEqual(['https://aws.example/12/2133/1458.png']);
  });

  it('counts a 5xx as a failure, and a success resets the count', async () => {
    let status = 503;
    const { dem } = loader((url) => answer(url.startsWith('https://mt') ? status : 200));
    for (let i = 0; i < core.DEM_FALLBACK_AFTER - 1; i += 1) await dem.load(TILE, signal());
    status = 200;
    await dem.load(TILE, signal());
    status = 503;
    for (let i = 0; i < core.DEM_FALLBACK_AFTER - 1; i += 1) await dem.load(TILE, signal());
    expect(dem.usingFallback()).toBe(false);
  });

  it('rejects when both fail, and passes an abort straight through', async () => {
    const { dem } = loader(() => answer(500));
    await expect(dem.load(TILE, signal())).rejects.toThrow('no elevation tile 12/2133/1458 (500)');

    const controller = new AbortController();
    const aborting = loader(() => {
      controller.abort();
      throw new DOMException('Aborted', 'AbortError');
    });
    await expect(aborting.dem.load(TILE, controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
    expect(aborting.fetchTile).toHaveBeenCalledOnce();
  });
});

describe('frameStats / PROBE_PASSES', () => {
  it('summarises frame intervals by nearest rank', () => {
    const intervals = [16, 17, 16, 18, 50, 16, 17, 16, 17, 100];
    expect(core.frameStats(intervals)).toEqual({
      frames: 10,
      medianMs: 17,
      p95Ms: 100,
      worstMs: 100,
      overBudget: 0.2,
      fps: 59,
      withinBudget: true,
    });
  });

  it('fails the budget when the median frame reaches it', () => {
    const stats = core.frameStats([33, 33, 40, 20, 34]);
    expect(stats.medianMs).toBe(33);
    expect(stats.withinBudget).toBe(false);
    expect(core.FRAME_BUDGET_MS).toBe(33);
  });

  it('holds frames to the budget it is given', () => {
    expect(core.frameStats([20, 20, 20], 16).overBudget).toBe(1);
  });

  it('ignores junk and returns null when nothing is left', () => {
    expect(core.frameStats([0, -5, Number.NaN, Infinity])).toBeNull();
    expect(core.frameStats([])).toBeNull();
    expect(core.frameStats([Number.NaN, 25]).frames).toBe(1);
  });

  it('probes the opening view and the zoom where 3D is proposed to stop', () => {
    expect(core.PROBE_PASSES.map((p) => p.zoom)).toEqual([core.OPENING_CAMERA.zoom, 9]);
    for (const pass of core.PROBE_PASSES) {
      expect(pass.durationMs).toBeGreaterThanOrEqual(10000);
      expect(pass.turnDeg).toBeGreaterThan(0);
    }
  });
});

describe('frameWindowFails', () => {
  const stats = (medianMs, p95Ms) => ({ medianMs, p95Ms });

  it('pins the budget, loosened on 6 Nov 2026', () => {
    expect(core.FRAME_BUDGET_MS).toBe(33);
    expect(core.FRAME_WINDOW_MS).toBe(2000);
    expect(core.FRAME_WINDOWS_TO_TRIP).toBe(3);
    expect(core.FRAME_P95_BUDGET_MS).toBeUndefined();
  });

  it('fails a median of 33 ms and passes 32.9', () => {
    expect(core.frameWindowFails(stats(33, 40))).toBe(true);
    expect(core.frameWindowFails(stats(32.9, 40))).toBe(false);
  });

  it('does not judge the p95, which a tile stall lifts on a phone that draws well', () => {
    // Hugo's Mali-G715 probe over Saas-Fee: median 16.7 ms, p95 50.1 ms,
    // worst 83.3 ms. A window with stalls far worse still passes.
    expect(core.frameWindowFails(stats(16.7, 50.1))).toBe(false);
    expect(core.frameWindowFails(stats(16.7, 250))).toBe(false);
  });

  it('reads frameStats\' own output, and no frames is not a failure', () => {
    expect(core.frameWindowFails(core.frameStats([20, 20, 20, 20, 100]))).toBe(false);
    expect(core.frameWindowFails(core.frameStats([20, 40, 40, 40, 100]))).toBe(true);
    expect(core.frameWindowFails(core.frameStats([20, 20, 20, 20, 20]))).toBe(false);
    expect(core.frameWindowFails(null)).toBe(false);
  });
});

describe('createFrameWatch', () => {
  const SLOW = 50;
  const FAST = 20;
  /** Push `n` intervals of `ms`, answering what the last push said. */
  const pushAll = (watch, ms, n) => {
    let tripped = false;
    for (let i = 0; i < n; i += 1) tripped = watch.push(ms);
    return tripped;
  };
  const perWindow = (ms) => core.FRAME_WINDOW_MS / ms;

  it('closes a window at about 2 s and trips on the third bad one in a row', () => {
    const watch = core.createFrameWatch();
    expect(pushAll(watch, SLOW, perWindow(SLOW) * 2)).toBe(false);
    expect(pushAll(watch, SLOW, perWindow(SLOW) - 1)).toBe(false);
    expect(watch.push(SLOW)).toBe(true);
    expect(watch.push(FAST)).toBe(true);
  });

  it('does not trip on bad, bad, good, bad, bad', () => {
    const watch = core.createFrameWatch();
    pushAll(watch, SLOW, perWindow(SLOW) * 2);
    pushAll(watch, FAST, perWindow(FAST));
    expect(pushAll(watch, SLOW, perWindow(SLOW) * 2)).toBe(false);
  });

  it('is cleared by reset, part-filled window and count included', () => {
    const watch = core.createFrameWatch();
    pushAll(watch, SLOW, perWindow(SLOW) * 3);
    watch.reset();
    expect(watch.push(FAST)).toBe(false);
    pushAll(watch, SLOW, perWindow(SLOW) - 1);
    // The fast frame and these close one bad window; one is not enough.
    expect(pushAll(watch, SLOW, 1)).toBe(false);
  });

  it('ignores junk intervals', () => {
    const watch = core.createFrameWatch();
    for (const ms of [0, -5, Number.NaN, Infinity]) expect(watch.push(ms)).toBe(false);
    expect(pushAll(watch, SLOW, perWindow(SLOW) * 3)).toBe(true);
  });
});
