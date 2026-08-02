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
 * SNOW-569 and the tile-grid rework that followed it add the geometry
 * helpers behind the on-map download progress grid —
 * ``geometryBounds``/``bboxPolygon``/``tileBounds``/``gridZoomFor``/
 * ``tileGridPlan``. Like ``budgetScaleForBBox`` these are client-only
 * with no Python twin, and their tests are likewise property-shaped. The
 * load-bearing properties: ``tileBounds`` inverts ``lonLatToTile`` and
 * tiles a zoom level seamlessly; the grid is drawn at the band's DEEPEST
 * zoom, so a square is a real tile and the scale is the same whatever the
 * area's size; a plan's per-cell totals partition the run exactly (so a
 * cell's countdown hits zero when its tiles land, and not before); each
 * cell's URLs are contiguous (the ordering is the whole reason the grid
 * fills one square at a time); and the plan's URL SET matches
 * ``rangesToTileURLs`` — the grid reorders the download without changing
 * what it caches.
 *
 * SNOW-570 adds ``blobFullyCached`` (originally ``downloadedIds``, a
 * multi-entry form dropped in SNOW-583 once its only callers each check
 * one blob at a time) — the pure half of "is this download actually
 * available offline?". Its load-bearing property is that it requires a
 * blob's WHOLE tile set, not just its centre tile: the two download
 * shapes share one cache, one band and one URL template, so a download
 * that merely crosses an area caches some of that area's tiles (its
 * centre one included) without covering it. It is also per-template,
 * which is what makes the answer change when the basemap does.
 *
 * SNOW-583 adds ``zoomRows`` — the accessor that lets every consumer of a
 * blob's ``z`` (``rangesToTileURLs``, ``tileCount``, ``tileGridPlan``,
 * ``blobFullyCached``) handle both shapes a zoom level's entry can now be:
 * ``buildBlob``'s 4-int rectangle (custom area; also what a stale
 * `max-age=86400` region response can still be for up to 24h after a
 * deploy) or a clipped region blob's ``{"<y>": [xmin, xmax]}`` row-span
 * map (``apps.regions.services.basemap_tiles.build_region_blob``).
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

  it('expands a clipped region blob row-map, not just a rectangle', () => {
    // SNOW-583: a region blob's z entry is {"<y>": [xmin, xmax]}, not the
    // 4-int rectangle a custom-area blob uses — this is the shape
    // /api/region-basemap-tiles/ now serves for a region.
    const blob = { z: { 14: { 5815: [8510, 8511], 5820: [8515, 8515] } } };
    expect(rangesToTileURLs_sorted(blob)).toEqual([
      'https://tiles.example.com/14/8510/5815.pbf',
      'https://tiles.example.com/14/8511/5815.pbf',
      'https://tiles.example.com/14/8515/5820.pbf',
    ]);
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

  it('sums row spans for a clipped region blob z map too', () => {
    // SNOW-583: row_tile_count's JS counterpart — via zoomRows, the same
    // function serves both shapes.
    const z = { 10: { 5: [1, 3], 6: [2, 2] }, 11: { 10: [0, 0] } };
    // z=10: (3-1+1) + (2-2+1) = 3 + 1 = 4
    // z=11: (0-0+1) = 1
    expect(core.tileCount(z)).toBe(5);
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
 * SNOW-583: one accessor for a blob's z, whichever shape it arrived in.
 * A custom-area blob's z is still buildBlob's 4-int rectangle; a region
 * blob fetched from /api/region-basemap-tiles/ is now a row-span map
 * (apps.regions.services.basemap_tiles.build_region_blob), and a stale
 * `max-age=86400` response can still hand back the old rectangle for up
 * to 24h after a deploy — zoomRows is what makes every consumer below
 * (rangesToTileURLs, tileCount, tileGridPlan, blobFullyCached) handle
 * both without knowing which one it got.
 */
describe('zoomRows', () => {
  it('expands a 4-int rectangle into one identical span per row', () => {
    expect(core.zoomRows([5, 7, 3, 4])).toEqual({
      '3': [5, 7],
      '4': [5, 7],
    });
  });

  it('collapses a single-row rectangle to one row', () => {
    expect(core.zoomRows([5, 5, 3, 3])).toEqual({ '3': [5, 5] });
  });

  it('returns a row-map object exactly as given', () => {
    const rows = { '3': [5, 6], '5': [5, 5] };
    expect(core.zoomRows(rows)).toBe(rows);
  });

  it('returns {} for a falsy entry', () => {
    expect(core.zoomRows(undefined)).toEqual({});
    expect(core.zoomRows(null)).toEqual({});
  });
});

/*
 * SNOW-570 — the "is this download actually available offline?" question,
 * widened by SNOW-583 (blobFullyCached replaces the old downloadedIds/
 * _bboxFullyCached pair now that its only two callers — the per-region and
 * custom-area done-probes — each check ONE blob rather than a list of
 * regions; the "Downloaded areas" overlay's per-region ring that needed
 * the list form is gone).
 *
 * The load-bearing property is FULL coverage, not a centre-tile proxy:
 * both download shapes write to one pinned cache over the same band with
 * the same template, so their tiles are indistinguishable strings, and a
 * download whose frame merely crosses an area caches some of that area's
 * tiles — including, often, its centre one — without covering it.
 */
describe('blobFullyCached', () => {
  const BAND = [10, 11];
  // Small enough that a whole download is a handful of tiles, so a test can
  // hold the entire cached set and remove one tile from it by hand.
  const BBOX = [7.0, 46.0, 7.2, 46.2];

  /**
   * A ``build_blob``-shaped blob for ``bbox``/``band`` — built with the
   * module's own tile math rather than hand-listed indices, so what these
   * tests are about is the membership logic on top of it.
   *
   * @param {number[]} bbox
   * @param {number[]} band
   * @returns {Object}
   */
  function blobFor(bbox, band = BAND) {
    return core.buildBlob(bbox, band[0], band[1]);
  }

  /** Every tile URL ``blobFor(bbox, band)`` fetches. */
  function urlsFor(bbox, band = BAND) {
    return core.rangesToTileURLs(TEMPLATE, blobFor(bbox, band));
  }

  it('reports true when every tile is cached', () => {
    expect(core.blobFullyCached(TEMPLATE, blobFor(BBOX), urlsFor(BBOX))).toBe(true);
  });

  it('reports false when tiles are only partly cached', () => {
    // One tile short is not downloaded. This is the whole point: the area
    // cannot be used offline, so it must not read as available.
    const partial = urlsFor(BBOX).slice(1);
    expect(core.blobFullyCached(TEMPLATE, blobFor(BBOX), partial)).toBe(false);
  });

  it('reports true for a blob wholly contained in a larger cached set', () => {
    // Whoever cached the tiles, if all of them are there the area
    // genuinely IS available offline.
    const containing = [6.5, 45.5, 8.0, 47.0];
    expect(core.blobFullyCached(TEMPLATE, blobFor(BBOX), urlsFor(containing))).toBe(true);
  });

  it('accepts an array as well as a Set', () => {
    expect(core.blobFullyCached(TEMPLATE, blobFor(BBOX), urlsFor(BBOX))).toBe(true);
    expect(core.blobFullyCached(TEMPLATE, blobFor(BBOX), new Set(urlsFor(BBOX)))).toBe(true);
  });

  it("checks the blob's OWN tiles, not a wider default band", () => {
    // There is no separate band argument to get wrong — a blob built over
    // a narrow band is checked against exactly its own z.
    const narrow = blobFor(BBOX, [10, 10]);
    expect(core.blobFullyCached(TEMPLATE, narrow, urlsFor(BBOX, [10, 10]))).toBe(true);
    // Only the z10 tiles cached does not satisfy the wider micro band.
    expect(core.blobFullyCached(TEMPLATE, blobFor(BBOX), urlsFor(BBOX, [10, 10]))).toBe(false);
  });

  it('is false for a falsy template, blob, or a blob with no z', () => {
    const cached = urlsFor(BBOX);
    expect(core.blobFullyCached('', blobFor(BBOX), cached)).toBe(false);
    expect(core.blobFullyCached(null, blobFor(BBOX), cached)).toBe(false);
    expect(core.blobFullyCached(TEMPLATE, null, cached)).toBe(false);
    expect(core.blobFullyCached(TEMPLATE, {}, cached)).toBe(false);
  });

  it('is false when nothing is cached', () => {
    expect(core.blobFullyCached(TEMPLATE, blobFor(BBOX), new Set())).toBe(false);
  });

  it('is per-basemap: the same cache answers differently per template', () => {
    // The whole reason the overlay changes when you switch basemap. Tiles
    // cached from one origin say nothing about another.
    const cached = urlsFor(BBOX);
    expect(core.blobFullyCached(TEMPLATE, blobFor(BBOX), cached)).toBe(true);
    expect(
      core.blobFullyCached('https://other.example.com/{z}/{x}/{y}.pbf', blobFor(BBOX), cached),
    ).toBe(false);
  });

  it('accepts a clipped row-map z, not just a rectangle', () => {
    // SNOW-583: a region blob's z is {"<y>": [xmin, xmax]}, not the 4-int
    // rectangle buildBlob produces — the dual-shape contract this function
    // has to honour via zoomRows.
    const blob = { z: { 10: { 363: [531, 532] } } };
    const urls = core.rangesToTileURLs(TEMPLATE, blob);
    expect(core.blobFullyCached(TEMPLATE, blob, urls)).toBe(true);
    expect(core.blobFullyCached(TEMPLATE, blob, urls.slice(1))).toBe(false);
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

describe('cachedTilesFromURLs', () => {
  const T = 'https://tiles.example.com/{z}/{x}/{y}.pbf';

  it('reads tile indices back out of cached URLs', () => {
    const urls = [
      'https://tiles.example.com/14/8501/5820.pbf',
      'https://tiles.example.com/14/8502/5820.pbf',
    ];
    expect(core.cachedTilesFromURLs(T, urls)).toEqual([
      { z: 14, x: 8501, y: 5820 },
      { z: 14, x: 8502, y: 5820 },
    ]);
  });

  it('round-trips whatever rangesToTileURLs produced', () => {
    // The overlay's honesty rests on this pair being exact inverses: the
    // cache holds the URLs one wrote, and the overlay draws what the other
    // reads back.
    const blob = { z: { 12: [2140, 2142, 1440, 1441] } };
    const urls = core.rangesToTileURLs(T, blob);
    const tiles = core.cachedTilesFromURLs(T, urls);
    expect(tiles).toHaveLength(urls.length);
    expect(new Set(tiles.map((t) => `${t.z}/${t.x}/${t.y}`))).toEqual(
      new Set(urls.map((u) => u.replace('https://tiles.example.com/', '').replace('.pbf', ''))),
    );
  });

  it('keeps only the requested zoom', () => {
    const urls = core.rangesToTileURLs(T, { z: { 10: [5, 5, 3, 3], 14: [80, 81, 48, 48] } });
    expect(core.cachedTilesFromURLs(T, urls, 14)).toHaveLength(2);
    expect(core.cachedTilesFromURLs(T, urls, 10)).toEqual([{ z: 10, x: 5, y: 3 }]);
  });

  it('ignores URLs from another basemap', () => {
    // Per-template is the point: tiles cached for one origin genuinely are
    // not cached for another, so the overlay must empty on a basemap swap.
    const urls = [
      'https://tiles.example.com/14/8501/5820.pbf',
      'https://other.example.net/14/8501/5820.pbf',
      'https://tiles.example.com/style.json',
    ];
    expect(core.cachedTilesFromURLs(T, urls)).toEqual([{ z: 14, x: 8501, y: 5820 }]);
  });

  it('honours the template’s own placeholder order', () => {
    // An ESRI VectorTileServer source is {z}/{y}/{x}; reading that as
    // {z}/{x}/{y} would draw every square transposed.
    const esri = 'https://esri.example.com/tile/{z}/{y}/{x}.pbf';
    expect(core.cachedTilesFromURLs(esri, ['https://esri.example.com/tile/14/5820/8501.pbf']))
      .toEqual([{ z: 14, x: 8501, y: 5820 }]);
  });

  it('is empty for a falsy or malformed template', () => {
    expect(core.cachedTilesFromURLs('', ['https://tiles.example.com/14/1/1.pbf'])).toEqual([]);
    expect(core.cachedTilesFromURLs(T, null)).toEqual([]);
    // No placeholders at all — nothing to read back.
    expect(core.cachedTilesFromURLs('https://tiles.example.com/a.pbf', ['x'])).toEqual([]);
  });
});

describe('gridZoomFor', () => {
  it('picks the deepest level in the blob', () => {
    // The band's detail floor, so a grid square is a real tile rather
    // than an arbitrary aggregate of them.
    expect(core.gridZoomFor({ z: { 10: [0, 1, 0, 1], 14: [0, 15, 0, 15] } })).toBe(14);
    expect(core.gridZoomFor({ z: { 11: [0, 1, 0, 1], 12: [0, 3, 0, 3] } })).toBe(12);
  });

  it('does not depend on how many tiles a level holds', () => {
    // A one-tile deepest level still wins: consistency of SCALE across
    // downloads is the property, not a target square count.
    expect(core.gridZoomFor({ z: { 10: [0, 9, 0, 9], 14: [7, 7, 7, 7] } })).toBe(14);
  });

  it('returns null for a blob with nothing to draw', () => {
    expect(core.gridZoomFor(null)).toBeNull();
    expect(core.gridZoomFor({})).toBeNull();
    expect(core.gridZoomFor({ z: {} })).toBeNull();
  });
});

describe('tileGridPlan', () => {
  const TEMPLATE = 'https://tiles.example/{z}/{x}/{y}.pbf';

  // z10 is one tile; z11 is the 2x2 beneath it — so the grid is drawn at
  // z11 (the deepest) and the z10 tile is SHALLOWER than the grid, which
  // is the interesting cell-assignment case. Small enough to reason about
  // position by position.
  const BLOB = { z: { 10: [536, 536, 358, 358], 11: [1072, 1073, 716, 717] } };

  it('accounts for every tile exactly once', () => {
    const plan = core.tileGridPlan(TEMPLATE, BLOB);
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
    const plan = core.tileGridPlan(TEMPLATE, BLOB);
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
    const plan = core.tileGridPlan(TEMPLATE, BLOB);
    expect(plan.cells).toHaveLength(4);
    const totals = plan.cells.map((cell) => cell.total);
    expect(totals.filter((t) => t === 2)).toHaveLength(1);
    expect(totals.filter((t) => t === 1)).toHaveLength(3);
    // Located by its indices rather than its position in the list — the
    // sweep order is a separate decision (see the ordering test) and this
    // assertion is about WHICH cell owns the coarse tile, not when it is
    // reached.
    const minX = Math.min(...plan.cells.map((c) => c.x));
    const minY = Math.min(...plan.cells.map((c) => c.y));
    const northWest = plan.cells.find((c) => c.x === minX && c.y === minY);
    expect(northWest.total).toBe(2);
  });

  it('keeps every cell inside the grid zoom’s own tile range', () => {
    // A coarse tile floors to a wider grid, so it starts west and north of
    // the area it was fetched for and its north-westmost fine cell can sit
    // outside the download's footprint. Unclamped, that drew a lattice of
    // stray squares off the edge of the area — squares over ground the run
    // does not cover.
    const blob = { z: { 10: [536, 536, 358, 358], 14: [8580, 8590, 5740, 5750] } };
    const plan = core.tileGridPlan(TEMPLATE, blob);
    const [xmin, xmax, ymin, ymax] = blob.z[14];
    for (const cell of plan.cells) {
      expect(cell.x).toBeGreaterThanOrEqual(xmin);
      expect(cell.x).toBeLessThanOrEqual(xmax);
      expect(cell.y).toBeGreaterThanOrEqual(ymin);
      expect(cell.y).toBeLessThanOrEqual(ymax);
    }
    // Still a partition — clamping folds the coarse tile onto an edge
    // cell rather than dropping it.
    expect(plan.cells.reduce((a, c) => a + c.total, 0)).toBe(core.tileCount(blob.z));
  });

  it('fills bottom-up, starting at the south-west corner', () => {
    // Bottom-up to match the download roundel's own fill. Tile y grows
    // southward, so "bottom row first" is DESCENDING y — getting that
    // backwards is the easy mistake, and it inverts the animation.
    const plan = core.tileGridPlan(TEMPLATE, BLOB);
    const ys = plan.cells.map((cell) => cell.y);
    expect(ys).toEqual([...ys].sort((a, b) => b - a));
    // Asserted in degrees too, so the test still means "south-west first"
    // even if the tile-index convention is ever revisited.
    expect(plan.cells[0].bbox[1]).toBe(Math.min(...plan.cells.map((c) => c.bbox[1])));
    expect(plan.cells[0].bbox[0]).toBe(Math.min(...plan.cells.map((c) => c.bbox[0])));
  });

  it('alternates row direction, so the sweep never jumps back', () => {
    // Boustrophedon: west-to-east, then east-to-west, and so on. A plain
    // raster scan restarts each row at the west edge, jumping the width of
    // the area — which reads as a repeating wipe rather than one rising
    // fill. Needs a grid wider than two columns to be meaningful, so this
    // builds its own 4x3 blob rather than using BLOB.
    const blob = { z: { 12: [2140, 2143, 1440, 1442] } };
    const plan = core.tileGridPlan(TEMPLATE, blob);
    expect(plan.cells).toHaveLength(12);

    const rows = [];
    for (const cell of plan.cells) {
      if (!rows.length || rows[rows.length - 1].y !== cell.y) {
        rows.push({ y: cell.y, xs: [] });
      }
      rows[rows.length - 1].xs.push(cell.x);
    }
    // Each row is visited exactly once — a row appearing twice would mean
    // the sweep left and came back.
    expect(rows).toHaveLength(3);
    expect(rows[0].xs).toEqual([2140, 2141, 2142, 2143]);
    expect(rows[1].xs).toEqual([2143, 2142, 2141, 2140]);
    expect(rows[2].xs).toEqual([2140, 2141, 2142, 2143]);

    // The property the alternation exists for: consecutive cells are
    // always neighbours, never a jump across the area.
    for (let i = 1; i < plan.cells.length; i++) {
      const dx = Math.abs(plan.cells[i].x - plan.cells[i - 1].x);
      const dy = Math.abs(plan.cells[i].y - plan.cells[i - 1].y);
      expect(dx + dy).toBe(1);
    }
  });

  it('gives each cell the bbox of its own tile', () => {
    const plan = core.tileGridPlan(TEMPLATE, BLOB);
    for (const cell of plan.cells) {
      expect(cell.bbox).toEqual(core.tileBounds(plan.gridZ, cell.x, cell.y));
    }
  });

  it('emits the same URL set as rangesToTileURLs, reordered', () => {
    // The download must be unaffected by the grid: same tiles, different
    // order. Anything else would mean the grid changed what gets cached.
    const plan = core.tileGridPlan(TEMPLATE, BLOB);
    const flat = core.rangesToTileURLs(TEMPLATE, BLOB);
    expect([...plan.urls].sort()).toEqual([...flat].sort());
  });

  it('returns null when there is nothing to draw', () => {
    expect(core.tileGridPlan(TEMPLATE, null)).toBeNull();
    expect(core.tileGridPlan(TEMPLATE, { z: {} })).toBeNull();
    expect(core.tileGridPlan('', BLOB)).toBeNull();
  });

  it('snaps a coarse tile to the nearest PRESENT grid row (SNOW-583)', () => {
    // A clipped region blob's grid rows are no longer necessarily
    // contiguous in y (a concave boundary can leave a gap between two
    // present rows) — the old single rectangular clamp would leave a
    // coarse tile's cy unclamped whenever it fell inside the OLD min/max
    // range but on a row that is, in a clipped blob, simply absent, and
    // crash looking up that row's span. Rows present at y=0 and y=10
    // (gap 1-9); the z10 tile's NW corner floors/scales to y=4 — closer
    // to 0 (distance 4) than to 10 (distance 6) — and to x=200, west of
    // row 0's own [201, 205] span, so BOTH steps of the clamp are
    // exercised: row first, then x within that row.
    const blob = {
      z: {
        10: [50, 50, 1, 1],
        12: { 0: [201, 205], 10: [201, 205] },
      },
    };
    const plan = core.tileGridPlan(TEMPLATE, blob);
    expect(plan.gridZ).toBe(12);
    // The coarse tile lands on row 0 (nearest), clamped to x=201 (row 0's
    // own western edge) — not the unclamped (200, 4) a single rectangular
    // clamp would have produced, which is not one of this grid's cells.
    expect(plan.cells.some((c) => c.x === 201 && c.y === 0)).toBe(true);
    expect(plan.cells.every((c) => c.y === 0 || c.y === 10)).toBe(true);
    // Still a partition — every tile in the blob is accounted for exactly
    // once, the coarse one included.
    expect(plan.cells.reduce((a, c) => a + c.total, 0)).toBe(core.tileCount(blob.z));
  });
});
