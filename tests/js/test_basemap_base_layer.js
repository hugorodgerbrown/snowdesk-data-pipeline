/*
 * tests/js/test_basemap_base_layer.js — Vitest unit tests for SNOW-856's
 * shared base layer in static/js/basemap_download_core.js.
 *
 * The bug: a download pins ``MICRO_BAND`` (z10-14) and the map's camera
 * goes down to z4, so an offline reader who zoomed out fell off the edge
 * of every area they owned and nothing said so. The base layer is the
 * shallow-zoom tiles that close that gap — one per BASEMAP, shared by
 * every area under it, in its own pinned bucket.
 *
 * SNOW-863 trimmed it from z0-9 to z0-7 after measuring what it actually
 * cost (144.4 MB on the default basemap, 29% of the standing budget), and
 * SNOW-868 made the band a function of the BASEMAP rather than a flat
 * rule, having measured the other three. The trim was right for
 * OpenFreeMap and was never a statement about a national style: z8+z9
 * costs OpenFreeMap 121 MB over its Alps-wide camera extent, and costs
 * swisstopo 4.4 MB, IGN 4.4 MB and basemap.at ~7.3 MB over theirs. So the
 * national basemaps close the seam to z9 and the default keeps the gap.
 * The SIZE assertions below are load-bearing for that reason and not
 * decoration — the old cost was invisible until a user complained about
 * their budget, and the numbers are what keeps this from being argued
 * from taste in either direction.
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
 *   - **A tile is told from a document by its url alone.** SNOW-929 put
 *     the style, TileJSON, sprite and glyph ranges that DRAW the band into
 *     the bucket beside it, so SNOW-863's re-banding check — which evicts
 *     a bucket whole over one unexpected entry — now judges tile entries
 *     only. ``isTileEntryURL`` gets a truth table of real provider urls
 *     because both ways of being wrong are silent: read a glyph range as a
 *     tile and every warm evicts the band it just fetched; read a raster
 *     tile as a document and the old-band migration quietly stops firing.
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
    expect(core.baseLayerBlob(CAMERA, WORLD).count).toBe(56);
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
  it('defaults to z0-7 when no basemap is named', () => {
    const blob = core.baseLayerBlob(CAMERA, SWISSTOPO);

    expect(blob.band).toEqual([0, 7]);
    expect(Object.keys(blob.z).map(Number).sort((a, b) => a - b)).toEqual([
      0, 1, 2, 3, 4, 5, 6, 7,
    ]);
    // The default is the CHEAPEST band, which is the conservative
    // direction for a basemap whose extent nothing has measured.
    expect(core.baseLayerBand()).toEqual(core.BASE_LAYER_BAND);
    expect(core.baseLayerBand('a_basemap_nobody_has_measured')).toEqual([0, 7]);
  });

  it('closes the seam below MICRO_BAND for a national basemap, not for the default', () => {
    // This assertion is per-basemap, and it has now been inverted twice —
    // so read the numbers, not the history. SNOW-856 chose z0-9 so the two
    // bands would abut. SNOW-863 measured the DEFAULT basemap and trimmed
    // to z0-7 everywhere. SNOW-868 measured the other three, and what
    // z8+z9 costs over each basemap's OWN extent is:
    //
    //   openfreemap_liberty  121 MB   (33.4 + 88.0, Alps-wide camera)
    //   swisstopo            4.4 MB   (1.4 + 3.0, CH, both sources)
    //   ign_plan             4.4 MB   (2.1 + 2.3, FR)
    //   basemap_at           ~7.3 MB  (~3.2 + ~4.1, AT)
    //
    // A quarter of the standing 500 MB budget against a rounding error.
    // So the gap stays for OpenFreeMap, where z8 and z9 draw from the
    // stored z7 tile through MapLibre's findLoadedParent — the same
    // mechanism the whole feature already relies on — and closes for the
    // national three, where paying to close it is not paying much.
    //
    // This is NOT SNOW-856's mistake being repeated: that band was global
    // and was never measured per basemap. This one is neither.
    expect(core.baseLayerBand('openfreemap_liberty')[1] + 1).toBeLessThan(
      core.MICRO_BAND[0],
    );
    for (const key of ['swisstopo_winter', 'swisstopo_light', 'ign_plan', 'basemap_at']) {
      expect(core.baseLayerBand(key)).toEqual([0, 9]);
      expect(core.baseLayerBand(key)[1] + 1).toBe(core.MICRO_BAND[0]);
    }
  });

  it('is 25 tiles per source over swisstopo at the default band, 215 at its own', () => {
    // Measured 2026-09-07 (SNOW-863): the z0-9 band is 215 tiles per source
    // and 14.8 MB on disk for swisstopo's two; z0-7 is 25. Both counts are
    // asserted so a band or bbox change that quietly multiplies the
    // download fails loudly rather than showing up as a budget complaint
    // months later — which is exactly how the z0-9 cost was found. 215 is
    // also the figure BASE_LAYER_BAND's own comment records, so the two
    // cannot drift apart silently.
    expect(core.baseLayerBlob(CAMERA, SWISSTOPO).count).toBe(25);
    const national = core.baseLayerBlob(CAMERA, SWISSTOPO, 'swisstopo_winter');
    expect(national.band).toEqual([0, 9]);
    expect(national.count).toBe(215);
  });

  it('is 56 tiles for a global style — the camera holds it, not the style', () => {
    // The case that made the size matter: OpenFreeMap Liberty declares the
    // whole world, so only the camera bounds it. At z0-9 that was 682
    // tiles and 144.4 MB on disk, 29% of the standing budget before a
    // single area was downloaded. Named or not, it stays at z0-7 — the
    // measurement is why the default is the default.
    expect(core.baseLayerBlob(CAMERA, null).count).toBe(56);
    expect(core.baseLayerBlob(CAMERA, null, 'openfreemap_liberty').count).toBe(56);
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

    expect(urls).toHaveLength(25 * 2);
    expect(urls.filter((url) => url.startsWith('https://a.'))).toHaveLength(25);
    expect(urls.filter((url) => url.startsWith('https://b.'))).toHaveLength(25);
  });

  it("asks for the named basemap's band, not the default one", () => {
    // What `resolveBaseLayerPlan` threads through, and the reason the
    // stale-bucket migration needs nothing per-basemap of its own: the url
    // set it compares against is already this basemap's.
    const urls = core.baseLayerTileURLs([[A]], CAMERA, SWISSTOPO, 'swisstopo_winter');

    expect(urls).toHaveLength(215);
    // The old band's urls are a strict SUBSET of the new one's, which is
    // why widening a national basemap's band evicts nothing: the ordinary
    // missing-url plan tops the bucket up with z8 and z9 and re-fetches
    // none of what is already held.
    const old = core.baseLayerTileURLs([[A]], CAMERA, SWISSTOPO);
    expect(old.every((url) => urls.includes(url))).toBe(true);
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

describe('isTileEntryURL', () => {
  // Real urls, read off the live styles in the browser rather than
  // invented: the near misses are the whole point of this predicate, and
  // an invented sprite or glyph path would be a near miss of a shape no
  // provider actually emits.
  it('recognises a vector tile whatever the tileset path carries', () => {
    // OpenFreeMap's tileset path carries a dated build id that changes
    // under us, so the match is the TAIL and nothing else.
    expect(
      core.isTileEntryURL(
        'https://tiles.openfreemap.org/planet/20260906_080001_pt/7/66/45.pbf',
      ),
    ).toBe(true);
    expect(core.isTileEntryURL('https://example.invalid/12/2145/1456.mvt')).toBe(true);
  });

  it('recognises a raster tile', () => {
    // OpenFreeMap's natural-earth source serves PNGs, so a tiles-only
    // judgement that read .png as "a document" would stop re-banding the
    // default basemap's bucket.
    expect(
      core.isTileEntryURL('https://tiles.openfreemap.org/natural_earth/ne2sr/6/33/22.png'),
    ).toBe(true);
    expect(core.isTileEntryURL('https://example.invalid/9/266/180.jpg')).toBe(true);
    expect(core.isTileEntryURL('https://example.invalid/9/266/180.jpeg')).toBe(true);
  });

  it('ignores a query string and a fragment', () => {
    // An api-keyed provider (and MapLibre's own cache-busting) appends to
    // the url the cache then stores, and the answer must not turn on it.
    expect(core.isTileEntryURL('https://example.invalid/7/66/45.pbf?key=abc')).toBe(true);
    expect(core.isTileEntryURL('https://example.invalid/7/66/45.pbf#x')).toBe(true);
  });

  it('rejects a glyph range, which is the closest miss there is', () => {
    // `0-255.pbf` is a .pbf under a path of its own, and it is NOT a
    // numeric triple: one segment, and a hyphenated range rather than a
    // number. Judging it a tile would make every bucket holding glyphs
    // permanently stale, which evicts the band on every single warm.
    expect(
      core.isTileEntryURL('https://tiles.openfreemap.org/fonts/Noto%20Sans%20Bold/0-255.pbf'),
    ).toBe(false);
    expect(
      core.isTileEntryURL('https://tiles.openfreemap.org/fonts/Noto%20Sans%20Bold/256-511.pbf'),
    ).toBe(false);
  });

  it('rejects the sprite pair, at 1x and 2x', () => {
    expect(
      core.isTileEntryURL('https://tiles.openfreemap.org/sprites/ofm_f384/ofm@2x.json'),
    ).toBe(false);
    expect(
      core.isTileEntryURL('https://tiles.openfreemap.org/sprites/ofm_f384/ofm@2x.png'),
    ).toBe(false);
    expect(
      core.isTileEntryURL('https://tiles.openfreemap.org/sprites/ofm_f384/ofm.png'),
    ).toBe(false);
  });

  it('rejects a style document and a TileJSON', () => {
    expect(core.isTileEntryURL('https://tiles.openfreemap.org/styles/liberty')).toBe(false);
    expect(core.isTileEntryURL('https://tiles.openfreemap.org/planet')).toBe(false);
    expect(core.isTileEntryURL('https://mapsneu.wien.gv.at/basemapvectorneu/root.json')).toBe(
      false,
    );
  });

  it('rejects what it cannot parse rather than guessing', () => {
    expect(core.isTileEntryURL(undefined)).toBe(false);
    expect(core.isTileEntryURL(null)).toBe(false);
    expect(core.isTileEntryURL('')).toBe(false);
    expect(core.isTileEntryURL(42)).toBe(false);
    expect(core.isTileEntryURL({ url: 'https://example.invalid/7/66/45.pbf' })).toBe(false);
  });
});

describe('baseLayerStaleEntries', () => {
  const A = 'https://a.example.com/{z}/{x}/{y}.pbf';
  const STYLE = 'https://a.example.com/styles/liberty';
  const TILEJSON = 'https://a.example.com/planet';
  const SPRITE_JSON = 'https://a.example.com/sprites/ofm_f384/ofm@2x.json';
  const SPRITE_PNG = 'https://a.example.com/sprites/ofm_f384/ofm@2x.png';
  const GLYPH = 'https://a.example.com/fonts/Noto%20Sans%20Bold/0-255.pbf';

  // The band the current plan asks for, and the wider one a device that
  // ran SNOW-856's z0-9 layer is left holding.
  const thisBand = core.baseLayerTileURLs([[A]], CAMERA, SWISSTOPO);
  const oldBand = core.baseLayerTileURLs([[A]], CAMERA, SWISSTOPO, 'swisstopo_winter');
  const extraTiles = oldBand.filter((url) => !thisBand.includes(url));

  it("reports an older band's tiles, which is what re-banding is for", () => {
    // SNOW-863's migration, unchanged: the old band is a strict superset,
    // so the missing-url plan is empty and only this can free the bytes.
    expect(extraTiles.length).toBeGreaterThan(0);
    expect(core.baseLayerStaleEntries(oldBand, thisBand)).toEqual(extraTiles);
  });

  it('reports nothing for this band plus the documents that draw it', () => {
    // The SNOW-929 case. Every one of these documents is outside the tile
    // set by construction, and judging them would evict the band on the
    // very warm that fetched them.
    const bucket = thisBand.concat([STYLE, TILEJSON, SPRITE_JSON, SPRITE_PNG, GLYPH]);

    expect(core.baseLayerStaleEntries(bucket, thisBand)).toEqual([]);
  });

  it('never makes a bucket stale over an unrecognised document', () => {
    // The risk this shape exists to remove: the documents are derived from
    // the LIVE style, so a provider renaming a sprite path or adding a
    // fontstack moves them. Under the old all-entries check that renamed
    // entry was an unexpected entry, and the user paid for the whole band
    // again. Here it is simply left alone.
    const bucket = thisBand.concat([
      'https://a.example.com/sprites/ofm_f999/renamed@2x.png',
      'https://a.example.com/fonts/Some%20New%20Stack/0-255.pbf',
      'https://a.example.com/styles/liberty-v2',
    ]);

    expect(core.baseLayerStaleEntries(bucket, thisBand)).toEqual([]);
  });

  it('still reports a stale tile sitting beside the documents', () => {
    const bucket = [STYLE, GLYPH].concat(oldBand);

    expect(core.baseLayerStaleEntries(bucket, thisBand)).toEqual(extraTiles);
  });

  it('reports nothing for an empty or unusable bucket', () => {
    // "There is nothing to throw away" — an absent bucket is not a stale
    // one, and neither is one this cannot read.
    expect(core.baseLayerStaleEntries([], thisBand)).toEqual([]);
    expect(core.baseLayerStaleEntries(null, thisBand)).toEqual([]);
    expect(core.baseLayerStaleEntries(undefined, thisBand)).toEqual([]);
  });

  it('reports nothing when the expected set is empty', () => {
    // Failing to enumerate the band is not evidence that every tile on
    // disk is stale — and the caller's eviction is destructive.
    expect(core.baseLayerStaleEntries(oldBand, [])).toEqual([]);
    expect(core.baseLayerStaleEntries(oldBand, null)).toEqual([]);
    expect(core.baseLayerStaleEntries(oldBand, new Set())).toEqual([]);
  });

  it('takes the expected tiles as an array or a Set', () => {
    // Same contract as `blobFullyCached` and `missingRenderDependencies`,
    // so a caller holding either shape needs no conversion.
    expect(core.baseLayerStaleEntries(oldBand, new Set(thisBand))).toEqual(extraTiles);
  });
});

describe('planEviction with a base layer present', () => {
  const MB = 1024 * 1024;
  const base = { id: 'base-swisstopo_winter', bytes: 10 * MB, savedAt: '2026-01-01T00:00:00Z' };
  const oldArea = { id: 'region-CH-1', bytes: 30 * MB, savedAt: '2026-02-01T00:00:00Z' };
  const newArea = { id: 'region-CH-2', bytes: 30 * MB, savedAt: '2026-03-01T00:00:00Z' };

  // SNOW-XXX reverses what SNOW-856 asserted here. It counted a base layer
  // toward the standing total, on the reasoning that a budget ignoring real
  // disk is a lie. True, but it is the wrong budget: the z0-9 layer is the
  // app's own map data — fetched once per basemap, shared by every area,
  // never chosen and never removable on its own — and this budget is the
  // one the user set for their downloads. Charging it here spent up to
  // 100 MB of their allowance on something they cannot point at, and on a
  // small budget could make their second download impossible. It is
  // accounted for in account settings' Reset local data summary instead.

  it('does not count a base layer toward the standing total', () => {
    const plan = core.planEviction([base], { id: 'region-CH-3', bytes: 30 * MB }, 100 * MB);

    expect(plan.fits).toBe(true);
    expect(plan.projectedBytes).toBe(30 * MB);
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
    expect(plan.projectedBytes).toBe(60 * MB);
  });

  it('lets a run fit that the old un-evictable floor refused', () => {
    // The case the change is FOR. 35 MB incoming against a 40 MB budget
    // fits once the areas are evicted; under SNOW-856 the base layer's
    // 10 MB sat in the way and this was refused outright.
    const plan = core.planEviction(
      [base, oldArea, newArea],
      { id: 'region-CH-3', bytes: 35 * MB },
      40 * MB,
    );

    expect(plan.impossible).toBe(false);
    expect(plan.evict).toEqual([oldArea.id, newArea.id]);
    expect(plan.projectedBytes).toBe(35 * MB);
  });

  it('still refuses a run bigger than the whole budget', () => {
    // Nothing un-evictable is left in the arithmetic, so the only
    // impossible run is one that does not fit an empty device.
    const plan = core.planEviction(
      [base, oldArea, newArea],
      { id: 'region-CH-3', bytes: 90 * MB },
      80 * MB,
    );

    expect(plan.impossible).toBe(true);
    expect(plan.evict).toEqual([]);
  });

  it('is unchanged for a device with no base layer', () => {
    // The pre-SNOW-856 behaviour, which is also the behaviour now.
    const plan = core.planEviction(
      [oldArea, newArea],
      { id: 'region-CH-3', bytes: 30 * MB },
      70 * MB,
    );

    expect(plan.evict).toEqual([oldArea.id]);
    expect(plan.projectedBytes).toBe(60 * MB);
  });
});
