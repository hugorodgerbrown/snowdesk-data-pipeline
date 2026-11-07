/*
 * tests/js/test_map_3d_core.js — the 3D terrain map's pure parts (SNOW-1438).
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the DEM source is Terrarium-encoded and the terrain spec names it;
 *   - the exaggeration stays modest, so 30° does not read as 40°;
 *   - a route share link into 3D is the share link plus `3d/`;
 *   - the frame-time probe's summary (SNOW-1448) uses intervals that
 *     happened, ignores junk, and holds the median to the 33 ms budget;
 *   - /map/'s frame watchdog (SNOW-1455): a window fails at a median of 33 ms
 *     or a p95 over 66 ms, closes at about 2 s, and two bad ones in a row trip
 *     the watch;
 *   - the 3D test's verdict (SNOW-1456): smooth, stutter and slow by the
 *     watchdog's own rule over each pass, and unmeasured for a pass with
 *     no frames or less than one window of them.
 *
 * SNOW-1450 removed the retired /map/3d/ page's own parts (the tours box,
 * line bounds, the shared view, the wildlife layers, the page's link and
 * the Steep terrain worker bridge) and their tests.
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

describe('routeShare3dUrl', () => {
  it('hangs 3d/ and the query off the route share link', () => {
    expect(core.routeShare3dUrl('https://s.example/routes/s/tok/', 'at=7,46,12,60,0')).toBe(
      'https://s.example/routes/s/tok/3d/?at=7,46,12,60,0',
    );
  });

  it('adds no query when none is given, as the route Shares call it', () => {
    expect(core.routeShare3dUrl('https://s.example/routes/s/tok/', '')).toBe(
      'https://s.example/routes/s/tok/3d/',
    );
  });

  it('copes with a link without its trailing slash', () => {
    expect(core.routeShare3dUrl('https://s.example/routes/s/tok', 'at=1')).toBe(
      'https://s.example/routes/s/tok/3d/?at=1',
    );
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

  // SNOW-1453: offline, the service worker answers a tile it does not hold
  // with its own 504 (`_synthesizedGatewayTimeout`). Counting those would
  // latch the loader off the pinned tiles after three misses off the area.
  it('does not latch on the service worker\'s offline miss, so pinned tiles are still asked for', async () => {
    const miss = () => ({ ...answer(504), headers: new Headers({ 'X-SW-Cache': 'miss' }) });
    const { fetchTile, dem } = loader(miss);
    for (let i = 0; i < core.DEM_FALLBACK_AFTER + 1; i += 1) {
      await expect(dem.load(TILE, signal())).rejects.toThrow('(504)');
    }
    expect(dem.usingFallback()).toBe(false);
    fetchTile.mockClear();
    await expect(dem.load(TILE, signal())).rejects.toThrow('(504)');
    expect(fetchTile.mock.calls[0][0]).toBe('https://mt.example/12/2133/1458.webp');
  });

  it('still counts a 504 the service worker did not make', async () => {
    const { dem } = loader((url) => (url.startsWith('https://mt')
      ? { ...answer(504), headers: new Headers() }
      : answer(200)));
    for (let i = 0; i < core.DEM_FALLBACK_AFTER; i += 1) await dem.load(TILE, signal());
    expect(dem.usingFallback()).toBe(true);
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
    const stats = core.frameStats([45, 45, 50, 20, 46]);
    expect(stats.medianMs).toBe(45);
    expect(stats.withinBudget).toBe(false);
    expect(core.FRAME_BUDGET_MS).toBe(45);
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
      expect(pass.durationMs).toBe(18000);
      expect(pass.turnDeg).toBeGreaterThan(0);
    }
  });
});

describe('frameWindowFails', () => {
  const stats = (medianMs, p95Ms) => ({ medianMs, p95Ms });

  it('pins the budget, loosened again on 7 Nov 2026 (SNOW-1463)', () => {
    expect(core.FRAME_BUDGET_MS).toBe(45);
    expect(core.FRAME_WINDOW_MS).toBe(2000);
    expect(core.FRAME_WINDOWS_TO_TRIP).toBe(5);
    expect(core.FRAME_P95_BUDGET_MS).toBeUndefined();
  });

  it('fails a median of 45 ms and passes 44.9', () => {
    expect(core.frameWindowFails(stats(45, 60))).toBe(true);
    expect(core.frameWindowFails(stats(44.9, 60))).toBe(false);
  });

  it('passes 30 fps and 24 fps, and fails 20 fps, each a refresh step clear of the bar', () => {
    expect(core.frameWindowFails(stats(33.3, 40))).toBe(false);
    expect(core.frameWindowFails(stats(41.7, 50))).toBe(false);
    expect(core.frameWindowFails(stats(50, 60))).toBe(true);
  });

  it('does not judge the p95, which a tile stall lifts on a phone that draws well', () => {
    // Hugo's Mali-G715 probe over Saas-Fee: median 16.7 ms, p95 50.1 ms,
    // worst 83.3 ms. A window with stalls far worse still passes.
    expect(core.frameWindowFails(stats(16.7, 50.1))).toBe(false);
    expect(core.frameWindowFails(stats(16.7, 250))).toBe(false);
  });

  it('reads frameStats\' own output, and no frames is not a failure', () => {
    expect(core.frameWindowFails(core.frameStats([20, 20, 20, 20, 100]))).toBe(false);
    expect(core.frameWindowFails(core.frameStats([20, 50, 50, 50, 100]))).toBe(true);
    expect(core.frameWindowFails(core.frameStats([20, 20, 20, 20, 20]))).toBe(false);
    expect(core.frameWindowFails(null)).toBe(false);
  });
});

describe('createFrameWatch', () => {
  const SLOW = 55;
  const FAST = 20;
  /** Push `n` intervals of `ms`, answering what the last push said. */
  const pushAll = (watch, ms, n, loading = false) => {
    let tripped = false;
    for (let i = 0; i < n; i += 1) tripped = watch.push(ms, loading);
    return tripped;
  };
  /** Intervals of `ms` that fill one window (the last one closes it). */
  const perWindow = (ms) => Math.ceil(core.FRAME_WINDOW_MS / ms);

  it('never trips on a steady 30 fps (33.3 ms), the iPad that dropped out', () => {
    const watch = core.createFrameWatch();
    expect(pushAll(watch, 1000 / 30, perWindow(1000 / 30) * 20)).toBe(false);
  });

  it('never trips on a steady 24 fps (41.7 ms, a 120 Hz display\'s step)', () => {
    const watch = core.createFrameWatch();
    expect(pushAll(watch, 1000 / 24, perWindow(1000 / 24) * 20)).toBe(false);
  });

  it('closes a window at about 2 s and trips on the fifth bad one in a row, not the fourth', () => {
    const watch = core.createFrameWatch();
    expect(pushAll(watch, SLOW, perWindow(SLOW) * 4)).toBe(false);
    expect(pushAll(watch, SLOW, perWindow(SLOW) - 1)).toBe(false);
    expect(watch.push(SLOW)).toBe(true);
    expect(watch.push(FAST)).toBe(true);
  });

  it('starts the count again after a good window', () => {
    const watch = core.createFrameWatch();
    pushAll(watch, SLOW, perWindow(SLOW) * 4);
    pushAll(watch, FAST, perWindow(FAST));
    expect(pushAll(watch, SLOW, perWindow(SLOW) * 4)).toBe(false);
    expect(pushAll(watch, SLOW, perWindow(SLOW))).toBe(true);
  });

  it('skips intervals while tiles load: they neither close a window nor count in it', () => {
    const watch = core.createFrameWatch();
    // A minute of slow frames, all while tiles were loading.
    expect(pushAll(watch, 200, 300, true)).toBe(false);
    // One timed slow window less one frame: still open, so no window closed.
    const windows = [];
    const traced = core.createFrameWatch({ onWindow: (w) => windows.push(w) });
    pushAll(traced, 200, 50, true);
    pushAll(traced, SLOW, perWindow(SLOW) - 1);
    expect(windows).toEqual([]);
    traced.push(SLOW);
    expect(windows).toHaveLength(1);
    expect(windows[0]).toMatchObject({ medianMs: SLOW, frames: perWindow(SLOW), skippedMs: 10000, slow: true });
  });

  it('keeps a window that was partly loading, judged on its timed frames only', () => {
    const watch = core.createFrameWatch();
    for (let w = 0; w < 5; w += 1) {
      pushAll(watch, 150, 10, true);
      pushAll(watch, FAST, perWindow(FAST));
    }
    expect(watch.push(FAST)).toBe(false);
  });

  it('tells onWindow about every closed window, with the run of failures', () => {
    const windows = [];
    const watch = core.createFrameWatch({ onWindow: (w) => windows.push(w) });
    pushAll(watch, FAST, perWindow(FAST));
    pushAll(watch, SLOW, perWindow(SLOW) * 5);
    expect(windows.map((w) => w.failing)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(windows.map((w) => w.tripped)).toEqual([false, false, false, false, false, true]);
    expect(windows[0]).toEqual({ medianMs: FAST, frames: perWindow(FAST), skippedMs: 0, slow: false, failing: 0, tripped: false });
  });

  it('is cleared by reset, part-filled window and count included', () => {
    const watch = core.createFrameWatch();
    pushAll(watch, SLOW, perWindow(SLOW) * 5);
    watch.reset();
    expect(watch.push(FAST)).toBe(false);
    pushAll(watch, SLOW, perWindow(SLOW) - 1);
    // The fast frame and these close one bad window; one is not enough.
    expect(pushAll(watch, SLOW, 1)).toBe(false);
  });

  it('ignores junk intervals', () => {
    const watch = core.createFrameWatch();
    for (const ms of [0, -5, Number.NaN, Infinity]) expect(watch.push(ms)).toBe(false);
    expect(pushAll(watch, SLOW, perWindow(SLOW) * 5)).toBe(true);
  });
});

describe('judgePass / testVerdict (SNOW-1456)', () => {
  const SLOW = 55;
  const FAST = 20;
  /** `windows` full watchdog windows of `ms` intervals. */
  const run = (ms, windows) => Array(Math.ceil(core.FRAME_WINDOW_MS / ms) * windows).fill(ms);
  const KEEPS_UP = run(FAST, 8);
  const FALLS_BEHIND = run(SLOW, 8);

  it('says smooth when both passes keep up', () => {
    expect(core.testVerdict([{ intervals: KEEPS_UP }, { intervals: KEEPS_UP }])).toBe('smooth');
  });

  it('says stutter when only the z9 pass falls behind', () => {
    expect(core.testVerdict([{ intervals: KEEPS_UP }, { intervals: FALLS_BEHIND }])).toBe('stutter');
  });

  it('says slow when the close-up pass falls behind, whatever the other does', () => {
    expect(core.testVerdict([{ intervals: FALLS_BEHIND }, { intervals: FALLS_BEHIND }])).toBe('slow');
    expect(core.testVerdict([{ intervals: FALLS_BEHIND }, { intervals: KEEPS_UP }])).toBe('slow');
  });

  it('judges by the watch: four slow windows in a row are not enough', () => {
    const fourBad = [...run(SLOW, 4), ...run(FAST, 1), ...run(SLOW, 4)];
    expect(core.judgePass(fourBad)).toEqual({ measured: true, keptUp: true, windows: 9, slowWindows: 8, skippedMs: 0 });
    expect(core.testVerdict([{ intervals: fourBad }, { intervals: fourBad }])).toBe('smooth');
  });

  it('counts every window of a pass that trips, not just those up to the trip', () => {
    expect(core.judgePass(FALLS_BEHIND)).toEqual({ measured: true, keptUp: false, windows: 8, slowWindows: 8, skippedMs: 0 });
  });

  it('never reads the p95: a stall in every window still keeps up', () => {
    const spiky = Array.from({ length: 8 * 100 }, (_v, i) => (i % 10 === 0 ? 120 : 16));
    expect(core.frameStats(spiky).p95Ms).toBeGreaterThan(66);
    expect(core.judgePass(spiky).keptUp).toBe(true);
  });

  it('skips the frames that saw tiles loading, as the watchdog does', () => {
    // Slow only while loading: the Pixel 9 close in.
    const intervals = [...run(150, 6), ...run(FAST, 6)];
    const loading = intervals.map((_ms, i) => i < run(150, 6).length);
    const judged = core.judgePass(intervals, loading);
    expect(judged).toMatchObject({ measured: true, keptUp: true, windows: 6, slowWindows: 0 });
    expect(judged.skippedMs).toBe(run(150, 6).length * 150);
    expect(core.judgePass(intervals).keptUp).toBe(false);
    expect(core.testVerdict([{ intervals, loading }, { intervals, loading }])).toBe('smooth');
  });

  it('says unmeasured for a pass with no frames', () => {
    expect(core.judgePass([])).toEqual({ measured: false, keptUp: false, windows: 0, slowWindows: 0, skippedMs: 0 });
    expect(core.testVerdict([{ intervals: KEEPS_UP }, { intervals: [] }])).toBe('unmeasured');
    expect(core.testVerdict([{ intervals: [] }, { intervals: FALLS_BEHIND }])).toBe('unmeasured');
  });

  it('says unmeasured for a pass of fewer than five windows, which could not trip', () => {
    const short = run(FAST, core.FRAME_WINDOWS_TO_TRIP - 1);
    expect(core.judgePass(short)).toMatchObject({ measured: false, windows: 4 });
    expect(core.judgePass(run(SLOW, 4)).measured).toBe(false);
    expect(core.judgePass(run(FAST, 5)).measured).toBe(true);
    expect(core.testVerdict([{ intervals: KEEPS_UP }, { intervals: short }])).toBe('unmeasured');
    expect(core.testVerdict([])).toBe('unmeasured');
  });

  it('lists the verdicts a result may be sent with', () => {
    expect(core.TEST_VERDICTS).toEqual(['smooth', 'stutter', 'slow']);
  });
});
