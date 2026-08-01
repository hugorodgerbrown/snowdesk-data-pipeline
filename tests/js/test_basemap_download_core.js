/*
 * tests/js/test_basemap_download_core.js — Vitest unit tests for
 * static/js/basemap_download_core.js (SNOW-521 rework; SNOW-522 adds the
 * client-side tile-math port for the custom-area download).
 *
 * Covers ``rangesToTileURLs`` (expanding a full basemap_download blob's
 * ``z`` tile-index ranges into ``{z}/{x}/{y}`` URLs) and
 * ``centreTileURL`` (the single "done-probe" tile URL for a download
 * summary) — the two pure functions left after SNOW-521's per-region
 * rework moved tile enumeration and byte-estimate arithmetic server-side
 * (``apps/regions/services/basemap_tiles.py``) — plus, from SNOW-522,
 * ``lonLatToTile``/``tileRangesForBBox``/``tileCount``/``centreTile``/
 * ``buildBlob``, a deliberate re-port of that same Python module's pure
 * functions for the custom-area control (a user-drawn bbox has no stable
 * ID to precompute against server-side — see the JS module's header for
 * the full rationale).
 *
 * SNOW-566 adds ``budgetScaleForBBox`` — the frame-sizing solve, which
 * unlike everything else here is client-only and has no Python twin to
 * stay honest against. Its tests are property-shaped rather than
 * golden-vector-shaped, because the properties ARE the fix: the frame
 * juddered because the size it was given moved with the box's alignment
 * to the tile grid, so what has to be asserted is that the answer now
 * depends on the box's SIZE alone, changes smoothly with it, and still
 * never buys more tiles than the ceiling allows.
 *
 * The ``describe('buildBlob (golden vector)', ...)`` block below is the
 * JS twin of ``tests/regions/services/test_basemap_tiles.py``'s
 * ``test_build_blob_golden_vector_matches_js_twin`` — same bbox, same
 * band, same hand-checked expected ranges/count/mb/centre_tile. Keeping
 * both assertions in one place each, cross-referencing the other by
 * name, is how a re-port with no compiler/typechecker link between the
 * two languages stays honest against drift.
 *
 * SNOW-569/571 add the geometry helpers behind the on-map download
 * progress grid — ``geometryBounds``/``bboxPolygon``/``tileBounds``/
 * ``gridZoomFor``/``tileGridPlan``. Like ``budgetScaleForBBox`` these are
 * client-only with no Python twin, and their tests are likewise
 * property-shaped. The load-bearing properties: ``tileBounds`` inverts
 * ``lonLatToTile`` and tiles a zoom level seamlessly; a plan's per-cell
 * totals partition the run exactly (so a cell's countdown hits zero when
 * its tiles land, and not before); each cell's URLs are contiguous (the
 * ordering is the whole reason the grid fills one square at a time); and
 * the plan's URL SET matches ``rangesToTileURLs`` — the grid reorders the
 * download without changing what it caches.
 *
 * SNOW-570 adds ``downloadedIds`` — the pure half of the "Downloaded
 * areas" overlay. Its load-bearing property is that it requires an area's
 * WHOLE tile set, not just its centre tile: the two download shapes share
 * one cache, one band and one URL template, so a download that merely
 * crosses a region caches some of that region's tiles (its centre one
 * included) without covering it. It is also per-template, which is what
 * makes the overlay change when the basemap does.
 *
 * `basemap_download_core.js` is a plain IIFE that assigns a frozen
 * `self.pwaBasemapDownloadCore` — jsdom's global is `window`, which is
 * also `self` in a window context, so importing it for side effects is
 * enough (no service-worker environment or MapLibre instance needed).
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/basemap_download_core.js';

const core = self.pwaBasemapDownloadCore;

const TEMPLATE = 'https://tiles.example.com/{z}/{x}/{y}.pbf';

describe('rangesToTileURLs', () => {
  it('expands a single-tile range at one zoom level', () => {
    const blob = { z: { '10': [5, 5, 3, 3] } };
    expect(rangesToTileURLs_sorted(blob)).toEqual([
      'https://tiles.example.com/10/5/3.pbf',
    ]);
  });

  it('expands every tile in a multi-tile range', () => {
    const blob = { z: { '8': [0, 1, 0, 1] } };
    expect(rangesToTileURLs_sorted(blob)).toEqual([
      'https://tiles.example.com/8/0/0.pbf',
      'https://tiles.example.com/8/0/1.pbf',
      'https://tiles.example.com/8/1/0.pbf',
      'https://tiles.example.com/8/1/1.pbf',
    ]);
  });

  it('covers every zoom level in the z map', () => {
    const blob = { z: { '10': [5, 5, 3, 3], '11': [10, 10, 6, 6] } };
    expect(rangesToTileURLs_sorted(blob)).toEqual([
      'https://tiles.example.com/10/5/3.pbf',
      'https://tiles.example.com/11/10/6.pbf',
    ]);
  });

  it('returns [] for a falsy template', () => {
    const blob = { z: { '10': [5, 5, 3, 3] } };
    expect(core.rangesToTileURLs('', blob)).toEqual([]);
    expect(core.rangesToTileURLs(null, blob)).toEqual([]);
  });

  it('returns [] for a falsy blob or a blob with no z key', () => {
    expect(core.rangesToTileURLs(TEMPLATE, null)).toEqual([]);
    expect(core.rangesToTileURLs(TEMPLATE, {})).toEqual([]);
  });

  // Sorted for deterministic assertion order — object key iteration order
  // for numeric-string keys is insertion order in every engine Vitest
  // runs under, but sorting removes any doubt.
  function rangesToTileURLs_sorted(blob) {
    return [...core.rangesToTileURLs(TEMPLATE, blob)].sort();
  }
});

describe('centreTileURL', () => {
  it('builds the tile URL for a summary centre_tile', () => {
    const summary = { centre_tile: { z: 14, x: 8501, y: 5820 } };
    expect(core.centreTileURL(TEMPLATE, summary)).toBe(
      'https://tiles.example.com/14/8501/5820.pbf',
    );
  });

  it('returns null for a falsy template', () => {
    const summary = { centre_tile: { z: 14, x: 1, y: 1 } };
    expect(core.centreTileURL('', summary)).toBeNull();
    expect(core.centreTileURL(null, summary)).toBeNull();
  });

  it('returns null for a falsy summary or a summary with no centre_tile', () => {
    expect(core.centreTileURL(TEMPLATE, null)).toBeNull();
    expect(core.centreTileURL(TEMPLATE, {})).toBeNull();
  });
});

describe('lonLatToTile', () => {
  it('null island always lands on the exact centre tile', () => {
    for (const z of [1, 5, 8, 10, 14]) {
      expect(core.lonLatToTile(0.0, 0.0, z)).toEqual([2 ** (z - 1), 2 ** (z - 1)]);
    }
  });

  it('collapses to a single tile at zoom 0', () => {
    expect(core.lonLatToTile(0.0, 0.0, 0)).toEqual([0, 0]);
    expect(core.lonLatToTile(-179.0, 46.0, 0)).toEqual([0, 0]);
  });

  it('the west antimeridian is always tile column 0', () => {
    for (const z of [1, 4, 10]) {
      const [x] = core.lonLatToTile(-180.0, 0.0, z);
      expect(x).toBe(0);
    }
  });
});

describe('tileRangesForBBox', () => {
  it('covers every zoom in the requested band, min<=max on both axes', () => {
    const bbox = [7.0, 46.0, 8.0, 47.0];
    const ranges = core.tileRangesForBBox(bbox, 8, 12);
    expect(Object.keys(ranges).sort()).toEqual(['10', '11', '12', '8', '9'].sort());
    for (const [xmin, xmax, ymin, ymax] of Object.values(ranges)) {
      expect(xmin).toBeLessThanOrEqual(xmax);
      expect(ymin).toBeLessThanOrEqual(ymax);
    }
  });

  it('clamps the east-edge index to 2**z - 1, never 2**z', () => {
    const bbox = [179.0, -1.0, 180.0, 1.0];
    const ranges = core.tileRangesForBBox(bbox, 4, 4);
    const [xmin, xmax] = ranges['4'];
    expect(xmax).toBe(2 ** 4 - 1);
    expect(xmin).toBeLessThanOrEqual(xmax);
  });
});

describe('tileCount', () => {
  it('sums (xmax-xmin+1) * (ymax-ymin+1) across every zoom', () => {
    const ranges = { '8': [10, 12, 5, 6], '9': [20, 20, 10, 10] };
    // z=8: (12-10+1) * (6-5+1) = 3 * 2 = 6
    // z=9: (20-20+1) * (10-10+1) = 1 * 1 = 1
    expect(core.tileCount(ranges)).toBe(7);
  });
});

describe('centreTile', () => {
  it('a bbox symmetric around (0, 0) centres on the exact grid centre', () => {
    const bbox = [-1.0, -1.0, 1.0, 1.0];
    for (const z of [8, 14]) {
      expect(core.centreTile(bbox, z)).toEqual({ z, x: 2 ** (z - 1), y: 2 ** (z - 1) });
    }
  });
});

describe('buildBlob (golden vector)', () => {
  // Twin of tests/regions/services/test_basemap_tiles.py's
  // test_build_blob_golden_vector_matches_js_twin — same bbox/band,
  // hand-checked against a real build_blob() run. See this file's module
  // docstring for why the pairing matters.
  const bbox = [7.0, 46.0, 7.2, 46.2];
  const [minZ, maxZ] = core.MICRO_BAND;

  it('matches the hand-checked shape and values', () => {
    const blob = core.buildBlob(bbox, minZ, maxZ);
    expect(blob).toEqual({
      band: [10, 14],
      count: 205,
      mb: 21,
      over_ceiling: false,
      centre_tile: { z: 14, x: 8515, y: 5822 },
      z: {
        '10': [531, 532, 363, 364],
        '11': [1063, 1064, 726, 728],
        '12': [2127, 2129, 1453, 1457],
        '13': [4255, 4259, 2907, 2914],
        '14': [8510, 8519, 5815, 5828],
      },
    });
  });

  it('flags over_ceiling for a near-continental bbox', () => {
    const hugeBbox = [-10.0, 30.0, 20.0, 60.0];
    const blob = core.buildBlob(hugeBbox, minZ, maxZ);
    expect(blob.over_ceiling).toBe(true);
    expect(blob.mb).toBeGreaterThan(core.DOWNLOAD_CEILING_MB);
  });
});

describe('budgetScaleForBBox', () => {
  const [minZ, maxZ] = core.MICRO_BAND;
  const scaleFor = (bbox) => core.budgetScaleForBBox(bbox, minZ, maxZ);
  const mbFor = (bbox) => core.buildBlob(bbox, minZ, maxZ).mb;

  // Web Mercator y (a fraction of the world, 0 at the north pole) for a
  // latitude, and its inverse. The map's framing rectangle is a fixed box
  // of SCREEN pixels, so shrinking or moving it scales/translates its
  // footprint in these units, not in degrees of latitude — the helpers
  // below let the tests model that faithfully. ``worldYToLat`` is checked
  // against the module's own projection in the first test rather than
  // taken on trust.
  const latToWorldY = (lat) => {
    const rad = (lat * Math.PI) / 180;
    return (1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2;
  };
  const worldYToLat = (y) => {
    const n = Math.PI * (1 - 2 * y);
    return (180 / Math.PI) * Math.atan(0.5 * (Math.exp(n) - Math.exp(-n)));
  };

  /**
   * ``bbox`` scaled about its centre by ``scale`` — the footprint the
   * framing rectangle covers once map.js has shrunk it by that factor.
   */
  const scaled = (bbox, scale) => {
    const [west, south, east, north] = bbox;
    const midLon = (west + east) / 2;
    const halfLon = ((east - west) / 2) * scale;
    const midY = (latToWorldY(south) + latToWorldY(north)) / 2;
    const halfY = ((latToWorldY(south) - latToWorldY(north)) / 2) * scale;
    return [
      midLon - halfLon,
      worldYToLat(midY + halfY),
      midLon + halfLon,
      worldYToLat(midY - halfY),
    ];
  };

  /** A box ``spanLon`` x ``spanY`` (world units) centred on (lon, lat). */
  const boxAt = (lon, lat, spanLon, spanY) => [
    lon - spanLon / 2,
    worldYToLat(latToWorldY(lat) + spanY / 2),
    lon + spanLon / 2,
    worldYToLat(latToWorldY(lat) - spanY / 2),
  ];

  it('models the same projection the tile math floors', () => {
    // Guards the local helpers above: a latitude round-tripped through
    // worldYToLat must land in the tile the module itself computes.
    for (const y of [0.2, 0.33, 0.5, 0.67]) {
      const [, tileY] = core.lonLatToTile(0, worldYToLat(y), 14);
      expect(tileY).toBe(Math.floor(y * 2 ** 14));
    }
  });

  it('leaves a box that already fits alone', () => {
    const bbox = [7.0, 46.0, 7.2, 46.2];
    expect(mbFor(bbox)).toBeLessThan(core.DOWNLOAD_CEILING_MB);
    expect(scaleFor(bbox)).toBe(1);
  });

  it('keeps an oversized box under the ceiling once scaled', () => {
    const bbox = [-10.0, 30.0, 20.0, 60.0];
    const scale = scaleFor(bbox);
    expect(scale).toBeLessThan(1);
    expect(mbFor(scaled(bbox, scale))).toBeLessThanOrEqual(core.DOWNLOAD_CEILING_MB);
  });

  it('spends most of the budget rather than shrinking to nothing', () => {
    // The model bounds the tile count from above, so the box it sizes
    // leaves a little headroom unspent — a few percent, not a third.
    const bbox = [-10.0, 30.0, 20.0, 60.0];
    expect(mbFor(scaled(bbox, scaleFor(bbox)))).toBeGreaterThan(
      0.85 * core.DOWNLOAD_CEILING_MB,
    );
  });

  it('gives the same answer wherever an identically-sized box sits', () => {
    // The judder regression (SNOW-566): buildBlob's floored tile indices
    // make its count jump by a whole row or column as a box crosses the
    // grid, so a size derived by searching against that count wobbled as
    // the user panned. Same box, 40 positions, one answer.
    // Asserted as the width the frame is actually given — the answer is
    // only ever consumed as a pixel size, and comparing it that way keeps
    // the round-trip noise of building 40 test boxes out of the assertion.
    const widths = new Set();
    for (let i = 0; i < 40; i++) {
      const bbox = boxAt(6.0 + i * 0.017, 46.5 + i * 0.011, 6.0, 0.02);
      widths.add(Math.round(1000 * scaleFor(bbox)));
    }
    expect(widths.size).toBe(1);
    expect([...widths][0]).toBeLessThan(1000);
  });

  it('shrinks smoothly and monotonically as the box grows', () => {
    // The other half of the judder: zooming out grows the footprint
    // continuously, so the scale must fall continuously with it. A step
    // function would show up here as a jump between neighbouring spans.
    let previous = null;
    for (let i = 0; i < 200; i++) {
      // ~1% growth per step, from comfortably under the ceiling to far over.
      const spanLon = 0.5 * 1.01 ** i;
      const scale = scaleFor(boxAt(8.0, 46.5, spanLon, spanLon / 250));
      if (previous !== null) {
        expect(scale).toBeLessThanOrEqual(previous);
        // A 1% bigger box is never more than 1% more capped.
        expect(previous - scale).toBeLessThan(0.011 * previous);
      }
      previous = scale;
    }
    expect(previous).toBeLessThan(0.2);
  });

  it('returns a usable factor for a degenerate box', () => {
    expect(scaleFor([7.0, 46.0, 7.0, 46.0])).toBe(1);
  });
});

describe('MICRO_BAND / WORST_CASE_BYTES_PER_TILE / DOWNLOAD_CEILING_MB', () => {
  it('mirror the Python module-level constants', () => {
    expect(core.MICRO_BAND).toEqual([10, 14]);
    expect(core.WORST_CASE_BYTES_PER_TILE).toBe(100 * 1024);
    expect(core.DOWNLOAD_CEILING_MB).toBe(200);
  });
});

/*
 * SNOW-568 — the storage pre-flight. Also client-only (no Python twin);
 * its job is to refuse a download that cannot fit BEFORE the run spends
 * a few hundred fetches finding out.
 */
describe('hasStorageHeadroom', () => {
  const MB = 1024 * 1024;

  it('allows a download that fits well inside the remaining quota', () => {
    // 1 GB free, half of it claimable, 200 MB wanted.
    expect(core.hasStorageHeadroom({ quota: 1000 * MB, usage: 0 }, 200)).toBe(true);
  });

  it('refuses a download larger than the remaining quota', () => {
    expect(core.hasStorageHeadroom({ quota: 1000 * MB, usage: 900 * MB }, 200)).toBe(false);
  });

  it('refuses a download that would fit only by claiming ALL the headroom', () => {
    // 300 MB free and 200 MB wanted: it would "fit", but only by leaving
    // the origin at its quota, where the browser starts evicting — and
    // the freshly-downloaded area is the first thing to go.
    expect(core.hasStorageHeadroom({ quota: 1000 * MB, usage: 700 * MB }, 200)).toBe(false);
  });

  it('is exact at the headroom boundary', () => {
    const free = 400 * MB; // 200 MB claimable at STORAGE_HEADROOM_FACTOR 0.5
    expect(core.hasStorageHeadroom({ quota: free, usage: 0 }, 200)).toBe(true);
    expect(core.hasStorageHeadroom({ quota: free, usage: 0 }, 201)).toBe(false);
  });

  it('claims exactly STORAGE_HEADROOM_FACTOR of what is left', () => {
    // Stated against the constant rather than the 0.5 baked into the
    // cases above, so retuning the factor fails here (where it is a
    // deliberate change) and not in five unrelated assertions.
    const free = 1000 * MB;
    const claimable = (free * core.STORAGE_HEADROOM_FACTOR) / MB;
    expect(core.hasStorageHeadroom({ quota: free, usage: 0 }, claimable)).toBe(true);
    expect(core.hasStorageHeadroom({ quota: free, usage: 0 }, claimable + 1)).toBe(false);
  });

  it('allows the download when the estimate is unusable', () => {
    // An unknown quota must never block a download that would have
    // worked — _warmCache's own QuotaExceededError handling is the
    // backstop for the case where it in fact would not have.
    expect(core.hasStorageHeadroom(null, 200)).toBe(true);
    expect(core.hasStorageHeadroom(undefined, 200)).toBe(true);
    expect(core.hasStorageHeadroom({}, 200)).toBe(true);
    expect(core.hasStorageHeadroom({ quota: 0, usage: 0 }, 200)).toBe(true);
    expect(core.hasStorageHeadroom({ quota: NaN, usage: 0 }, 200)).toBe(true);
    expect(core.hasStorageHeadroom({ quota: 1000 * MB }, 200)).toBe(true);
  });

  it('allows a download with no meaningful size', () => {
    expect(core.hasStorageHeadroom({ quota: 100 * MB, usage: 99 * MB }, 0)).toBe(true);
    expect(core.hasStorageHeadroom({ quota: 100 * MB, usage: 99 * MB }, NaN)).toBe(true);
  });
});

/*
 * SNOW-570 — the "which areas are downloaded?" overlay's pure half. The
 * caller does one cache.keys() pass; this turns that URL set into an answer
 * for every area at once.
 *
 * The load-bearing property is FULL coverage, not the centre-tile proxy the
 * roundel's done-probe uses. Both download shapes write to one pinned cache
 * over the same band with the same template, so their tiles are
 * indistinguishable strings: a custom-area download whose frame merely
 * crosses a region caches some of that region's tiles — including,
 * often, its centre one. A centre-tile probe rings the whole region on the
 * strength of a tile the download never meant to cover. Hence
 * ``does not report an area whose tiles are only partly cached`` and
 * ``a crossing download does not count as covering the region`` below,
 * which are the regression tests for exactly that.
 */
describe('downloadedIds', () => {
  const BAND = [10, 11];
  // Small enough that a whole download is a handful of tiles, so a test can
  // hold the entire cached set and remove one tile from it by hand.
  const BBOX = [7.0, 46.0, 7.2, 46.2];

  /**
   * Every tile URL a ``band`` download of ``bbox`` fetches.
   *
   * Built with the module's own tile math rather than hand-listed indices:
   * that math is pinned by its own golden vector against the Python twin,
   * and what these tests are about is the membership logic on top of it.
   *
   * @param {number[]} bbox
   * @param {number[]} band
   * @returns {string[]}
   */
  function urlsFor(bbox, band = BAND) {
    return core.rangesToTileURLs(TEMPLATE, {
      z: core.tileRangesForBBox(bbox, band[0], band[1]),
    });
  }

  const ENTRY = (id, bbox = BBOX, band = BAND) => ({ id, bbox, band });

  it('reports an area whose every tile is cached', () => {
    expect(core.downloadedIds(TEMPLATE, [ENTRY('a')], urlsFor(BBOX))).toEqual(['a']);
  });

  it('does not report an area whose tiles are only partly cached', () => {
    // One tile short is not downloaded. This is the whole point: the area
    // cannot be used offline, so it must not be ringed.
    const partial = urlsFor(BBOX).slice(1);
    expect(core.downloadedIds(TEMPLATE, [ENTRY('a')], partial)).toEqual([]);
  });

  it('a crossing download does not count as covering the region', () => {
    // A neighbouring area that overlaps this one — the reported bug: its
    // download caches some of this region's tiles (its centre tile among
    // them, since the overlap straddles the middle) without covering it.
    const crossing = [7.1, 46.1, 7.4, 46.4];
    const cached = new Set(urlsFor(crossing));
    // Precondition: the overlap really does cache this region's centre
    // tile, so a centre-tile probe WOULD have reported it. Without this the
    // test could pass for the wrong reason.
    const centre = core.centreTileURL(TEMPLATE, {
      centre_tile: core.centreTile(BBOX, BAND[1]),
    });
    expect(cached.has(centre)).toBe(true);
    expect(core.downloadedIds(TEMPLATE, [ENTRY('a')], cached)).toEqual([]);
  });

  it('reports an area wholly contained in a larger download', () => {
    // The other half of the same rule: whoever cached the tiles, if all of
    // them are there the area genuinely IS available offline.
    const containing = [6.5, 45.5, 8.0, 47.0];
    expect(core.downloadedIds(TEMPLATE, [ENTRY('a')], urlsFor(containing))).toEqual(['a']);
  });

  it('preserves input order', () => {
    const other = [8.0, 46.0, 8.2, 46.2];
    const cached = [...urlsFor(BBOX), ...urlsFor(other)];
    const entries = [ENTRY('a'), ENTRY('b', other), ENTRY('c', [9.0, 46.0, 9.2, 46.2])];
    expect(core.downloadedIds(TEMPLATE, entries, cached)).toEqual(['a', 'b']);
  });

  it('accepts an array as well as a Set', () => {
    expect(core.downloadedIds(TEMPLATE, [ENTRY('a')], urlsFor(BBOX))).toEqual(['a']);
    expect(core.downloadedIds(TEMPLATE, [ENTRY('a')], new Set(urlsFor(BBOX)))).toEqual(['a']);
  });

  it('honours each entry own band', () => {
    // An area saved over a different band must be checked against the tiles
    // it actually fetched, not against MICRO_BAND's.
    const narrow = [10, 10];
    expect(
      core.downloadedIds(TEMPLATE, [ENTRY('a', BBOX, narrow)], urlsFor(BBOX, narrow)),
    ).toEqual(['a']);
    // Those z10 tiles alone do not satisfy the wider default band.
    expect(core.downloadedIds(TEMPLATE, [ENTRY('a')], urlsFor(BBOX, narrow))).toEqual([]);
  });

  it('defaults a bandless entry to MICRO_BAND', () => {
    const entry = { id: 'a', bbox: BBOX };
    expect(core.downloadedIds(TEMPLATE, [entry], urlsFor(BBOX, core.MICRO_BAND)))
      .toEqual(['a']);
  });

  it('skips an entry with no bbox rather than claiming it', () => {
    // A region whose blob was never computed has nothing to check. It is
    // not downloaded — and an empty tile set must never vacuously pass.
    expect(core.downloadedIds(TEMPLATE, [{ id: 'no-bbox' }], urlsFor(BBOX))).toEqual([]);
  });

  it('is empty when nothing is cached', () => {
    expect(core.downloadedIds(TEMPLATE, [ENTRY('a')], new Set())).toEqual([]);
  });

  it('is per-basemap: the same cache answers differently per template', () => {
    // The whole reason the overlay changes when you switch basemap. Tiles
    // cached from one origin say nothing about another.
    const cached = urlsFor(BBOX);
    expect(core.downloadedIds(TEMPLATE, [ENTRY('a')], cached)).toEqual(['a']);
    expect(
      core.downloadedIds('https://other.example.com/{z}/{x}/{y}.pbf', [ENTRY('a')], cached),
    ).toEqual([]);
  });

  it('returns [] for a falsy template or entries', () => {
    const cached = urlsFor(BBOX);
    expect(core.downloadedIds('', [ENTRY('a')], cached)).toEqual([]);
    expect(core.downloadedIds(null, [ENTRY('a')], cached)).toEqual([]);
    expect(core.downloadedIds(TEMPLATE, null, cached)).toEqual([]);
  });

  it('tolerates a null entry in the list', () => {
    expect(core.downloadedIds(TEMPLATE, [null, ENTRY('a')], urlsFor(BBOX))).toEqual(['a']);
  });
});

describe('geometryBounds', () => {
  it('bounds a simple polygon', () => {
    const polygon = {
      type: 'Polygon',
      coordinates: [[[7, 46], [8, 46], [8, 47], [7, 47], [7, 46]]],
    };
    expect(core.geometryBounds(polygon)).toEqual([7, 46, 8, 47]);
  });

  it('spans every part of a multipolygon', () => {
    const geometry = {
      type: 'MultiPolygon',
      coordinates: [
        [[[7, 46], [8, 46], [8, 47], [7, 47], [7, 46]]],
        [[[9, 44], [10, 44], [10, 45], [9, 45], [9, 44]]],
      ],
    };
    expect(core.geometryBounds(geometry)).toEqual([7, 44, 10, 47]);
  });

  it('ignores holes, which are inside the outer ring by definition', () => {
    const geometry = {
      type: 'Polygon',
      coordinates: [
        [[7, 46], [8, 46], [8, 47], [7, 47], [7, 46]],
        [[7.4, 46.4], [7.6, 46.4], [7.6, 46.6], [7.4, 46.6], [7.4, 46.4]],
      ],
    };
    expect(core.geometryBounds(geometry)).toEqual([7, 46, 8, 47]);
  });

  it('returns null for a non-polygon or empty geometry', () => {
    expect(core.geometryBounds(null)).toBeNull();
    expect(core.geometryBounds({ type: 'Point', coordinates: [7, 46] })).toBeNull();
    expect(core.geometryBounds({ type: 'Polygon', coordinates: [] })).toBeNull();
  });
});

describe('bboxPolygon', () => {
  it('closes the ring on the south-west corner', () => {
    const polygon = core.bboxPolygon([7, 46, 8, 47]);
    expect(polygon.type).toBe('Polygon');
    expect(polygon.coordinates[0]).toEqual([
      [7, 46],
      [8, 46],
      [8, 47],
      [7, 47],
      [7, 46],
    ]);
  });

  it('round-trips through geometryBounds', () => {
    expect(core.geometryBounds(core.bboxPolygon([7, 46, 8, 47]))).toEqual([7, 46, 8, 47]);
  });
});

describe('tileBounds', () => {
  it('covers the whole world at zoom 0', () => {
    const [west, south, east, north] = core.tileBounds(0, 0, 0);
    expect(west).toBeCloseTo(-180, 9);
    expect(east).toBeCloseTo(180, 9);
    // Web Mercator's own latitude limit, not the poles.
    expect(north).toBeCloseTo(85.0511287798066, 6);
    expect(south).toBeCloseTo(-85.0511287798066, 6);
  });

  it('puts north above south and east above west', () => {
    const [west, south, east, north] = core.tileBounds(14, 8501, 5820);
    expect(east).toBeGreaterThan(west);
    expect(north).toBeGreaterThan(south);
  });

  it('inverts lonLatToTile — the tile containing a point covers it', () => {
    // The Valais point the rest of this suite uses. Projecting it forward
    // and the resulting index back must bracket the original position;
    // that round trip is the whole contract between the two functions.
    const lon = 7.5;
    const lat = 46.25;
    for (const z of [10, 12, 14]) {
      const [x, y] = core.lonLatToTile(lon, lat, z);
      const [west, south, east, north] = core.tileBounds(z, x, y);
      expect(lon).toBeGreaterThanOrEqual(west);
      expect(lon).toBeLessThan(east);
      expect(lat).toBeGreaterThan(south);
      expect(lat).toBeLessThanOrEqual(north);
    }
  });

  it('tiles a zoom level edge to edge with no gap or overlap', () => {
    // Neighbouring tiles must share an edge exactly, or the grid would
    // render hairline seams between its squares.
    const left = core.tileBounds(12, 2140, 1440);
    const right = core.tileBounds(12, 2141, 1440);
    const below = core.tileBounds(12, 2140, 1441);
    expect(right[0]).toBeCloseTo(left[2], 12);
    expect(below[3]).toBeCloseTo(left[1], 12);
  });
});

describe('gridZoomFor', () => {
  /**
   * A blob whose every zoom level is a `span` x `span` square of tiles.
   *
   * @param {number[]} zooms Zoom levels to include.
   * @param {function(number): number} span Tiles per side at a zoom.
   * @returns {{z: Object<string, number[]>}}
   */
  function blobWith(zooms, span) {
    const z = {};
    for (const level of zooms) z[String(level)] = [0, span(level) - 1, 0, span(level) - 1];
    return { z: z };
  }

  it('picks the shallowest level that reaches the cell floor', () => {
    // 1, 4, 16, 64, 256 cells at z10..z14 — z12's 16 is the first to
    // clear the default floor of 12.
    const blob = blobWith([10, 11, 12, 13, 14], (z) => Math.pow(2, z - 10));
    expect(core.gridZoomFor(blob)).toBe(12);
  });

  it('honours an explicit floor', () => {
    const blob = blobWith([10, 11, 12, 13, 14], (z) => Math.pow(2, z - 10));
    expect(core.gridZoomFor(blob, 1)).toBe(10);
    expect(core.gridZoomFor(blob, 100)).toBe(14);
  });

  it('falls back to the deepest level when nothing reaches the floor', () => {
    // A tiny area: even its finest level is only a handful of tiles, and
    // a grid of 9 squares beats no grid at all.
    const blob = blobWith([10, 11, 12], () => 3);
    expect(core.gridZoomFor(blob)).toBe(12);
  });

  it('returns null for a blob with nothing to draw', () => {
    expect(core.gridZoomFor(null)).toBeNull();
    expect(core.gridZoomFor({})).toBeNull();
    expect(core.gridZoomFor({ z: {} })).toBeNull();
  });
});

describe('tileGridPlan', () => {
  const TEMPLATE = 'https://tiles.example/{z}/{x}/{y}.pbf';

  // z10 is one tile; z11 is the 2x2 beneath it. Small enough to reason
  // about position by position, and it exercises both cell-assignment
  // directions: at a z11 grid the z10 tile is SHALLOWER than the grid.
  const BLOB = { z: { 10: [536, 536, 358, 358], 11: [1072, 1073, 716, 717] } };

  it('accounts for every tile exactly once', () => {
    const plan = core.tileGridPlan(TEMPLATE, BLOB, 4);
    expect(plan.gridZ).toBe(11);
    expect(plan.urls).toHaveLength(core.tileCount(BLOB.z));
    expect(new Set(plan.urls).size).toBe(plan.urls.length);
    // Per-cell totals are a partition of the run, which is what lets a
    // cell's countdown reach zero exactly when its tiles have landed.
    const summed = plan.cells.reduce((acc, cell) => acc + cell.total, 0);
    expect(summed).toBe(plan.urls.length);
  });

  it('groups each cell’s URLs together', () => {
    // The ordering IS the feature: a cell whose tiles were scattered
    // through the run could not light up until the run had nearly
    // finished. Every cell's indices must form one contiguous block.
    const plan = core.tileGridPlan(TEMPLATE, BLOB, 4);
    const firstSeen = new Map();
    let previous = null;
    plan.cellOfURL.forEach((cellIndex, i) => {
      if (previous !== null && cellIndex !== previous) {
        expect(firstSeen.has(cellIndex)).toBe(false);
      }
      if (!firstSeen.has(cellIndex)) firstSeen.set(cellIndex, i);
      previous = cellIndex;
    });
    expect(firstSeen.size).toBe(plan.cells.length);
  });

  it('assigns a tile shallower than the grid to one cell only', () => {
    // The z10 tile spans all four z11 cells. Counting it once — against
    // the north-westmost — is what keeps the totals a partition; letting
    // it credit all four would light squares over ground whose own z11
    // tile had not been fetched.
    const plan = core.tileGridPlan(TEMPLATE, BLOB, 4);
    expect(plan.cells).toHaveLength(4);
    const totals = plan.cells.map((cell) => cell.total);
    expect(totals.filter((t) => t === 2)).toHaveLength(1);
    expect(totals.filter((t) => t === 1)).toHaveLength(3);
    // North-westmost cell = lowest x and y = first in the sweep.
    expect(plan.cells[0].total).toBe(2);
  });

  it('orders cells north-west to south-east', () => {
    const plan = core.tileGridPlan(TEMPLATE, BLOB, 4);
    const keys = plan.cells.map((cell) => [cell.y, cell.x]);
    const sorted = [...keys].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    expect(keys).toEqual(sorted);
    // North-west first means the first cell's bbox is the northern and
    // western one of the set.
    const norths = plan.cells.map((cell) => cell.bbox[3]);
    const wests = plan.cells.map((cell) => cell.bbox[0]);
    expect(plan.cells[0].bbox[3]).toBe(Math.max(...norths));
    expect(plan.cells[0].bbox[0]).toBe(Math.min(...wests));
  });

  it('gives each cell the bbox of its own tile', () => {
    const plan = core.tileGridPlan(TEMPLATE, BLOB, 4);
    for (const cell of plan.cells) {
      expect(cell.bbox).toEqual(core.tileBounds(plan.gridZ, cell.x, cell.y));
    }
  });

  it('emits the same URL set as rangesToTileURLs, reordered', () => {
    // The download must be unaffected by the grid: same tiles, different
    // order. Anything else would mean the grid changed what gets cached.
    const plan = core.tileGridPlan(TEMPLATE, BLOB, 4);
    const flat = core.rangesToTileURLs(TEMPLATE, BLOB);
    expect([...plan.urls].sort()).toEqual([...flat].sort());
  });

  it('returns null when there is nothing to draw', () => {
    expect(core.tileGridPlan(TEMPLATE, null)).toBeNull();
    expect(core.tileGridPlan(TEMPLATE, { z: {} })).toBeNull();
    expect(core.tileGridPlan('', BLOB)).toBeNull();
  });
});
