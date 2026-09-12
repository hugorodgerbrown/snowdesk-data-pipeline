/*
 * tests/js/test_basemap_download_core.js — Vitest unit tests for
 * static/js/basemap_download_core.js (SNOW-521 rework; SNOW-522 adds the
 * client-side tile-math port for the custom-area download).
 *
 * Covers ``rangesToTileURLs`` (expanding a full basemap_download blob's
 * ``z`` tile-index ranges into ``{z}/{x}/{y}`` URLs) — the one pure
 * function left after SNOW-521's per-region rework moved tile enumeration
 * and byte-estimate arithmetic server-side
 * (``apps/regions/services/basemap_tiles.py``), and now the oracle the
 * whole download path is checked against, since SNOW-615 deleted
 * ``centreTileURL`` as dead — plus, from SNOW-522,
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
 * SNOW-843 adds the tile-source group at the end — a basemap is one or
 * more vector SOURCES, each served from one or more hosts MapLibre
 * round-robins between, so every function here that builds or reads a tile
 * URL takes a source spec rather than a template string. Those tests are
 * written against MapLibre's own selection rule and against the tile
 * indices from the offline trace that reported the bug, so they fail if
 * either the rule or our reading of it drifts.
 *
 * SNOW-569 and the tile-grid rework that followed it add the geometry
 * helpers behind the on-map download progress grid —
 * ``bboxPolygon``/``tileBounds``/``gridZoomFor``/``tileGridPlan``. Like
 * ``budgetScaleForBBox`` these are client-only
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
 * SNOW-844 adds ``missingRenderDependencies`` — the OTHER half of that
 * same question, and the half nothing asked: a bucket full of tiles still
 * renders nothing without the style JSON, each vector source's TileJSON
 * and the sprite. Kept a separate function from ``blobFullyCached``
 * (whose tests below are untouched) because the two answers drive
 * different states — see its own docstring.
 *
 * SNOW-583 adds ``zoomRows`` — the accessor that lets every consumer of a
 * blob's ``z`` (``rangesToTileURLs``, ``tileCount``, ``tileGridPlan``,
 * ``blobFullyCached``) handle both shapes a zoom level's entry can now be:
 * ``buildBlob``'s 4-int rectangle (custom area; also what a region
 * response served from its stale window can still be — SNOW-902) or a clipped region blob's ``{"<y>": [xmin, xmax]}`` row-span
 * map (``apps.regions.services.basemap_tiles.build_region_blob``).
 *
 * SNOW-868 adds the per-basemap price at the end — a tile is not one size
 * across every style, and the single 50 KB figure the estimate spent on
 * all of them was OpenFreeMap's. Those tests are guards as much as
 * assertions: each constant is checked against the measured worst-region
 * p99 it has to clear, with the measurement in the comment, so a later
 * "tidy up these numbers" edit fails loudly rather than quietly turning
 * the readout's "up to N MB" back into something that is not an upper
 * bound.
 *
 * `basemap_download_core.js` is a plain IIFE that assigns a frozen
 * `self.pwaBasemapDownloadCore` — jsdom's global is `window`, which is
 * also `self` in a window context, so importing it for side effects is
 * enough (no service-worker environment or MapLibre instance needed).
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/basemap_download_core.js';
// SNOW-924: real CH micro-region boundaries, for the superset invariant
// at the foot of this file. The same source the app loads them from.
import CH_FIXTURE from '../../apps/regions/fixtures/eaws_CH.json';

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
      // 205 tiles at WORST_CASE_BYTES_PER_TILE (50 KB) = 11 MB, plus
      // DOWNLOAD_DOCUMENTS_MB for the style, sprite and promoted glyphs a
      // run writes beside its tiles.
      mb: 13,
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
    // SNOW-631: halving WORST_CASE_BYTES_PER_TILE halves mb at any given
    // tile count, so the scale needed to fit the same box under the
    // ceiling grows by roughly sqrt(2) — this floor moved with it.
    expect(previous).toBeLessThan(0.3);
  });

  it('returns a usable factor for a degenerate box', () => {
    expect(scaleFor([7.0, 46.0, 7.0, 46.0])).toBe(1);
  });
});

describe('MICRO_BAND / WORST_CASE_BYTES_PER_TILE / DOWNLOAD_CEILING_MB', () => {
  it('mirror the Python module-level constants', () => {
    expect(core.MICRO_BAND).toEqual([10, 14]);
    expect(core.WORST_CASE_BYTES_PER_TILE).toBe(50 * 1024);
    expect(core.DOWNLOAD_DOCUMENTS_MB).toBe(2);
    expect(core.DOWNLOAD_CEILING_MB).toBe(200);
  });
});

/*
 * SNOW-568 — the storage pre-flight. Also client-only (no Python twin);
 * its job is to refuse a download that cannot fit BEFORE the run spends
 * a few hundred fetches finding out.
 */
describe('circleBlob', () => {
  const [minZ, maxZ] = core.MICRO_BAND;
  const lat = 46.0961;
  const lon = 7.2286;
  const radiusKm = 10;

  it('fetches fewer tiles than the bounding box it fits in', () => {
    // A circle is pi/4 of its box, so this is the whole point of clipping:
    // roughly a fifth of the tiles a box download spent were corners the
    // user never selected.
    const latD = radiusKm / 111.32;
    const lonD = radiusKm / (111.32 * Math.cos((lat * Math.PI) / 180));
    const box = core.buildBlob([lon - lonD, lat - latD, lon + lonD, lat + latD], minZ, maxZ);
    const circle = core.circleBlob(lat, lon, radiusKm, minZ, maxZ);
    expect(circle.count).toBeLessThan(box.count);
    // Not a savage crop either — a clip that lost half the area would mean
    // the geometry is wrong, not tight.
    expect(circle.count).toBeGreaterThan(box.count * 0.6);
  });

  it('writes row spans every consumer already understands', () => {
    // Row spans are the shape a REGION download uses to clip to a boundary,
    // which is why nothing downstream needed changing.
    const blob = core.circleBlob(lat, lon, radiusKm, minZ, maxZ);
    const rows = blob.z[String(maxZ)];
    expect(Array.isArray(rows)).toBe(false);
    const spans = Object.values(rows);
    expect(spans.length).toBeGreaterThan(1);
    // A circle's rows are not all the same width; a rectangle's are.
    const widths = spans.map(([xmin, xmax]) => xmax - xmin);
    expect(new Set(widths).size).toBeGreaterThan(1);
    // Widest through the middle, narrowest at the ends.
    expect(Math.max(...widths)).toBe(widths[Math.floor(widths.length / 2)]);
  });

  it('agrees with tileCount and the URL list it produces', () => {
    const blob = core.circleBlob(lat, lon, radiusKm, minZ, maxZ);
    expect(core.tileCount(blob.z)).toBe(blob.count);
    const urls = core.rangesToTileURLs([['https://tiles.example/{z}/{x}/{y}.pbf']], blob);
    expect(urls.length).toBe(blob.count);
  });

  it('carries the same shape as buildBlob, ceiling included', () => {
    const blob = core.circleBlob(lat, lon, radiusKm, minZ, maxZ, 1);
    expect(blob.band).toEqual([minZ, maxZ]);
    expect(blob.centre_tile).toEqual(core.centreTile([lon, lat, lon, lat], maxZ));
    expect(blob.over_ceiling).toBe(true);
    expect(core.circleBlob(lat, lon, radiusKm, minZ, maxZ, 8000).over_ceiling).toBe(false);
  });

  it('grows with the radius', () => {
    const small = core.circleBlob(lat, lon, 2, minZ, maxZ);
    const large = core.circleBlob(lat, lon, 20, minZ, maxZ);
    expect(large.count).toBeGreaterThan(small.count);
  });
});

describe('deviceCeilingMb', () => {
  const MB = 1024 * 1024;

  it('is the largest download hasStorageHeadroom will pass', () => {
    // The two are inverses, and this is the property that matters: the
    // frame a user is offered and the pre-flight that accepts it must
    // never disagree about what fits.
    const estimate = { quota: 1000 * MB, usage: 137 * MB };
    const ceiling = core.deviceCeilingMb(estimate);
    expect(core.hasStorageHeadroom(estimate, ceiling)).toBe(true);
    expect(core.hasStorageHeadroom(estimate, ceiling + 1)).toBe(false);
  });

  it('grows with the device, past any constant', () => {
    // The point of the whole change: a roomy device is not held to
    // 200 MB because that number was once written down.
    expect(core.deviceCeilingMb({ quota: 64 * 1024 * MB, usage: 0 })).toBeGreaterThan(
      core.DOWNLOAD_CEILING_MB
    );
  });

  it('shrinks as the device fills', () => {
    const empty = core.deviceCeilingMb({ quota: 1000 * MB, usage: 0 });
    const full = core.deviceCeilingMb({ quota: 1000 * MB, usage: 900 * MB });
    expect(full).toBeLessThan(empty);
  });

  it('falls back to the constant when the device will not say', () => {
    // An unknown device gets the old fixed ceiling — never an unbounded
    // one, which is the failure that would matter.
    expect(core.deviceCeilingMb(null)).toBe(core.DOWNLOAD_CEILING_MB);
    expect(core.deviceCeilingMb({})).toBe(core.DOWNLOAD_CEILING_MB);
    expect(core.deviceCeilingMb({ quota: 0, usage: 0 })).toBe(core.DOWNLOAD_CEILING_MB);
  });

  it('never returns zero on a full device', () => {
    // A ceiling of 0 would make every surface that reads it grow a second
    // empty state; the quota pre-flight is where "no room" is said.
    expect(core.deviceCeilingMb({ quota: 1000 * MB, usage: 1000 * MB })).toBe(1);
  });
});

describe('buildBlob ceiling', () => {
  const [minZ, maxZ] = core.MICRO_BAND;
  // ~2 x 1.5 degrees — a few hundred MB at the current rate, so it is over
  // a 200 MB ceiling and well under a device-sized one.
  const bbox = [7.0, 46.0, 9.0, 47.5];

  it('measures over_ceiling against the ceiling it is given', () => {
    expect(core.buildBlob(bbox, minZ, maxZ, 200).over_ceiling).toBe(true);
    expect(core.buildBlob(bbox, minZ, maxZ, 8000).over_ceiling).toBe(false);
  });

  it('falls back to the constant when given none', () => {
    expect(core.buildBlob(bbox, minZ, maxZ).over_ceiling).toBe(
      core.buildBlob(bbox, minZ, maxZ, core.DOWNLOAD_CEILING_MB).over_ceiling
    );
  });

  it('lets budgetScaleForBBox draw a bigger box on a bigger ceiling', () => {
    const tight = core.budgetScaleForBBox(bbox, minZ, maxZ, 1, 200);
    const roomy = core.budgetScaleForBBox(bbox, minZ, maxZ, 1, 8000);
    expect(tight).toBeLessThan(1);
    expect(roomy).toBeGreaterThan(tight);
  });
});

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
 * (apps.regions.services.basemap_tiles.build_region_blob), and a response
 * served from the stale window can still hand back the old rectangle
 * (SNOW-902) — zoomRows is what makes every consumer below
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

/*
 * SNOW-844: the second half of "is this download actually available
 * offline?". `blobFullyCached` above answers only for TILES, and every
 * surface asked it alone — so an area whose style JSON, source TileJSON or
 * sprite is not in its bucket read `done` and came up blank offline. This
 * function names what is missing, because the repair fetches exactly that
 * list and nothing else.
 */
describe('missingRenderDependencies', () => {
  const STYLE = 'https://tiles.example.com/styles/winter/style.json';
  const TILEJSON = 'https://tiles.example.com/tiles/base/tiles.json';
  const SPRITE = 'https://tiles.example.com/sprites/winter.json';
  const DEPS = [STYLE, TILEJSON, SPRITE];

  it('returns the subset that is absent, in the order given', () => {
    expect(core.missingRenderDependencies(DEPS, [SPRITE])).toEqual([STYLE, TILEJSON]);
  });

  it('returns nothing when every dependency is present', () => {
    expect(core.missingRenderDependencies(DEPS, DEPS)).toEqual([]);
  });

  it('accepts an array as well as a Set, like blobFullyCached', () => {
    expect(core.missingRenderDependencies(DEPS, new Set(DEPS))).toEqual([]);
    expect(core.missingRenderDependencies(DEPS, new Set([STYLE]))).toEqual([
      TILEJSON,
      SPRITE,
    ]);
  });

  it('reports nothing for an empty or unusable dependency list', () => {
    // "Nothing was claimed", NOT "nothing is missing" — the UNKNOWN case a
    // legacy record with no stored `deps` lands in. A caller must never
    // paint that as a fault; see the three-row resolution rule in the
    // decision doc.
    expect(core.missingRenderDependencies([], [])).toEqual([]);
    expect(core.missingRenderDependencies(null, [])).toEqual([]);
    expect(core.missingRenderDependencies(undefined, DEPS)).toEqual([]);
  });

  it('reports every dependency when nothing at all is cached', () => {
    expect(core.missingRenderDependencies(DEPS, new Set())).toEqual(DEPS);
  });

  it('deduplicates, and skips entries that are not URLs', () => {
    // The live list is assembled by concatenation (style + sprite + one
    // TileJSON per vector source), and two sources of one basemap can name
    // the same document — a repair must not fetch it twice.
    const repeated = [TILEJSON, TILEJSON, '', null, STYLE];
    expect(core.missingRenderDependencies(repeated, new Set())).toEqual([TILEJSON, STYLE]);
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

/*
 * SNOW-586 — area identity and the standing byte-budget arithmetic that
 * replaced the pinned cache's old entry-count FIFO trim. See the module
 * header's "third client-only group" note.
 */

describe('areaIdForRegion / pinnedCacheName', () => {
  it('prefixes a region id', () => {
    expect(core.areaIdForRegion('CH-4115')).toBe('region-CH-4115');
  });

  it('round-trips through pinnedCacheName under the shared prefix', () => {
    const areaId = core.areaIdForRegion('CH-4115');
    expect(core.pinnedCacheName(areaId)).toBe(core.PINNED_CACHE_PREFIX + areaId);
    expect(core.pinnedCacheName(areaId).startsWith(core.PINNED_CACHE_PREFIX)).toBe(true);
  });

  it('CUSTOM_AREA_ID also round-trips', () => {
    expect(core.pinnedCacheName(core.CUSTOM_AREA_ID)).toBe(
      core.PINNED_CACHE_PREFIX + 'custom',
    );
  });
});

describe('planEviction', () => {
  const MB = 1024 * 1024;
  const BUDGET = 500 * MB;

  it('evicts nothing when the run already fits', () => {
    const areas = [{ id: 'a', bytes: 100 * MB, savedAt: '2026-01-01T00:00:00Z' }];
    const plan = core.planEviction(areas, { id: 'b', bytes: 50 * MB }, BUDGET);
    expect(plan).toEqual({
      fits: true,
      impossible: false,
      evict: [],
      projectedBytes: 150 * MB,
    });
  });

  it('evicts oldest areas first until the run fits', () => {
    const areas = [
      { id: 'a', bytes: 200 * MB, savedAt: '2026-01-03T00:00:00Z' },
      { id: 'b', bytes: 200 * MB, savedAt: '2026-01-01T00:00:00Z' },
      { id: 'c', bytes: 50 * MB, savedAt: '2026-01-02T00:00:00Z' },
    ];
    // Standing 450 MB + 100 MB incoming = 550 MB > 500 MB budget. Evicting
    // 'b' (oldest) alone frees 200 MB, landing at 350 MB — under budget.
    const plan = core.planEviction(areas, { id: 'd', bytes: 100 * MB }, BUDGET);
    expect(plan.impossible).toBe(false);
    expect(plan.fits).toBe(false);
    expect(plan.evict).toEqual(['b']);
    expect(plan.projectedBytes).toBe(350 * MB);
  });

  it('evicts more than one area, oldest first, when one is not enough', () => {
    const areas = [
      { id: 'a', bytes: 250 * MB, savedAt: '2026-01-01T00:00:00Z' },
      { id: 'b', bytes: 250 * MB, savedAt: '2026-01-02T00:00:00Z' },
    ];
    // Standing 500 MB + 300 MB incoming = 800 MB. Evicting only 'a' (the
    // oldest) leaves 550 MB, still over budget, so 'b' has to go too.
    const plan = core.planEviction(areas, { id: 'c', bytes: 300 * MB }, BUDGET);
    expect(plan.evict).toEqual(['a', 'b']);
    expect(plan.projectedBytes).toBe(300 * MB);
  });

  it('a re-download of an existing area frees its own bytes first', () => {
    // 'a' is being re-downloaded at a new size. Its OLD 400 MB must not
    // count against the new run, or every re-download would evict itself.
    const areas = [{ id: 'a', bytes: 400 * MB, savedAt: '2026-01-01T00:00:00Z' }];
    const plan = core.planEviction(areas, { id: 'a', bytes: 450 * MB }, BUDGET);
    expect(plan).toEqual({
      fits: true,
      impossible: false,
      evict: [],
      projectedBytes: 450 * MB,
    });
  });

  it('reports impossible for a run larger than the whole budget, evicting nothing', () => {
    const areas = [{ id: 'a', bytes: 10 * MB, savedAt: '2026-01-01T00:00:00Z' }];
    const plan = core.planEviction(areas, { id: 'b', bytes: 600 * MB }, BUDGET);
    expect(plan).toEqual({
      fits: false,
      impossible: true,
      evict: [],
      projectedBytes: 10 * MB,
    });
  });

  it('breaks a savedAt tie by id, deterministically', () => {
    const areas = [
      { id: 'z', bytes: 300 * MB, savedAt: '2026-01-01T00:00:00Z' },
      { id: 'a', bytes: 300 * MB, savedAt: '2026-01-01T00:00:00Z' },
    ];
    const plan = core.planEviction(areas, { id: 'c', bytes: 100 * MB }, BUDGET);
    // 'a' sorts before 'z' as the tiebreak, so it is evicted first.
    expect(plan.evict).toEqual(['a']);
  });

  it('treats a missing bytes/savedAt as zero/unset rather than throwing', () => {
    const areas = [{ id: 'a', savedAt: '2026-01-01T00:00:00Z' }];
    const plan = core.planEviction(areas, { id: 'b', bytes: 10 * MB }, BUDGET);
    expect(plan.fits).toBe(true);
    expect(plan.projectedBytes).toBe(10 * MB);
  });

  it('treats an empty areas list as an empty standing total', () => {
    const plan = core.planEviction([], { id: 'a', bytes: 10 * MB }, BUDGET);
    expect(plan).toEqual({
      fits: true,
      impossible: false,
      evict: [],
      projectedBytes: 10 * MB,
    });
  });
});

describe('tile sources — several sources, several hosts (SNOW-843)', () => {
  // The real shape this ticket exists for: the swisstopo winter style
  // declares two vector sources, each served from five hosts.
  const HOSTS = [0, 1, 2, 3, 4].map((n) => `https://vectortiles${n}.geo.admin.ch`);
  const RELIEF = HOSTS.map((h) => `${h}/tiles/relief.vt/{z}/{x}/{y}.pbf`);
  const BASE = HOSTS.map((h) => `${h}/tiles/base.vt/{z}/{x}/{y}.pbf`);
  const SOURCES = [RELIEF, BASE];
  /** One tile at z14. (8522 + 5829) % 5 === 1, so MapLibre asks host 1. */
  const ONE_TILE = { z: { 14: [8522, 8522, 5829, 5829] } };

  describe('tileSources', () => {
    it('reads a bare template string as one source with one host', () => {
      // The shape every download record written before this ticket holds.
      expect(core.tileSources(TEMPLATE)).toEqual([[TEMPLATE]]);
    });

    it('takes the nested shape as given, dropping empty sources', () => {
      expect(core.tileSources([RELIEF, [], BASE])).toEqual([RELIEF, BASE]);
    });

    it('reads a flat list as one single-host source each', () => {
      expect(core.tileSources(['a', 'b'])).toEqual([['a'], ['b']]);
    });

    it('answers nothing for anything unusable', () => {
      expect(core.tileSources(null)).toEqual([]);
      expect(core.tileSources('')).toEqual([]);
      expect(core.tileSources({})).toEqual([]);
      expect(core.tileSources([[], [null, 3]])).toEqual([]);
    });
  });

  describe('tileURLs', () => {
    it("picks each source's host the way MapLibre does", () => {
      // MapLibre: urls[(x + y) % urls.length] — CanonicalTileID.url in
      // static/js/maplibre-gl.min.js. Written out here rather than derived,
      // so this asserts the rule and not our restatement of it. The tiles
      // are the ones from the SNOW-843 offline trace, whose hosts the
      // service worker's own log named.
      expect(core.tileURLs([RELIEF], 14, 8522, 5828)[0]).toContain('vectortiles0.');
      expect(core.tileURLs([RELIEF], 14, 8522, 5829)[0]).toContain('vectortiles1.');
      expect(core.tileURLs([RELIEF], 14, 8520, 5827)[0]).toContain('vectortiles2.');
      expect(core.tileURLs([RELIEF], 14, 8521, 5827)[0]).toContain('vectortiles3.');
      expect(core.tileURLs([RELIEF], 14, 8521, 5828)[0]).toContain('vectortiles4.');
    });

    it('emits one URL per source, in style order', () => {
      expect(core.tileURLs(SOURCES, 14, 8522, 5829)).toEqual([
        'https://vectortiles1.geo.admin.ch/tiles/relief.vt/14/8522/5829.pbf',
        'https://vectortiles1.geo.admin.ch/tiles/base.vt/14/8522/5829.pbf',
      ]);
    });

    it('leaves a single-host source alone', () => {
      expect(core.tileURLs([[TEMPLATE]], 14, 8522, 5829)).toEqual([
        'https://tiles.example.com/14/8522/5829.pbf',
      ]);
    });
  });

  describe('tileSourcesKey', () => {
    it('reads a legacy string and its normalised form as the same basemap', () => {
      // What keeps an area downloaded before this ticket, under a
      // single-source basemap, still matching that basemap afterwards.
      expect(core.tileSourcesKey(TEMPLATE)).toBe(core.tileSourcesKey([[TEMPLATE]]));
    });

    it('separates two different source sets', () => {
      expect(core.tileSourcesKey(SOURCES)).not.toBe(core.tileSourcesKey([RELIEF]));
    });

    it("answers '' for an unresolved style, which matches nothing", () => {
      expect(core.tileSourcesKey(null)).toBe('');
      expect(core.tileSourcesKey(null)).not.toBe(core.tileSourcesKey(SOURCES));
    });
  });

  describe('rangesToTileURLs', () => {
    it('fetches every source of every tile', () => {
      expect(core.rangesToTileURLs(SOURCES, ONE_TILE)).toEqual([
        'https://vectortiles1.geo.admin.ch/tiles/relief.vt/14/8522/5829.pbf',
        'https://vectortiles1.geo.admin.ch/tiles/base.vt/14/8522/5829.pbf',
      ]);
    });
  });

  describe('blobFullyCached', () => {
    it('is false while one source of a tile is missing', () => {
      const all = core.rangesToTileURLs(SOURCES, ONE_TILE);
      expect(core.blobFullyCached(SOURCES, ONE_TILE, all)).toBe(true);
      // Relief down, base absent — hillshade with no roads or labels, which
      // is not the area being available offline.
      expect(core.blobFullyCached(SOURCES, ONE_TILE, all.slice(0, 1))).toBe(false);
    });

    it('is false when the tiles are cached under another host', () => {
      // The original bug, stated as an assertion: every tile pinned under
      // host 0, none of them at the host MapLibre asks for.
      const wrongHost = core
        .rangesToTileURLs(SOURCES, ONE_TILE)
        .map((url) => url.replace('vectortiles1.', 'vectortiles0.'));
      expect(core.blobFullyCached(SOURCES, ONE_TILE, wrongHost)).toBe(false);
    });
  });

  describe('cachedTilesFromURLs', () => {
    it('counts a tile only when every source holds it', () => {
      const all = core.rangesToTileURLs(SOURCES, ONE_TILE);
      expect(core.cachedTilesFromURLs(SOURCES, all, 14)).toEqual([
        { z: 14, x: 8522, y: 5829 },
      ]);
      expect(core.cachedTilesFromURLs(SOURCES, all.slice(0, 1), 14)).toEqual([]);
    });

    it('reads a tile once, not once per host it could have come from', () => {
      const all = core.rangesToTileURLs([RELIEF], ONE_TILE);
      expect(core.cachedTilesFromURLs([RELIEF], all, 14)).toHaveLength(1);
    });
  });

  describe('tileGridPlan', () => {
    it("counts every source in a cell's total, so the grid fills honestly", () => {
      const plan = core.tileGridPlan(SOURCES, ONE_TILE);
      expect(plan.urls).toHaveLength(2);
      // Both URLs belong to the one cell — a square is a patch of ground,
      // and the ground is not covered until every layer over it is down.
      expect(plan.cellOfURL).toEqual([0, 0]);
      expect(plan.cells[0].total).toBe(2);
    });
  });

  describe('sourceScaledMb', () => {
    it("charges one estimate per source, at that basemap's own price", () => {
      // SOURCES is a two-source swisstopo spec, so SNOW-868 prices it at
      // 96 KB a tile a source rather than the blob's own 50 KB. With no
      // tile count to recompute from, only the TILE half of `mb` scales:
      // (50 - 2) * 2 * 96/50 rounds up to 185 MB of tiles, plus the
      // documents allowance once — not once per source, which is what the
      // old `mb * sources` charged.
      expect(core.sourceScaledMb(50, SOURCES)).toBe(187);
      // A SINGLE-source NATIONAL style still costs more than the fallback,
      // which is why the old `count <= 1` early return had to become an
      // unresolved-spec test: (50 - 2) * 96/50 -> 93, plus the documents.
      expect(core.sourceScaledMb(50, [RELIEF])).toBe(95);
    });

    it('leaves the estimate alone when the style is unresolved', () => {
      // An unknown basemap must not inflate an estimate on a guess.
      expect(core.sourceScaledMb(50, null)).toBe(50);
    });
  });

  describe('budgetScaleForBBox', () => {
    it('sizes a smaller frame for a two-source basemap', () => {
      const bbox = [7.0, 46.0, 8.0, 47.0];
      const [minZ, maxZ] = core.MICRO_BAND;
      const one = core.budgetScaleForBBox(bbox, minZ, maxZ, 1);
      const two = core.budgetScaleForBBox(bbox, minZ, maxZ, 2);
      expect(two).toBeLessThan(one);
      // The default is the single-source answer, which is what the golden
      // vector above asserts.
      expect(core.budgetScaleForBBox(bbox, minZ, maxZ)).toBe(one);
    });

    it('still fits the ceiling at the size it allows', () => {
      const bbox = [7.0, 46.0, 8.0, 47.0];
      const [minZ, maxZ] = core.MICRO_BAND;
      // The frame is sized at the SAME price the estimate charges it at —
      // size at 50 KB while pricing at 96 and every frame the control lets
      // you draw is over the ceiling, which latches Download off for good
      // (see budgetScaleForBBox's own note).
      const bytesPerTile = core.bytesPerTileForSources(SOURCES);
      const scale = core.budgetScaleForBBox(bbox, minZ, maxZ, 2, undefined, bytesPerTile);
      const width = (bbox[2] - bbox[0]) * scale;
      const height = (bbox[3] - bbox[1]) * scale;
      const cx = (bbox[0] + bbox[2]) / 2;
      const cy = (bbox[1] + bbox[3]) / 2;
      const blob = core.buildBlob(
        [cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2],
        minZ,
        maxZ,
      );
      expect(core.sourceScaledMb(blob.mb, SOURCES, blob.count)).toBeLessThanOrEqual(
        core.DOWNLOAD_CEILING_MB,
      );
    });
  });
});

describe('bytes per tile, per basemap (SNOW-868)', () => {
  // Real tile-source specs, in the shape `activeBasemapTileSources` hands
  // over. Only the HOST is load-bearing here — that is what the price is
  // resolved from, deliberately, rather than the basemap picker (see
  // `basemapKeyForTileSources`).
  const SWISSTOPO = [0, 1, 2, 3, 4].flatMap((n) => [
    [`https://vectortiles${n}.geo.admin.ch/tiles/relief.vt/{z}/{x}/{y}.pbf`],
    [`https://vectortiles${n}.geo.admin.ch/tiles/base.vt/{z}/{x}/{y}.pbf`],
  ]);
  const SWISSTOPO_TWO_SOURCE = [
    ['https://vectortiles0.geo.admin.ch/tiles/relief.vt/{z}/{x}/{y}.pbf'],
    ['https://vectortiles1.geo.admin.ch/tiles/base.vt/{z}/{x}/{y}.pbf'],
  ];
  const IGN = [['https://data.geopf.fr/tms/1.0.0/PLAN.IGN/{z}/{x}/{y}.pbf']];
  const BASEMAP_AT = [['https://mapsneu.wien.gv.at/basemapv/bmapv/{z}/{x}/{y}.pbf']];
  // OpenFreeMap's origin is deployment-dependent — staging self-hosts at
  // tiles.snowdesk-data.info — so there is no host to match on and it
  // resolves through the fallback, which is its own measured figure.
  const OPENFREEMAP = [['https://tiles.snowdesk-data.info/planet/{z}/{x}/{y}.pbf']];

  describe('basemapKeyForTileSources', () => {
    it('resolves each national basemap from its host', () => {
      expect(core.basemapKeyForTileSources(SWISSTOPO)).toBe('swisstopo_winter');
      expect(core.basemapKeyForTileSources(IGN)).toBe('ign_plan');
      expect(core.basemapKeyForTileSources(BASEMAP_AT)).toBe('basemap_at');
    });

    it('names no basemap for a host it does not know, OpenFreeMap included', () => {
      expect(core.basemapKeyForTileSources(OPENFREEMAP)).toBe('');
      expect(core.basemapKeyForTileSources(null)).toBe('');
    });
  });

  describe('bytesPerTileForSources', () => {
    it("falls back to OpenFreeMap's own measured figure", () => {
      // The fallback IS the default basemap's price (mean 25.2 KB a tile,
      // worst-region p99 34.7), which is why an unmatched host is not a
      // guess — it is the figure the whole estimate was calibrated on.
      expect(core.bytesPerTileForSources(OPENFREEMAP)).toBe(
        core.WORST_CASE_BYTES_PER_TILE,
      );
      expect(core.bytesPerTileForBasemap('openfreemap_liberty')).toBe(
        core.WORST_CASE_BYTES_PER_TILE,
      );
    });
  });

  describe('sourceScaledMb', () => {
    it('prices a thousand tiles per basemap', () => {
      // Written out rather than derived, so this asserts the arithmetic and
      // not our restatement of it. 1,000 tiles at each basemap's own figure,
      // times its source count, plus DOWNLOAD_DOCUMENTS_MB once.
      const mb = 51; // what buildBlob would have said on its own
      expect(core.sourceScaledMb(mb, SWISSTOPO_TWO_SOURCE, 1000)).toBe(190);
      expect(core.sourceScaledMb(mb, IGN, 1000)).toBe(96);
      expect(core.sourceScaledMb(mb, BASEMAP_AT, 1000)).toBe(143);
      expect(core.sourceScaledMb(mb, OPENFREEMAP, 1000)).toBe(51);
    });

    it('is exactly a no-op for a single-source basemap at the fallback price', () => {
      // The fallback is buildBlob's OWN arithmetic, so re-pricing a blob it
      // built must return the number it already carries — an estimate that
      // moved here would be this seam inventing megabytes.
      const blob = core.buildBlob([7.0, 46.0, 7.2, 46.2], 10, 12);
      expect(core.sourceScaledMb(blob.mb, OPENFREEMAP, blob.count)).toBe(blob.mb);
      expect(core.sourceScaledMb(blob.mb, OPENFREEMAP)).toBe(blob.mb);
    });

    it('leaves the estimate alone when the style is unresolved', () => {
      // Unchanged rule, narrower test: an unresolved SPEC never inflates an
      // estimate on a guess. A single-source national style is no longer
      // covered by it — those cost more than the fallback and are priced.
      expect(core.sourceScaledMb(42, [])).toBe(42);
      expect(core.sourceScaledMb(42, null)).toBe(42);
    });

    it('charges the documents allowance once, not once per source', () => {
      // The bug the count-based path fixes: `mb * sources` multiplied
      // DOWNLOAD_DOCUMENTS_MB too, so a two-source style was charged 4 MB of
      // style, sprite and TileJSON it fetches one copy of.
      expect(core.sourceScaledMb(2, SWISSTOPO_TWO_SOURCE, 0)).toBe(
        core.DOWNLOAD_DOCUMENTS_MB,
      );
    });

    it('stays above what a real swisstopo download actually wrote', () => {
      // Both figures are OBSERVED, not modelled. The 400-tile sample was
      // fetched from the real hosts over the real CH tile mix and averaged
      // 60.8 KB a tile a source; the Ybrig region download's own trace
      // reported 59.9 KB across 474 URLs. "Up to N MB" is only a promise if
      // the estimate clears both.
      const sampleMb = (400 * 2 * 60.8 * 1024) / (1024 * 1024);
      expect(core.sourceScaledMb(22, SWISSTOPO_TWO_SOURCE, 400)).toBeGreaterThan(
        sampleMb,
      );
      const ybrigMb = (474 * 59.9 * 1024) / (1024 * 1024);
      expect(core.sourceScaledMb(14, SWISSTOPO_TWO_SOURCE, 237)).toBeGreaterThan(
        ybrigMb,
      );
    });
  });

  describe('BYTES_PER_TILE_BY_BASEMAP', () => {
    it('keeps every constant above its measured worst-region p99', () => {
      // A download averages hundreds of tiles, so what the constant has to
      // bound is a DOWNLOAD's mean rather than the fattest tile. Bootstrapped
      // from the 400-tile sample at the real CH region sizes (min 56 tiles,
      // median 181, max 2,816), the worst case is the SMALLEST region:
      // OpenFreeMap 34.7 KB, swisstopo 85.5 KB per source. This is why 72 KB
      // was rejected for swisstopo despite clearing the 60.8 KB mean — a
      // small region would have exceeded it about 1% of the time. Do not
      // "tidy" these downwards without re-measuring; this assertion is what
      // makes that fail loudly.
      expect(core.BYTES_PER_TILE_BY_BASEMAP.openfreemap_liberty).toBeGreaterThan(
        34.7 * 1024,
      );
      expect(core.BYTES_PER_TILE_BY_BASEMAP.swisstopo_winter).toBeGreaterThan(
        85.5 * 1024,
      );
      // The two swisstopo styles differ in paint, not in tile sources, which
      // is also why the host cannot tell them apart — see
      // `basemapKeyForTileSources` on the key being a pricing representative.
      expect(core.BYTES_PER_TILE_BY_BASEMAP.swisstopo_light).toBe(
        core.BYTES_PER_TILE_BY_BASEMAP.swisstopo_winter,
      );
    });

    it('keeps the two PROVISIONAL constants above their crude samples', () => {
      // IGN and basemap.at have no stratified sample behind them — only CH
      // region geometry exists in the local fixture — so these come from a
      // four-point sample (IGN ~52 KB, basemap.at ~85 KB, the latter with a
      // 782 KB z10 tile in it) scaled by the CH worst-region factor. They are
      // generous rather than accurate, and this guards the direction.
      expect(core.BYTES_PER_TILE_BY_BASEMAP.ign_plan).toBeGreaterThan(52 * 1024);
      expect(core.BYTES_PER_TILE_BY_BASEMAP.basemap_at).toBeGreaterThan(85 * 1024);
    });
  });
});

// ---------------------------------------------------------------------------
// SNOW-847: glyph enumeration
//
// The expression walk is the whole point of these tests. The bug this
// ticket fixes is silent in both directions — a fontstack missed ships an
// area with no labels for those layers, and a non-font string collected
// (an operator name, a property read) puts a URL in the record's `deps`
// that no repair can ever satisfy. The swisstopo shape below is taken from
// the live winter style, where two of the five stacks appear ONLY inside a
// `["match", ["get", "class"], …]`.
// ---------------------------------------------------------------------------

describe('styleFontstacks', () => {
  it('reads a plain text-font array', () => {
    const style = {
      layers: [{ layout: { 'text-font': ['Frutiger Neue Regular'] } }],
    };
    expect(core.styleFontstacks(style)).toEqual(['Frutiger Neue Regular']);
  });

  it('finds a fontstack that appears only inside a match expression', () => {
    // The swisstopo case, verbatim in shape: without the literal walk these
    // two stacks are invisible and towns and lake elevations lose their
    // labels offline.
    const style = {
      layers: [
        {
          layout: {
            'text-font': [
              'match',
              ['get', 'class'],
              'town',
              ['literal', ['Frutiger Neue Medium']],
              'lake_elevation',
              ['literal', ['Frutiger Neue Condensed Medium']],
              ['literal', ['Frutiger Neue Regular']],
            ],
          },
        },
      ],
    };
    expect(core.styleFontstacks(style)).toEqual([
      'Frutiger Neue Condensed Medium',
      'Frutiger Neue Medium',
      'Frutiger Neue Regular',
    ]);
  });

  it('never collects an operator name or a property read as a font', () => {
    // The inverse failure: an earlier version of this walk returned
    // `class`, `lake_elevation` and `town` alongside the real fonts,
    // because it descended into `["get", …]` and took its operand.
    const style = {
      layers: [
        {
          layout: {
            'text-font': ['match', ['get', 'class'], 'town', ['literal', ['Arial Bold']], ['literal', ['Arial Regular']]],
          },
        },
      ],
    };
    const stacks = core.styleFontstacks(style);
    expect(stacks).toEqual(['Arial Bold', 'Arial Regular']);
    for (const name of ['class', 'town', 'get', 'match', 'literal']) {
      expect(stacks).not.toContain(name);
    }
  });

  it('deduplicates across layers and sorts, so a run records a stable list', () => {
    const style = {
      layers: [
        { layout: { 'text-font': ['Noto Sans Regular'] } },
        { layout: { 'text-font': ['Noto Sans Bold', 'Noto Sans Regular'] } },
        { layout: { 'text-font': ['Noto Sans Italic'] } },
      ],
    };
    expect(core.styleFontstacks(style)).toEqual([
      'Noto Sans Bold',
      'Noto Sans Italic',
      'Noto Sans Regular',
    ]);
  });

  it('answers [] for a style with no layers, no layout or no labels', () => {
    expect(core.styleFontstacks(null)).toEqual([]);
    expect(core.styleFontstacks({})).toEqual([]);
    expect(core.styleFontstacks({ layers: [{}] })).toEqual([]);
    expect(core.styleFontstacks({ layers: [{ layout: {} }] })).toEqual([]);
  });
});

describe('glyphURLs', () => {
  const style = {
    glyphs: 'https://vectortiles.geo.admin.ch/fonts/{fontstack}/{range}.pbf',
    layers: [{ layout: { 'text-font': ['Frutiger Neue Regular'] } }],
  };

  it('is the cross product of the style fontstacks and the fixed ranges', () => {
    const urls = core.glyphURLs(style);
    expect(urls).toHaveLength(core.GLYPH_RANGES.length);
    expect(urls[0]).toBe(
      'https://vectortiles.geo.admin.ch/fonts/Frutiger%20Neue%20Regular/0-255.pbf',
    );
  });

  it('percent-encodes the fontstack, matching what MapLibre requests', () => {
    // A pinned entry keyed on an unencoded URL is one nothing ever looks
    // up, so the area reads complete and still renders unlabelled.
    for (const url of core.glyphURLs(style)) {
      expect(url).toContain('Frutiger%20Neue%20Regular');
      expect(url).not.toContain('Frutiger Neue Regular');
    }
  });

  it('covers 8192-8447, the range the 2026-09-08 staging trace caught missing', () => {
    // The regression this ticket's range set exists for: the originally
    // proposed 0-1023 set would not have fetched it.
    expect(core.GLYPH_RANGES).toContain('8192-8447');
    expect(core.glyphURLs(style)).toContain(
      'https://vectortiles.geo.admin.ch/fonts/Frutiger%20Neue%20Regular/8192-8447.pbf',
    );
  });

  it('answers [] for a style with no glyphs template', () => {
    // UNKNOWN, never "no glyphs needed" — the probe reads an empty list as
    // no claim.
    expect(core.glyphURLs({ layers: style.layers })).toEqual([]);
    expect(core.glyphURLs(null)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// SNOW-692: slope-angle raster enumeration
//
// The two guards are what distinguish this from a second `rangesToTileURLs`
// call, and both fail silently if dropped: an unclipped run fetches tiles
// the service does not serve, and every one of those failures counts
// against the run's `failed` total, so a complete download reports as
// failed. The bounds below are the live layer's own
// (slope_overlay_core.js's COVERAGE_BOUNDS).
// ---------------------------------------------------------------------------

describe('slopeTileURLs', () => {
  const TEMPLATE = 'https://wmts.geo.admin.ch/slope/{z}/{x}/{y}.png';
  const ALPS = [5.140242, 45.398181, 11.47757, 48.230651];

  it('walks the same blob rows the basemap tiles use', () => {
    // z10 x=532..533, y=363 sits over the Valais — inside the raster.
    const blob = { z: { 10: { 363: [532, 533] } } };
    expect(core.slopeTileURLs(TEMPLATE, blob, ALPS, 16)).toEqual([
      'https://wmts.geo.admin.ch/slope/10/532/363.png',
      'https://wmts.geo.admin.ch/slope/10/533/363.png',
    ]);
  });

  it('drops tiles outside the raster rectangle rather than fetching 400s', () => {
    // x=490 at z10 is ~-7.6°W, far outside the layer's western edge; the
    // Valais tile beside it in the same row must survive.
    const blob = { z: { 10: { 363: [490, 490] } } };
    expect(core.slopeTileURLs(TEMPLATE, blob, ALPS, 16)).toEqual([]);
  });

  it('keeps a tile that straddles the edge, since it carries data inside', () => {
    // Overlap, not containment: clipping to fully-contained tiles would
    // leave an unpainted margin all round a border region.
    const blob = { z: { 8: { 90: [130, 133] } } };
    const urls = core.slopeTileURLs(TEMPLATE, blob, ALPS, 16);
    expect(urls.length).toBeGreaterThan(0);
  });

  it('skips zooms past the layer ceiling', () => {
    // The service answers HTTP 400 past z17 and its real detail stops at
    // z16. The download band tops out at z14 today, so this is headroom —
    // enforced anyway because the two ceilings move independently.
    const blob = { z: { 10: { 363: [532, 532] }, 17: { 46000: [68000, 68000] } } };
    const urls = core.slopeTileURLs(TEMPLATE, blob, ALPS, 16);
    expect(urls).toEqual(['https://wmts.geo.admin.ch/slope/10/532/363.png']);
  });

  it('answers [] without a template, a blob or any ranges', () => {
    // An environment with SLOPE_TILE_URL unset pins nothing and leaves the
    // rest of the download untouched.
    const blob = { z: { 10: { 363: [532, 532] } } };
    expect(core.slopeTileURLs('', blob, ALPS, 16)).toEqual([]);
    expect(core.slopeTileURLs(TEMPLATE, null, ALPS, 16)).toEqual([]);
    expect(core.slopeTileURLs(TEMPLATE, {}, ALPS, 16)).toEqual([]);
  });

  it('handles the clipped row-span shape as well as a rectangle', () => {
    // A region blob's rows are per-y spans (SNOW-583) and need not be
    // contiguous; `zoomRows` normalises both, and this must go through it.
    const rect = { z: { 10: [532, 533, 363, 364] } };
    const spans = { z: { 10: { 363: [532, 533], 364: [532, 533] } } };
    expect(core.slopeTileURLs(TEMPLATE, rect, ALPS, 16).sort()).toEqual(
      core.slopeTileURLs(TEMPLATE, spans, ALPS, 16).sort(),
    );
  });
});

// ---------------------------------------------------------------------------
// SNOW-692: a custom area's slope set is rebuilt from bbox + band
//
// The two area kinds describe their ground differently and the derivation
// has to read whichever is present: a region record carries the run's own
// `z` row spans, a custom area carries `bbox` + `band` and no `z` at all.
// `buildBlob` is the client-side twin that produced the custom area's tile
// set in the first place, so rebuilding through it is what makes the
// derived set equal the fetched one.
// ---------------------------------------------------------------------------

describe('slopeTileURLs from a rebuilt custom-area blob', () => {
  const TEMPLATE = 'https://wmts.geo.admin.ch/slope/{z}/{x}/{y}.png';
  const ALPS = [5.140242, 45.398181, 11.47757, 48.230651];
  // A small box over the Valais, inside the raster's rectangle.
  const BBOX = [7.2, 46.05, 7.35, 46.15];

  it('rebuilds the same tile set the download enumerated', () => {
    // The equality that matters: what a run FETCHES and what a later probe
    // DERIVES must be the same list, or an area reads incomplete forever.
    const blob = core.buildBlob(BBOX, 10, 12);
    const fetched = core.slopeTileURLs(TEMPLATE, blob, ALPS, 16);
    const derived = core.slopeTileURLs(
      TEMPLATE,
      core.buildBlob(BBOX, blob.band[0], blob.band[1]),
      ALPS,
      16,
    );
    expect(derived).toEqual(fetched);
    expect(derived.length).toBeGreaterThan(0);
  });

  it('is stable across repeated derivation, so a probe never flickers', () => {
    const once = core.slopeTileURLs(TEMPLATE, core.buildBlob(BBOX, 10, 12), ALPS, 16);
    const twice = core.slopeTileURLs(TEMPLATE, core.buildBlob(BBOX, 10, 12), ALPS, 16);
    expect(twice).toEqual(once);
  });
});

/* -------------------------------------------------------------------- *
 * SNOW-924: what an area's boundary contains.
 *
 * The contract these are all about: inside the boundary, everything;
 * outside, whatever happens to be there. Under-fetching is the only
 * defect, so every test below is checking that the selection is big
 * enough rather than that it is tight.
 * -------------------------------------------------------------------- */

describe('featureBBox', () => {
  // Moved here from map.js by SNOW-924, so its coverage moves with it. The
  // flat [w, s, e, n] is this file's convention throughout; map.js keeps a
  // one-line adapter for MapLibre's nested pair.

  it('bounds a Polygon', () => {
    const feature = {
      geometry: { type: 'Polygon', coordinates: [[[7, 46], [8, 46], [8, 47], [7, 47], [7, 46]]] },
    };
    expect(core.featureBBox(feature)).toEqual([7, 46, 8, 47]);
  });

  it('bounds a MultiPolygon across all its parts', () => {
    // The case a Polygon-only walk gets wrong: the second part is what sets
    // the eastern and northern edges.
    const feature = {
      geometry: {
        type: 'MultiPolygon',
        coordinates: [
          [[[7, 46], [7.5, 46], [7.5, 46.5], [7, 46.5], [7, 46]]],
          [[[9, 47], [10, 47], [10, 48], [9, 48], [9, 47]]],
        ],
      },
    };
    expect(core.featureBBox(feature)).toEqual([7, 46, 10, 48]);
  });

  it('says "cannot say" rather than guessing, for geometry it cannot read', () => {
    expect(core.featureBBox(null)).toBe(null);
    expect(core.featureBBox({})).toBe(null);
    expect(core.featureBBox({ geometry: { type: 'Point', coordinates: [7, 46] } })).toBe(null);
    expect(core.featureBBox({ geometry: { type: 'Polygon', coordinates: [] } })).toBe(null);
  });
});

describe('bboxesOverlap and pointInBBox', () => {
  const BOX = [7, 46, 8, 47];

  it('counts a shared edge as an overlap', () => {
    // The one behavioural difference from `intersectBBox`, which uses a
    // strict `<` because it returns the overlapping REGION and a
    // zero-area overlap is not one. Here the question is "might anything
    // of this region be in the area", and the contract answers a shared
    // edge with the cheap inclusion.
    expect(core.bboxesOverlap(BOX, [8, 46, 9, 47])).toBe(true);
    expect(core.bboxesOverlap(BOX, [6, 45, 7, 46])).toBe(true);
    expect(core.pointInBBox(7, 46, BOX)).toBe(true);
    expect(core.pointInBBox(8, 47, BOX)).toBe(true);
  });

  it('refuses a box or point genuinely outside', () => {
    expect(core.bboxesOverlap(BOX, [8.01, 46, 9, 47])).toBe(false);
    expect(core.bboxesOverlap(BOX, [7, 47.01, 8, 48])).toBe(false);
    expect(core.pointInBBox(8.01, 46.5, BOX)).toBe(false);
  });

  it('treats an unanswerable question as "no", never as an overlap', () => {
    expect(core.bboxesOverlap(BOX, null)).toBe(false);
    expect(core.bboxesOverlap(null, BOX)).toBe(false);
    expect(core.bboxesOverlap(BOX, [7, 46, 8])).toBe(false);
    expect(core.pointInBBox(NaN, 46, BOX)).toBe(false);
  });
});

describe('areaBBox', () => {
  it('takes a custom area at its stored box', () => {
    // Preferred over deriving from `z`: the stored box is the ground the
    // user framed, where the derived one is the tiles it landed on.
    expect(core.areaBBox({ bbox: [7, 46, 7.2, 46.2] })).toEqual([7, 46, 7.2, 46.2]);
  });

  it('derives a region area from its tile rows, having no stored box', () => {
    // SNOW-583 replaced a region record's bbox with `z`. The derived box
    // must contain the tiles, which is what makes it safe to select
    // against — checked here by round-tripping a known bbox through
    // buildBlob and back.
    const bbox = [7.0, 46.0, 7.2, 46.2];
    const blob = core.buildBlob(bbox, ...core.MICRO_BAND);
    const derived = core.areaBBox({ z: blob.z });

    expect(derived).not.toBe(null);
    // Tile edges bound the request, so the derived box is a superset —
    // never tighter than what was asked for on any side.
    expect(derived[0]).toBeLessThanOrEqual(bbox[0]);
    expect(derived[1]).toBeLessThanOrEqual(bbox[1]);
    expect(derived[2]).toBeGreaterThanOrEqual(bbox[2]);
    expect(derived[3]).toBeGreaterThanOrEqual(bbox[3]);
  });

  it('reads the clipped row-span shape as well as the rectangle one', () => {
    // Both blob shapes go through `zoomRows`, so a server-clipped region
    // resolves like a locally-built rectangle.
    const rows = core.areaBBox({ z: { 14: { 5815: [8510, 8511], 5820: [8515, 8515] } } });
    const rect = core.areaBBox({ z: { 14: [8510, 8515, 5815, 5820] } });
    expect(rows).toEqual(rect);
  });

  it('has nothing to say about a record carrying neither', () => {
    expect(core.areaBBox(null)).toBe(null);
    expect(core.areaBBox({})).toBe(null);
    expect(core.areaBBox({ z: {} })).toBe(null);
  });
});

describe('areaContentPlan', () => {
  const region = (id, slug, bbox) => ({
    properties: { id, slug },
    geometry: {
      type: 'Polygon',
      coordinates: [[
        [bbox[0], bbox[1]], [bbox[2], bbox[1]],
        [bbox[2], bbox[3]], [bbox[0], bbox[3]], [bbox[0], bbox[1]],
      ]],
    },
  });
  const REGIONS = [
    region('CH-1111', 'inside', [7.0, 46.0, 7.3, 46.3]),
    region('CH-2222', 'straddles', [7.2, 46.2, 7.6, 46.6]),
    region('CH-3333', 'far-away', [9.0, 47.0, 9.5, 47.5]),
  ];
  const DAYS = ['2026-01-06'];

  it('takes every region the rectangle touches and no further', () => {
    const plan = core.areaContentPlan({
      bbox: [7.1, 46.1, 7.25, 46.25],
      regionFeatures: REGIONS,
      days: DAYS,
    });
    expect(plan.regionIds).toEqual(['CH-1111', 'CH-2222']);
  });

  it('lowercases the region id in the url', () => {
    // `bulletin_detail` is wrapped in `@lowercase_region_id` and 301s a
    // mixed-case one — a redirect the worker would cache as the entry for
    // a url nothing ever requests again.
    //
    // The box stops short of 7.2/46.2, which is CH-2222's corner — and an
    // inclusive test counts a shared corner, as the case above asserts.
    const plan = core.areaContentPlan({
      bbox: [7.05, 46.05, 7.15, 46.15],
      regionFeatures: REGIONS,
      days: DAYS,
    });
    expect(plan.bulletinUrls).toEqual(['/ch-1111/inside/2026-01-06/']);
  });

  it('takes one url per region per day', () => {
    const plan = core.areaContentPlan({
      bbox: [7.05, 46.05, 7.15, 46.15],
      regionFeatures: REGIONS,
      days: ['2026-01-06', '2026-01-07'],
    });
    expect(plan.bulletinUrls).toEqual([
      '/ch-1111/inside/2026-01-06/',
      '/ch-1111/inside/2026-01-07/',
    ]);
  });

  it('yields nothing for an area whose boundary contains no region', () => {
    const plan = core.areaContentPlan({
      bbox: [1.0, 41.0, 1.1, 41.1],
      regionFeatures: REGIONS,
      days: DAYS,
    });
    expect(plan.regionIds).toEqual([]);
    expect(plan.bulletinUrls).toEqual([]);
  });

  it('takes one UNDATED weather sheet per location inside', () => {
    // Not one per day, and that is correctness rather than thrift: `?date=`
    // selects which Weather ROW the page reads and only today's exists, so
    // a url per day would cache six "no weather was recorded here" pages
    // out of every seven. The forward days live inside that row. See
    // docs/decisions/weather-day-picker-is-a-selector-not-navigation.md.
    const weather = [
      { properties: { short_id: 'AAAAAAAAAAA' }, geometry: { coordinates: [7.15, 46.15] } },
      { properties: { short_id: 'BBBBBBBBBBB' }, geometry: { coordinates: [9.2, 47.2] } },
    ];
    const plan = core.areaContentPlan({
      bbox: [7.1, 46.1, 7.2, 46.2],
      weatherFeatures: weather,
      days: ['2026-01-06', '2026-01-07'],
      weatherDetailTemplate: '/api/weather/__SHORTID__/detail/',
    });
    expect(plan.weatherDetailUrls).toEqual(['/api/weather/AAAAAAAAAAA/detail/']);
  });

  it('yields no weather at all without a template to build one from', () => {
    const plan = core.areaContentPlan({
      bbox: [7.1, 46.1, 7.2, 46.2],
      weatherFeatures: [
        { properties: { short_id: 'AAAAAAAAAAA' }, geometry: { coordinates: [7.15, 46.15] } },
      ],
      days: DAYS,
    });
    expect(plan.weatherDetailUrls).toEqual([]);
  });
});

describe('areaContentPlan — the superset invariant (SNOW-924)', () => {
  // THE assertion this whole group exists to protect, and the one that must
  // never be relaxed.
  //
  // `areaContentPlan` selects regions by RECTANGLE overlap, not by real
  // geometry. That is a deliberate over-selection: the contract is "inside
  // the boundary, everything; outside, whatever happens to be there", so a
  // region wrongly included costs one HTML page while one wrongly excluded
  // costs a user their bulletin. A future reader "fixing" the crude test
  // into point-in-polygon would be trading a free over-selection for that.
  //
  // Mirrors tests/regions/services/test_basemap_tiles.py's
  // `test_clip_ranges_is_a_subset_of_the_candidate_rectangle`, which makes
  // the same argument at the other end of the pipeline and also runs
  // against every real CH boundary rather than a hand-built one. The JS
  // mechanics — a generated sweep inside one `it` — follow
  // `budgetScaleForBBox`'s loops above.

  /** Every CH micro-region in the fixture, as a geojson-shaped feature. */
  const REGIONS = CH_FIXTURE
    .filter((entry) => entry.model === 'regions.microregion')
    .map((entry) => ({
      properties: { id: entry.fields.region_id, slug: entry.fields.slug },
      geometry: entry.fields.boundary,
    }));

  /**
   * Regions with at least one boundary VERTEX inside the box.
   *
   * A sound under-approximation of "really intersects": a vertex inside
   * the box proves the region does, while a region crossing the box with
   * every vertex outside it is missed. That asymmetry is the right way
   * round — everything this set contains MUST be in the rectangle
   * selection, and anything it misses only weakens the test rather than
   * making it wrong.
   */
  function regionsWithVertexInside(bbox) {
    const hit = [];
    for (const feature of REGIONS) {
      const rings = feature.geometry.coordinates.flat();
      let found = false;
      for (const ring of rings) {
        for (const [lon, lat] of ring) {
          if (lon >= bbox[0] && lon <= bbox[2] && lat >= bbox[1] && lat <= bbox[3]) {
            found = true;
            break;
          }
        }
        if (found) break;
      }
      if (found) hit.push(feature.properties.id);
    }
    return hit;
  }

  it('loaded real boundaries to test against', () => {
    // Guards the guard: a fixture that stopped parsing would make every
    // assertion below vacuously true.
    expect(REGIONS.length).toBe(149);
    expect(REGIONS.every((f) => f.geometry && f.geometry.coordinates.length > 0)).toBe(true);
  });

  it('never selects fewer regions than really intersect, anywhere over CH', () => {
    // A sweep across Switzerland at a spread of sizes — a valley-sized box
    // up to one covering several cantons — rather than one hand-picked
    // rectangle, because the failure this catches is a bbox derivation
    // that is subtly tight rather than one that is obviously wrong.
    let checked = 0;
    for (let lon = 6.0; lon <= 10.0; lon += 0.5) {
      for (let lat = 45.9; lat <= 47.6; lat += 0.4) {
        for (const size of [0.05, 0.2, 0.75]) {
          const bbox = [lon, lat, lon + size, lat + size];
          const selected = new Set(
            core.areaContentPlan({ bbox, regionFeatures: REGIONS, days: [] }).regionIds,
          );
          for (const id of regionsWithVertexInside(bbox)) {
            expect(selected.has(id)).toBe(true);
          }
          checked += 1;
        }
      }
    }
    // The sweep is worth nothing if the loop bounds ever collapse.
    expect(checked).toBeGreaterThan(100);
  });

  it('holds for the boxes an actual download produces, not just tidy ones', () => {
    // The real path: a framed bbox becomes a blob, the blob's tile rows
    // become the area's rectangle, and THAT is what selects the regions.
    // Each step can only widen the ground, so the invariant has to survive
    // the round trip — this is where a wrong Mercator inverse in
    // `bboxFromZoomRanges` would show up.
    for (const framed of [
      [7.0, 46.0, 7.2, 46.2],
      [8.5, 46.5, 8.6, 46.6],
      [9.6, 46.4, 10.1, 46.9],
    ]) {
      const blob = core.buildBlob(framed, ...core.MICRO_BAND);
      const derived = core.areaBBox({ z: blob.z });
      const selected = new Set(
        core.areaContentPlan({ bbox: derived, regionFeatures: REGIONS, days: [] }).regionIds,
      );
      // Everything the FRAMED box really touches must survive into the
      // selection made against the DERIVED one.
      for (const id of regionsWithVertexInside(framed)) {
        expect(selected.has(id)).toBe(true);
      }
    }
  });
});
