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
 * SNOW-569 adds the geometry helpers behind the on-map download progress
 * fill — ``geometryBounds``/``bboxPolygon``/``fillLevelLatitude``/
 * ``clipPolygonBelow``. Like ``budgetScaleForBBox`` these are client-only
 * with no Python twin, and their tests are likewise property-shaped: what
 * has to hold is that the level rises monotonically and lands exactly on
 * the box's edges at 0 and 1, and that the clip keeps everything below the
 * line, drops everything above it, and always hands back rings a fill
 * layer can render.
 *
 * SNOW-570 adds ``downloadedIds`` — the pure half of the "Downloaded
 * areas" overlay. Its load-bearing properties are that it agrees with
 * ``centreTileURL`` (the roundel's own probe key, so the overlay and the
 * roundel can never disagree about one region) and that it is per-template,
 * which is what makes the overlay change when the basemap does.
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
 */
describe('downloadedIds', () => {
  const TILE = (z, x, y) => `https://tiles.example.com/${z}/${x}/${y}.pbf`;
  const ENTRY = (id, z, x, y) => ({ id, centre_tile: { z, x, y } });

  it('reports only the entries whose centre tile is cached', () => {
    const entries = [ENTRY('CH-1', 14, 1, 1), ENTRY('CH-2', 14, 2, 2)];
    const cached = new Set([TILE(14, 1, 1)]);
    expect(core.downloadedIds(TEMPLATE, entries, cached)).toEqual(['CH-1']);
  });

  it('preserves input order', () => {
    const entries = [ENTRY('a', 14, 1, 1), ENTRY('b', 14, 2, 2), ENTRY('c', 14, 3, 3)];
    const cached = new Set([TILE(14, 3, 3), TILE(14, 1, 1)]);
    expect(core.downloadedIds(TEMPLATE, entries, cached)).toEqual(['a', 'c']);
  });

  it('accepts an array as well as a Set', () => {
    const entries = [ENTRY('a', 14, 1, 1)];
    expect(core.downloadedIds(TEMPLATE, entries, [TILE(14, 1, 1)])).toEqual(['a']);
  });

  it('skips an entry with no centre_tile rather than claiming it', () => {
    // A region whose blob was never computed carries no centre_tile. It is
    // not downloaded — but it must also never be reported as downloaded by
    // accident, which a URL built from an undefined tile could do.
    const entries = [{ id: 'no-blob' }, ENTRY('a', 14, 1, 1)];
    expect(core.downloadedIds(TEMPLATE, entries, [TILE(14, 1, 1)])).toEqual(['a']);
  });

  it('is empty when nothing is cached', () => {
    const entries = [ENTRY('a', 14, 1, 1), ENTRY('b', 14, 2, 2)];
    expect(core.downloadedIds(TEMPLATE, entries, new Set())).toEqual([]);
  });

  it('is per-basemap: the same cache answers differently per template', () => {
    // The whole reason the overlay changes when you switch basemap. Tiles
    // cached from one origin say nothing about another's.
    const entries = [ENTRY('a', 14, 1, 1)];
    const cached = new Set([TILE(14, 1, 1)]);
    expect(core.downloadedIds(TEMPLATE, entries, cached)).toEqual(['a']);
    expect(
      core.downloadedIds('https://other.example.com/{z}/{x}/{y}.pbf', entries, cached),
    ).toEqual([]);
  });

  it('returns [] for a falsy template or entries', () => {
    const entries = [ENTRY('a', 14, 1, 1)];
    expect(core.downloadedIds('', entries, [TILE(14, 1, 1)])).toEqual([]);
    expect(core.downloadedIds(null, entries, [TILE(14, 1, 1)])).toEqual([]);
    expect(core.downloadedIds(TEMPLATE, null, [TILE(14, 1, 1)])).toEqual([]);
  });

  it('tolerates a null entry in the list', () => {
    expect(core.downloadedIds(TEMPLATE, [null, ENTRY('a', 14, 1, 1)], [TILE(14, 1, 1)]))
      .toEqual(['a']);
  });

  it('agrees with centreTileURL, which is the roundel probe key', () => {
    // The overlay and the per-region roundel must never disagree about
    // whether one region is downloaded. Both derive the key the same way;
    // asserting it here is what keeps that true if either moves.
    const entry = ENTRY('a', 14, 8501, 5820);
    const url = core.centreTileURL(TEMPLATE, entry);
    expect(core.downloadedIds(TEMPLATE, [entry], [url])).toEqual(['a']);
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

describe('fillLevelLatitude', () => {
  const BBOX = [7.0, 46.0, 8.0, 47.0];

  it('pins the ends to the box edges', () => {
    expect(core.fillLevelLatitude(BBOX, 0)).toBeCloseTo(46.0, 9);
    expect(core.fillLevelLatitude(BBOX, 1)).toBeCloseTo(47.0, 9);
  });

  it('clamps a fraction outside [0, 1] to those edges', () => {
    expect(core.fillLevelLatitude(BBOX, -0.5)).toBeCloseTo(46.0, 9);
    expect(core.fillLevelLatitude(BBOX, 3)).toBeCloseTo(47.0, 9);
  });

  it('rises monotonically with progress', () => {
    let previous = -Infinity;
    for (let pct = 0; pct <= 100; pct++) {
      const lat = core.fillLevelLatitude(BBOX, pct / 100);
      expect(lat).toBeGreaterThan(previous);
      previous = lat;
    }
  });

  it('interpolates in Mercator, not in degrees', () => {
    // The projection stretches northward, so the box's northern half
    // occupies more SCREEN height than its southern half — and half the
    // screen height is therefore reached slightly NORTH of the degree
    // midpoint. The gap is small (hundredths of a degree over a 1° box);
    // asserting its sign and rough size is what distinguishes this from a
    // linear-in-degrees implementation, which would land exactly on 46.5.
    const half = core.fillLevelLatitude(BBOX, 0.5);
    expect(half).toBeGreaterThan(46.5);
    expect(half - 46.5).toBeLessThan(0.01);
  });

  it('is linear in degrees for a degenerate box', () => {
    expect(core.fillLevelLatitude([7, 46, 8, 46], 0.5)).toBeCloseTo(46.0, 9);
  });
});

describe('clipPolygonBelow', () => {
  // A unit square from (7, 46) to (8, 47).
  const SQUARE = {
    type: 'Polygon',
    coordinates: [[[7, 46], [8, 46], [8, 47], [7, 47], [7, 46]]],
  };

  /**
   * The latitude range spanned by every position in `geometry`.
   *
   * @param {Object} geometry
   * @returns {[number, number]} ``[south, north]``.
   */
  function latSpan(geometry) {
    const bounds = core.geometryBounds(geometry);
    return [bounds[1], bounds[3]];
  }

  it('keeps nothing above the clip line', () => {
    const clipped = core.clipPolygonBelow(SQUARE, 46.25);
    expect(latSpan(clipped)).toEqual([46, 46.25]);
  });

  it('returns a MultiPolygon whatever the input type', () => {
    expect(core.clipPolygonBelow(SQUARE, 46.5).type).toBe('MultiPolygon');
    const multi = { type: 'MultiPolygon', coordinates: [SQUARE.coordinates] };
    expect(core.clipPolygonBelow(multi, 46.5).type).toBe('MultiPolygon');
  });

  it('returns the whole geometry when the line is above it', () => {
    const clipped = core.clipPolygonBelow(SQUARE, 99);
    expect(latSpan(clipped)).toEqual([46, 47]);
    expect(core.geometryBounds(clipped)).toEqual([7, 46, 8, 47]);
  });

  it('returns null when the line is below everything', () => {
    expect(core.clipPolygonBelow(SQUARE, 45)).toBeNull();
  });

  it('closes every ring it produces', () => {
    const clipped = core.clipPolygonBelow(SQUARE, 46.5);
    for (const polygon of clipped.coordinates) {
      for (const ring of polygon) {
        expect(ring.length).toBeGreaterThanOrEqual(4);
        expect(ring[0]).toEqual(ring[ring.length - 1]);
      }
    }
  });

  it('handles a concave ring, keeping both surviving lobes', () => {
    // A "U": two legs rising from a shared base, with a notch between
    // them. Clipping above the notch floor leaves both legs, which
    // Sutherland–Hodgman returns as one ring joined along the clip line
    // — so what matters is that the full x-extent survives, not the ring
    // count.
    const u = {
      type: 'Polygon',
      coordinates: [[
        [0, 0], [3, 0], [3, 3], [2, 3], [2, 1], [1, 1], [1, 3], [0, 3], [0, 0],
      ]],
    };
    const clipped = core.clipPolygonBelow(u, 2);
    expect(core.geometryBounds(clipped)).toEqual([0, 0, 3, 2]);
  });

  it('drops a polygon whose outer ring is entirely above the line', () => {
    const geometry = {
      type: 'MultiPolygon',
      coordinates: [
        [[[7, 46], [8, 46], [8, 46.5], [7, 46.5], [7, 46]]],
        [[[7, 48], [8, 48], [8, 49], [7, 49], [7, 48]]],
      ],
    };
    const clipped = core.clipPolygonBelow(geometry, 47);
    expect(clipped.coordinates).toHaveLength(1);
    expect(core.geometryBounds(clipped)).toEqual([7, 46, 8, 46.5]);
  });

  it('keeps a hole that is wholly below the line', () => {
    const withHole = {
      type: 'Polygon',
      coordinates: [
        [[7, 46], [8, 46], [8, 47], [7, 47], [7, 46]],
        [[7.2, 46.2], [7.4, 46.2], [7.4, 46.4], [7.2, 46.4], [7.2, 46.2]],
      ],
    };
    expect(core.clipPolygonBelow(withHole, 46.8).coordinates[0]).toHaveLength(2);
  });

  it('drops a hole that is wholly above the line', () => {
    // The hole is gone, so the outer ring below the line is solid —
    // which is right: every point of that hole is above the fill level.
    const withHole = {
      type: 'Polygon',
      coordinates: [
        [[7, 46], [8, 46], [8, 47], [7, 47], [7, 46]],
        [[7.2, 46.8], [7.4, 46.8], [7.4, 46.9], [7.2, 46.9], [7.2, 46.8]],
      ],
    };
    expect(core.clipPolygonBelow(withHole, 46.5).coordinates[0]).toHaveLength(1);
  });

  it('clips an unclosed ring identically to a closed one', () => {
    const open = { type: 'Polygon', coordinates: [[[7, 46], [8, 46], [8, 47], [7, 47]]] };
    expect(core.clipPolygonBelow(open, 46.5)).toEqual(core.clipPolygonBelow(SQUARE, 46.5));
  });

  it('returns null for a non-polygon geometry', () => {
    expect(core.clipPolygonBelow(null, 46)).toBeNull();
    expect(core.clipPolygonBelow({ type: 'LineString', coordinates: [[7, 46]] }, 46)).toBeNull();
  });
});
