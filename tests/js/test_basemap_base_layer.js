/*
 * tests/js/test_basemap_base_layer.js — Vitest unit tests for SNOW-856's
 * shared z0-9 base layer in static/js/basemap_download_core.js.
 *
 * The bug: a download pins ``MICRO_BAND`` (z10-14) and the map's camera
 * goes down to z4, so an offline reader who zoomed out fell off the edge
 * of every area they owned and nothing said so. The base layer is the
 * z0-9 tiles that close that gap — one per BASEMAP, shared by every area
 * under it, in its own pinned bucket.
 *
 * What is actually worth asserting here, and why:
 *
 *   - **The camera bounds the layer, the style only narrows it.** Getting
 *     this backwards is not a rounding error: deriving the extent from the
 *     style's own declared bounds — the design this ticket started with —
 *     works for the three national basemaps and asks for roughly 350,000
 *     tiles on the default one, which is global. The direction of that
 *     intersection is the whole safety property, so it is tested from both
 *     sides (a global style, and a style narrower than the camera).
 *   - **The host rotation still matches MapLibre's.** A base layer stored
 *     under a host the map never asks for is a bucket full of tiles that
 *     never serve — SNOW-843's failure, one layer down. Asserted against
 *     ``urls[(x + y) % urls.length]`` directly.
 *   - **A base layer is never an eviction candidate.** ``planEviction``
 *     counting it but never proposing it, and — the case that is easy to
 *     get wrong — refusing a run as ``impossible`` when the un-evictable
 *     floor plus the incoming run exceeds the budget, rather than
 *     returning a plan that evicts everything and still does not fit.
 *
 * `basemap_download_core.js` is a plain IIFE assigning a frozen
 * `self.pwaBasemapDownloadCore` — importing it for side effects is enough.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/basemap_download_core.js';

const core = self.pwaBasemapDownloadCore;

// static/js/map.js's MAX_BOUNDS, as `mapCameraBBox` reads it back off the
// live map — the Alpine arc the camera is clamped to.
const CAMERA = [0.9482, 41.9952, 19.6674, 49.9983];

// The swisstopo sources' declared TileJSON bounds, narrower than the
// camera on every side but the south-west.
const SWISSTOPO = [3.57, 44.18, 13.66, 48.88];

describe('areaIdForBaseLayer / isBaseLayerAreaId', () => {
  it('mints a third id namespace beside region- and custom-', () => {
    const id = core.areaIdForBaseLayer('swisstopo_winter');

    expect(id).toBe('base-swisstopo_winter');
    expect(core.isBaseLayerAreaId(id)).toBe(true);
    // The three namespaces must not overlap: every surface that lists,
    // sizes or evicts areas tells them apart by these predicates alone.
    expect(core.isCustomAreaId(id)).toBe(false);
    expect(core.isBaseLayerAreaId(core.areaIdForRegion('CH-4115'))).toBe(false);
    expect(core.isBaseLayerAreaId(core.generateCustomAreaId())).toBe(false);
    expect(core.isBaseLayerAreaId(core.CUSTOM_AREA_ID)).toBe(false);
  });

  it('is keyed per basemap, so two basemaps get two buckets', () => {
    const swiss = core.areaIdForBaseLayer('swisstopo_winter');
    const free = core.areaIdForBaseLayer('openfreemap_liberty');

    expect(swiss).not.toBe(free);
    expect(core.pinnedCacheName(swiss)).not.toBe(core.pinnedCacheName(free));
  });

  it('rejects a non-string rather than claiming one', () => {
    expect(core.isBaseLayerAreaId(undefined)).toBe(false);
    expect(core.isBaseLayerAreaId(null)).toBe(false);
    expect(core.isBaseLayerAreaId(42)).toBe(false);
  });
});

describe('baseLayerBBox', () => {
  it('narrows the camera to a national style, never the other way', () => {
    expect(core.baseLayerBBox(CAMERA, SWISSTOPO)).toEqual(SWISSTOPO);
  });

  it('keeps the camera whole for a style that declares no bounds', () => {
    // Falling back to the STYLE here — the design this ticket started with
    // — asks for z0-9 worldwide, roughly 350,000 tiles. The camera is the
    // only bound that exists, and it is sufficient because the reader
    // cannot pan outside it.
    expect(core.baseLayerBBox(CAMERA, null)).toEqual(CAMERA);
    expect(core.baseLayerBBox(CAMERA, undefined)).toEqual(CAMERA);
  });

  it('clamps a style that declares the WHOLE WORLD — the real global case', () => {
    // Not a hypothetical, and not the same code path as "no bounds":
    // OpenFreeMap Liberty — the DEFAULT basemap — declares
    // `[-180, -85.05113, 180, 85.05113]` on its `openmaptiles` source.
    // Read off the live style in the browser on 2026-09-07, because a
    // global style declaring its globality explicitly is the case that
    // would slip past a null check and enumerate the planet.
    const WORLD = [-180, -85.05113, 180, 85.05113];

    expect(core.baseLayerBBox(CAMERA, WORLD)).toEqual(CAMERA);
    expect(core.baseLayerBlob(CAMERA, WORLD).count).toBe(682);
  });

  it('ignores a malformed bounds array rather than trusting it', () => {
    expect(core.baseLayerBBox(CAMERA, [1, 2, 3])).toEqual(CAMERA);
    expect(core.baseLayerBBox(CAMERA, [1, 2, 3, Number.NaN])).toEqual(CAMERA);
    expect(core.baseLayerBBox(CAMERA, 'everywhere')).toEqual(CAMERA);
  });

  it('takes the tighter edge on each axis independently', () => {
    // Overlaps the camera on the east/north and overhangs it west/south:
    // the result must be the intersection edge by edge, not whichever box
    // happens to be smaller overall.
    const straddling = [-5, 40, 10, 47];

    expect(core.baseLayerBBox(CAMERA, straddling)).toEqual([0.9482, 41.9952, 10, 47]);
  });

  it('answers null for a style that does not meet the map at all', () => {
    // A basemap covering ground this map cannot show has no base layer to
    // download, and null is what stops a run being planned for it.
    expect(core.baseLayerBBox(CAMERA, [-120, 30, -100, 40])).toBeNull();
  });

  it('answers null for a bbox that only touches at an edge', () => {
    // Zero-area overlap is no overlap — a strictly-greater test, so a
    // shared border never enumerates a degenerate strip of tiles.
    expect(core.baseLayerBBox(CAMERA, [19.6674, 41.9952, 25, 49.9983])).toBeNull();
  });

  it('answers null for a malformed camera, which is not a fallback case', () => {
    // Unlike the style's bounds, the camera has no safe default: guessing
    // one would be guessing how much of the world to download.
    expect(core.baseLayerBBox(null, SWISSTOPO)).toBeNull();
    expect(core.baseLayerBBox([1, 2, 3], SWISSTOPO)).toBeNull();
  });
});

describe('baseLayerBlob', () => {
  it('covers z0-9 — everything below a download band, and no overlap', () => {
    const blob = core.baseLayerBlob(CAMERA, SWISSTOPO);

    expect(blob.band).toEqual([0, 9]);
    expect(Object.keys(blob.z).map(Number).sort((a, b) => a - b)).toEqual([
      0, 1, 2, 3, 4, 5, 6, 7, 8, 9,
    ]);
    // The two bands abut: the base layer stops exactly where MICRO_BAND
    // starts, so there is neither a gap nor a tile paid for twice.
    expect(core.BASE_LAYER_BAND[1] + 1).toBe(core.MICRO_BAND[0]);
  });

  it('is 215 tiles per source over swisstopo — the measured figure', () => {
    // Fetched for real on 2026-09-07 while scoping SNOW-856: 215 tiles per
    // source, 3.0 MB for ch.swisstopo.base.vt and 5.5 MB for
    // ch.swisstopo.relief.vt, 8.5 MB the pair. The count is what this can
    // assert; it is here so a change to the band or the bbox that quietly
    // multiplies the download fails loudly instead.
    expect(core.baseLayerBlob(CAMERA, SWISSTOPO).count).toBe(215);
  });

  it('stays bounded for a global style — the camera holds it to 682', () => {
    expect(core.baseLayerBlob(CAMERA, null).count).toBe(682);
  });

  it('answers null where the extent does', () => {
    expect(core.baseLayerBlob(CAMERA, [-120, 30, -100, 40])).toBeNull();
  });
});

describe('baseLayerTileURLs', () => {
  const A = 'https://a.example.com/{z}/{x}/{y}.pbf';
  const B = 'https://b.example.com/{z}/{x}/{y}.pbf';

  it('emits one url per tile per source', () => {
    const sources = [[A], [B]];
    const urls = core.baseLayerTileURLs(sources, CAMERA, SWISSTOPO);

    expect(urls).toHaveLength(215 * 2);
    expect(urls.filter((url) => url.startsWith('https://a.'))).toHaveLength(215);
    expect(urls.filter((url) => url.startsWith('https://b.'))).toHaveLength(215);
  });

  it("picks each tile's host the way MapLibre does", () => {
    // MapLibre's CanonicalTileID.url is `urls[(x + y) % urls.length]`. A
    // base layer stored under any other host is a bucket the map never
    // reads — SNOW-843's bug, repeated below the download band.
    const hosts = [
      'https://h0.example.com/{z}/{x}/{y}.pbf',
      'https://h1.example.com/{z}/{x}/{y}.pbf',
      'https://h2.example.com/{z}/{x}/{y}.pbf',
    ];
    // A one-tile extent so the expected url can be named outright: z0 is
    // always tile 0/0, and (0 + 0) % 3 is host 0.
    const urls = core.baseLayerTileURLs([hosts], CAMERA, SWISSTOPO);

    expect(urls).toContain('https://h0.example.com/0/0/0.pbf');
    // Every url must come from one of the three declared hosts and from
    // the one the rule names for its own indices.
    for (const url of urls) {
      const [, z, x, y] = url.match(/h\d\.example\.com\/(\d+)\/(\d+)\/(\d+)/).map(Number);
      const expected = `https://h${(x + y) % 3}.example.com/${z}/${x}/${y}.pbf`;
      expect(url).toBe(expected);
    }
  });

  it('answers an empty list, never null, where there is no extent', () => {
    // The runner treats an empty list as "nothing to do" and a null PLAN
    // as "no base layer for this style" — so this must not conflate them.
    expect(core.baseLayerTileURLs([[A]], CAMERA, [-120, 30, -100, 40])).toEqual([]);
  });

  it('answers an empty list for a style whose sources have not settled', () => {
    expect(core.baseLayerTileURLs(null, CAMERA, SWISSTOPO)).toEqual([]);
  });
});

describe('planEviction with a base layer present', () => {
  const MB = 1024 * 1024;
  const base = { id: 'base-swisstopo_winter', bytes: 10 * MB, savedAt: '2026-01-01T00:00:00Z' };
  const oldArea = { id: 'region-CH-1', bytes: 30 * MB, savedAt: '2026-02-01T00:00:00Z' };
  const newArea = { id: 'region-CH-2', bytes: 30 * MB, savedAt: '2026-03-01T00:00:00Z' };

  it('counts a base layer toward the standing total', () => {
    // It is real disk. A budget that ignores it would let the device run
    // over by however much every base layer on it weighs.
    const plan = core.planEviction([base], { id: 'region-CH-3', bytes: 30 * MB }, 100 * MB);

    expect(plan.fits).toBe(true);
    expect(plan.projectedBytes).toBe(40 * MB);
  });

  it('never proposes evicting one, even as the oldest entry', () => {
    // `base` has the earliest savedAt, so an oldest-first sort that did
    // not exclude it would pick it FIRST — which is exactly the bug this
    // guards: it is shared, so evicting it to fit one area breaks the
    // zoomed-out view of every other area on the device.
    const plan = core.planEviction(
      [base, oldArea, newArea],
      { id: 'region-CH-3', bytes: 30 * MB },
      80 * MB,
    );

    expect(plan.evict).not.toContain(base.id);
    expect(plan.evict).toEqual([oldArea.id]);
    expect(plan.projectedBytes).toBe(70 * MB);
  });

  it('refuses a run the un-evictable floor makes impossible', () => {
    // Evicting every area still leaves the base layer, so a run that only
    // fits with the base layer gone does not fit at all. Before SNOW-856
    // the floor was zero and the first check compared `incoming` alone;
    // returning a plan here instead would evict the user's whole library
    // and then fail anyway.
    const plan = core.planEviction(
      [base, oldArea, newArea],
      { id: 'region-CH-3', bytes: 35 * MB },
      40 * MB,
    );

    expect(plan.impossible).toBe(true);
    expect(plan.evict).toEqual([]);
    // Nothing changes, so the reported total is the standing one.
    expect(plan.projectedBytes).toBe(70 * MB);
  });

  it('still evicts down to the floor when that is enough', () => {
    const plan = core.planEviction(
      [base, oldArea, newArea],
      { id: 'region-CH-3', bytes: 30 * MB },
      45 * MB,
    );

    expect(plan.impossible).toBe(false);
    expect(plan.evict).toEqual([oldArea.id, newArea.id]);
    expect(plan.projectedBytes).toBe(40 * MB);
  });

  it('is unchanged for a device with no base layer', () => {
    // The pre-SNOW-856 behaviour, asserted so the floor arithmetic cannot
    // quietly alter the ordinary case.
    const plan = core.planEviction(
      [oldArea, newArea],
      { id: 'region-CH-3', bytes: 30 * MB },
      70 * MB,
    );

    expect(plan.evict).toEqual([oldArea.id]);
    expect(plan.projectedBytes).toBe(60 * MB);
  });
});
