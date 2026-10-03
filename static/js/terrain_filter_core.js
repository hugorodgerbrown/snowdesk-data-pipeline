/*
 * static/js/terrain_filter_core.js — the pure half of the map's terrain
 * filter (SNOW-978).
 *
 * The filter highlights ground by aspect, slope angle and elevation. Its
 * tiles do not carry a picture: each pixel of a terrain-class tile
 * (published by the snowdesk-tiles repo, SNOW-987) carries three FACTS
 * about the ground under it, and the device turns those into colour for
 * whatever filter the reader has set. So a filter change repaints from
 * tiles already held, with no network, and the server never learns what
 * anyone is filtering for.
 *
 * THE PIXEL CONTRACT (opaque 256 px PNG, alpha always 255):
 *
 *   R, G  — height in whole metres, uint16 big-endian (R is the high byte).
 *   B     — octant << 5 | band, where
 *             octant: N=0, NE=1 … NW=7, N covering 337.5–22.5° — the same
 *                     split as apps/core/geo.py's octant_for;
 *             band:   floor(slope / 5), 0..17.
 *           B = 254 — level ground: no aspect, R/G still hold the height.
 *           B = 255 — no data, R = G = 0.
 *
 * A tile with no data at all, or a zoom outside 12–14, answers HTTP 204.
 *
 * THE CLASSIFIER RETURNS A PALETTE INDEX, NOT A BOOLEAN, and it is handed
 * the tile and the pixel's coordinates as well as the decoded cell. Nothing
 * in this ticket needs either, but SNOW-979 plugs a four-state palette and
 * per-region clipping into exactly this seam, and a boolean predicate would
 * have to be torn out to do it. The palette here has three entries:
 *
 *   0 — transparent: the ground does not match (and level ground never does);
 *   1 — the match tint, see-through so contours and hillshade still read;
 *   2 — the no-data hatch, drawn from pixel coordinates so blank never has
 *       to mean "no data".
 *
 * Lives outside `map.js` for the usual reason: `map.js` is one file of IIFEs
 * that jsdom cannot import, and an off-by-one in an octant or band boundary
 * would paint the wrong slopes without any error anywhere.
 *
 * Every function here is pure.
 *
 * Exports (frozen `self.pwaTerrainFilterCore`):
 *
 *   OCTANTS / MIN_ZOOM / MAX_ZOOM / TILE_SIZE / COVERAGE_BOUNDS
 *   SLOPE_LADDER / ELEVATION_STEP_M / ELEVATION_MAX_M / B_LEVEL / B_NODATA
 *   PALETTE_INDEX / PALETTE / HATCH_PERIOD / HATCH_WIDTH / PROTOCOL
 *   DEFAULT_FILTER
 *   decodeCell(r, g, b, out?)
 *   createFilterClassifier(filter)  → (cell, px, py, tile) => paletteIndex
 *   paintTile(src, out, classify, palette, tile)
 *   noDataTile()
 *   coversPoint(lng, lat) / tileInCoverage(z, x, y)
 *   availability({zoom, lng, lat})  → 'ok' | 'zoom-in' | 'out-of-coverage'
 *   normaliseFilter(raw) / parseFilter(text) / serialiseFilter(filter)
 *   filterHash(filter)
 *   summarise(filter, strings, options?)
 *   tileUrl(template, z, x, y) / protocolTileUrl(hash) / parseProtocolUrl(url)
 *   createTileCache(limit)
 */

// @ts-check

(function () {
  'use strict';

  /**
   * @typedef {object} TerrainFilter
   * @property {ReadonlyArray<number>} aspects Octants that match, 0 (N) … 7 (NW), sorted.
   * @property {number} minSlope Lowest matching slope, a SLOPE_LADDER value.
   * @property {number|null} maxSlope Exclusive upper slope, or null for none.
   * @property {number|null} minElevation Lowest matching height (m), or null.
   * @property {number|null} maxElevation Highest matching height (m), or null.
   */

  /**
   * @typedef {object} Cell
   * @property {'data'|'level'|'nodata'} kind What the pixel says.
   * @property {number} height Height in whole metres (0 for no data).
   * @property {number} octant 0 (N) … 7 (NW); -1 when there is no aspect.
   * @property {number} band floor(slope / 5); -1 when there is no aspect.
   */

  /**
   * @typedef {object} TileCoord
   * @property {number} z Zoom.
   * @property {number} x Column.
   * @property {number} y Row.
   */

  /**
   * @typedef {object} PaletteEntry
   * @property {string} id Name of the state.
   * @property {ReadonlyArray<number>} rgba Straight (unpremultiplied) RGBA, 0–255.
   * @property {string} [token] The `@theme` token the legend paints with.
   * @property {string} [hex] That token's value, for a drift test.
   * @property {boolean} [hatch] Paint only on the hatch's stroke pixels.
   */

  /** @typedef {(cell: Cell, px: number, py: number, tile: TileCoord) => number} Classifier */

  /** The eight octants, by index — the order SNOW-1063's aspect wheel uses. */
  const OCTANTS = Object.freeze(['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']);

  /**
   * The zoom range the tiles exist at. The origin answers 204 outside it;
   * MapLibre overzooms the z14 tile above, which is exact for a nearest-
   * neighbour raster of facts.
   */
  const MIN_ZOOM = 12;
  const MAX_ZOOM = 14;
  const TILE_SIZE = 256;

  /**
   * The tileset's coverage, `[west, south, east, north]` — the bbox the
   * snowdesk-tiles build lists swissALTI3D squares over, which its cutter
   * writes unchanged as `terrain-class/v1/tiles.json` `bounds`. It keeps
   * MapLibre from requesting tiles outside the survey and drives the sheet's
   * "outside coverage" line.
   */
  const COVERAGE_BOUNDS = Object.freeze([5.9503666, 45.7213375, 10.4998461, 47.8216742]);

  /** The slope thresholds a reader can pick, in degrees. */
  const SLOPE_LADDER = Object.freeze([30, 35, 40, 45, 50]);

  /** Width of one slope band, and the highest band the contract allows. */
  const BAND_DEG = 5;
  const MAX_BAND = 17;

  /** The elevation band's step and ceiling, in metres. */
  const ELEVATION_STEP_M = 100;
  const ELEVATION_MAX_M = 4800;

  /** The two reserved blue-channel values. */
  const B_LEVEL = 254;
  const B_NODATA = 255;

  const PALETTE_INDEX = Object.freeze({ NONE: 0, MATCH: 1, NODATA: 2 });

  /**
   * The no-data hatch: a 45° stroke where `(px + py) % PERIOD < WIDTH`.
   * PERIOD divides TILE_SIZE, so the strokes run on across tile seams.
   */
  const HATCH_PERIOD = 8;
  const HATCH_WIDTH = 2;

  /**
   * Parse `#rrggbb` into three channels.
   *
   * @param {string} hex A six-digit hex colour.
   * @returns {number[]} `[r, g, b]`.
   */
  function hexChannels(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  const MATCH_HEX = '#2563eb';
  const NODATA_HEX = '#475569';

  /**
   * What each palette index paints. The match tint is about 45% opaque so
   * the basemap's contours and hillshade read through it; the hatch's
   * strokes are stronger because they cover only a quarter of the area.
   * `token`/`hex` tie each colour to `src/css/main.css`, which the legend
   * and the sheet's key paint from — a Vitest test asserts they agree.
   *
   * @type {ReadonlyArray<PaletteEntry>}
   */
  const PALETTE = Object.freeze([
    Object.freeze({ id: 'none', rgba: Object.freeze([0, 0, 0, 0]) }),
    Object.freeze({
      id: 'match',
      token: '--color-terrain-match',
      hex: MATCH_HEX,
      rgba: Object.freeze([...hexChannels(MATCH_HEX), 115]),
    }),
    Object.freeze({
      id: 'nodata',
      token: '--color-terrain-nodata',
      hex: NODATA_HEX,
      rgba: Object.freeze([...hexChannels(NODATA_HEX), 170]),
      hatch: true,
    }),
  ]);

  /** @type {Readonly<TerrainFilter>} */
  const DEFAULT_FILTER = Object.freeze({
    aspects: Object.freeze([0, 1, 2, 3, 4, 5, 6, 7]),
    minSlope: 30,
    maxSlope: null,
    minElevation: null,
    maxElevation: null,
  });

  /** The custom protocol MapLibre fetches painted tiles through. */
  const PROTOCOL = 'terrainfilter';

  /**
   * Decode one pixel into the facts it carries.
   *
   * @param {number} r Red: the height's high byte.
   * @param {number} g Green: the height's low byte.
   * @param {number} b Blue: octant << 5 | band, or 254 / 255.
   * @param {Cell} [out] An object to write into, so a tile's 65,536 pixels
   *   need not allocate 65,536 objects.
   * @returns {Cell} The cell.
   */
  function decodeCell(r, g, b, out) {
    const cell = out || { kind: 'nodata', height: 0, octant: -1, band: -1 };
    if (b === B_NODATA) {
      cell.kind = 'nodata';
      cell.height = 0;
      cell.octant = -1;
      cell.band = -1;
      return cell;
    }
    cell.height = r * 256 + g;
    if (b === B_LEVEL) {
      cell.kind = 'level';
      cell.octant = -1;
      cell.band = -1;
      return cell;
    }
    cell.kind = 'data';
    cell.octant = b >> 5;
    cell.band = b & 31;
    return cell;
  }

  /**
   * Snap a height to the elevation step, inside the allowed range.
   *
   * @param {*} value A candidate height.
   * @returns {number|null} The snapped height, or null for anything unusable.
   */
  function normaliseElevation(value) {
    if (value === null || value === undefined || value === '') return null;
    const n = Number(value);
    if (!Number.isFinite(n)) return null;
    const snapped = Math.round(n / ELEVATION_STEP_M) * ELEVATION_STEP_M;
    if (snapped < 0 || snapped > ELEVATION_MAX_M) return null;
    return snapped;
  }

  /**
   * Validate a filter, falling back field by field to the defaults.
   *
   * An empty aspect list is VALID — it is what a reader gets by switching
   * every aspect off, and it matches nothing. A maximum slope at or below
   * the minimum, or a maximum height at or below the minimum, is dropped.
   *
   * @param {*} raw Anything — usually a parsed JSON blob.
   * @returns {TerrainFilter} A filter safe to classify with.
   */
  function normaliseFilter(raw) {
    const src = raw && typeof raw === 'object' ? raw : {};
    let aspects = [...DEFAULT_FILTER.aspects];
    if (Array.isArray(src.aspects)) {
      const valid = src.aspects.filter(
        (/** @type {*} */ a) => Number.isInteger(a) && a >= 0 && a < OCTANTS.length,
      );
      if (valid.length === src.aspects.length) {
        aspects = [...new Set(/** @type {number[]} */ (valid))].sort((a, b) => a - b);
      }
    }
    const minSlope = SLOPE_LADDER.includes(src.minSlope) ? src.minSlope : DEFAULT_FILTER.minSlope;
    const maxSlope = SLOPE_LADDER.includes(src.maxSlope) && src.maxSlope > minSlope
      ? src.maxSlope
      : null;
    const minElevation = normaliseElevation(src.minElevation);
    let maxElevation = normaliseElevation(src.maxElevation);
    if (minElevation !== null && maxElevation !== null && maxElevation <= minElevation) {
      maxElevation = null;
    }
    return { aspects, minSlope, maxSlope, minElevation, maxElevation };
  }

  /**
   * Read a stored filter. Anything unparseable yields the defaults.
   *
   * @param {string|null|undefined} text The stored JSON.
   * @returns {TerrainFilter} The filter.
   */
  function parseFilter(text) {
    if (typeof text !== 'string' || !text) return normaliseFilter(null);
    try {
      return normaliseFilter(JSON.parse(text));
    } catch {
      return normaliseFilter(null);
    }
  }

  /**
   * Serialise a filter for storage, in a fixed key order.
   *
   * @param {*} filter The filter.
   * @returns {string} JSON.
   */
  function serialiseFilter(filter) {
    const f = normaliseFilter(filter);
    return JSON.stringify({
      aspects: f.aspects,
      minSlope: f.minSlope,
      maxSlope: f.maxSlope,
      minElevation: f.minElevation,
      maxElevation: f.maxElevation,
    });
  }

  /**
   * A short, URL-safe identity for a filter — the `v` that makes MapLibre
   * treat a repainted tile as a new one.
   *
   * @param {*} filter The filter.
   * @returns {string} e.g. `ff-30--2400-`.
   */
  function filterHash(filter) {
    const f = normaliseFilter(filter);
    const mask = f.aspects.reduce((acc, a) => acc | (1 << a), 0);
    const part = (/** @type {number|null} */ v) => (v === null ? '' : String(v));
    return [
      mask.toString(16).padStart(2, '0'),
      f.minSlope,
      part(f.maxSlope),
      part(f.minElevation),
      part(f.maxElevation),
    ].join('-');
  }

  /**
   * Build the per-pixel classifier for one filter.
   *
   * The filter is resolved ONCE here — an aspect bitmask and a band range —
   * so the per-pixel function is a handful of integer comparisons.
   *
   * Slope bands are 5° wide and every ladder value is a multiple of 5, so
   * "at least 35°" is exactly "band ≥ 7" and "under 45°" exactly "band < 9".
   *
   * @param {*} filter The filter (normalised here).
   * @returns {Classifier} `(cell, px, py, tile) => paletteIndex`.
   */
  function createFilterClassifier(filter) {
    const f = normaliseFilter(filter);
    const mask = f.aspects.reduce((acc, a) => acc | (1 << a), 0);
    const loBand = f.minSlope / BAND_DEG;
    const hiBand = f.maxSlope === null ? MAX_BAND : f.maxSlope / BAND_DEG - 1;
    const loHeight = f.minElevation === null ? -Infinity : f.minElevation;
    const hiHeight = f.maxElevation === null ? Infinity : f.maxElevation;
    return function classify(cell, _px, _py, _tile) {
      if (cell.kind === 'nodata') return PALETTE_INDEX.NODATA;
      if (cell.kind === 'level') return PALETTE_INDEX.NONE;
      if ((mask & (1 << cell.octant)) === 0) return PALETTE_INDEX.NONE;
      if (cell.band < loBand || cell.band > hiBand) return PALETTE_INDEX.NONE;
      if (cell.height < loHeight || cell.height > hiHeight) return PALETTE_INDEX.NONE;
      return PALETTE_INDEX.MATCH;
    };
  }

  /**
   * Whether a pixel lies on one of the hatch's strokes.
   *
   * @param {number} px Column within the tile.
   * @param {number} py Row within the tile.
   * @returns {boolean}
   */
  function onHatch(px, py) {
    return (px + py) % HATCH_PERIOD < HATCH_WIDTH;
  }

  /**
   * Paint a decoded class tile into RGBA for one classifier.
   *
   * @param {Uint8ClampedArray} src The class tile's RGBA pixels.
   * @param {Uint8ClampedArray} out Where to write; same length as `src`.
   * @param {Classifier} classify From createFilterClassifier (or SNOW-979's).
   * @param {ReadonlyArray<PaletteEntry>} palette Index → colour.
   * @param {TileCoord} tile The tile being painted.
   * @returns {Uint8ClampedArray} `out`.
   */
  function paintTile(src, out, classify, palette, tile) {
    const size = Math.round(Math.sqrt(src.length / 4));
    /** @type {Cell} */
    const cell = { kind: 'nodata', height: 0, octant: -1, band: -1 };
    for (let py = 0; py < size; py += 1) {
      for (let px = 0; px < size; px += 1) {
        const i = (py * size + px) * 4;
        decodeCell(src[i], src[i + 1], src[i + 2], cell);
        const entry = palette[classify(cell, px, py, tile)] || palette[0];
        const rgba = entry.hatch && !onHatch(px, py) ? palette[0].rgba : entry.rgba;
        out[i] = rgba[0];
        out[i + 1] = rgba[1];
        out[i + 2] = rgba[2];
        out[i + 3] = rgba[3];
      }
    }
    return out;
  }

  /**
   * A class tile with no data anywhere — what a 204 inside coverage stands
   * for, so it goes through the same classifier as any other tile.
   *
   * @returns {Uint8ClampedArray} TILE_SIZE² RGBA pixels, every B = 255.
   */
  function noDataTile() {
    const data = new Uint8ClampedArray(TILE_SIZE * TILE_SIZE * 4);
    for (let i = 0; i < data.length; i += 4) {
      data[i + 2] = B_NODATA;
      data[i + 3] = 255;
    }
    return data;
  }

  /**
   * Whether a point falls inside the tileset's coverage, edges included.
   *
   * @param {number} lng Longitude in degrees.
   * @param {number} lat Latitude in degrees.
   * @returns {boolean}
   */
  function coversPoint(lng, lat) {
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return false;
    const [west, south, east, north] = COVERAGE_BOUNDS;
    return lng >= west && lng <= east && lat >= south && lat <= north;
  }

  /**
   * Whether a tile's centre falls inside the coverage — the test for
   * whether a 204 means "no data here" (hatched) or "outside the survey".
   *
   * @param {number} z Zoom.
   * @param {number} x Column.
   * @param {number} y Row.
   * @returns {boolean}
   */
  function tileInCoverage(z, x, y) {
    const n = 2 ** z;
    const lng = ((x + 0.5) / n) * 360 - 180;
    const lat = (Math.atan(Math.sinh(Math.PI * (1 - (2 * (y + 0.5)) / n))) * 180) / Math.PI;
    return coversPoint(lng, lat);
  }

  /**
   * Whether the filter can show anything where the map is looking.
   *
   * Coverage is asked first: zooming in does not help outside the survey.
   *
   * @param {{zoom: number, lng: number, lat: number}} view The camera.
   * @returns {'ok'|'zoom-in'|'out-of-coverage'}
   */
  function availability(view) {
    if (!coversPoint(view.lng, view.lat)) return 'out-of-coverage';
    if (!(view.zoom >= MIN_ZOOM)) return 'zoom-in';
    return 'ok';
  }

  /**
   * Substitute `%(name)s` placeholders.
   *
   * @param {string} template The string.
   * @param {Object<string, string|number>} values Replacements.
   * @returns {string}
   */
  function fill(template, values) {
    return String(template).replace(/%\((\w+)\)s/g, (_m, key) =>
      key in values ? String(values[key]) : '');
  }

  /**
   * The filter in words, for the map chip (long) or the menu row (short).
   *
   * Long: "N, NE, NW · 35°+ · above 2,400 m". Short: "3 aspects · 35°+".
   *
   * @param {*} filter The filter.
   * @param {Object<string, string>} strings The sheet's strings — see
   *   terrain_filter_sheet.js for the keys and their English fallbacks.
   * @param {{short?: boolean, formatNumber?: (n: number) => string}} [options]
   * @returns {string}
   */
  function summarise(filter, strings, options) {
    const f = normaliseFilter(filter);
    const opts = options || {};
    const num = opts.formatNumber || ((/** @type {number} */ n) => n.toLocaleString('en-GB'));
    const parts = [];
    if (f.aspects.length === OCTANTS.length) {
      parts.push(strings['aspects-all']);
    } else if (f.aspects.length === 0) {
      parts.push(strings['aspects-none']);
    } else if (opts.short) {
      parts.push(f.aspects.length === 1
        ? strings['aspects-one']
        : fill(strings['aspects-count'], { count: f.aspects.length }));
    } else {
      parts.push(f.aspects.map((a) => strings[`compass-${a}`] || OCTANTS[a]).join(', '));
    }
    parts.push(f.maxSlope === null
      ? fill(strings['slope-min'], { min: f.minSlope })
      : fill(strings['slope-range'], { min: f.minSlope, max: f.maxSlope }));
    if (!opts.short) {
      const lo = f.minElevation;
      const hi = f.maxElevation;
      if (lo !== null && hi !== null) {
        parts.push(fill(strings['elevation-range'], { min: num(lo), max: num(hi) }));
      } else if (lo !== null) {
        parts.push(fill(strings['elevation-above'], { min: num(lo) }));
      } else if (hi !== null) {
        parts.push(fill(strings['elevation-below'], { max: num(hi) }));
      }
    }
    // A middle dot between parts — a glyph, not a word, so not a string.
    return parts.join(' · ');
  }

  /**
   * Fill an XYZ template.
   *
   * @param {string} template e.g. `https://…/{z}/{x}/{y}.png`.
   * @param {number} z Zoom.
   * @param {number} x Column.
   * @param {number} y Row.
   * @returns {string}
   */
  function tileUrl(template, z, x, y) {
    return template
      .replace('{z}', String(z))
      .replace('{x}', String(x))
      .replace('{y}', String(y));
  }

  /**
   * The template MapLibre's source requests painted tiles through. The
   * hash changes with the filter, so a repaint is a new URL and never a
   * stale tile from MapLibre's own cache.
   *
   * @param {string} hash From filterHash().
   * @returns {string}
   */
  function protocolTileUrl(hash) {
    return `${PROTOCOL}://{z}/{x}/{y}?v=${encodeURIComponent(hash)}`;
  }

  /**
   * Read the tile coordinates back out of a protocol URL.
   *
   * @param {string} url e.g. `terrainfilter://13/4262/2905?v=ff-30----`.
   * @returns {TileCoord|null}
   */
  function parseProtocolUrl(url) {
    const m = /^terrainfilter:\/\/(\d+)\/(\d+)\/(\d+)(?:[?#]|$)/.exec(String(url));
    if (!m) return null;
    return { z: Number(m[1]), x: Number(m[2]), y: Number(m[3]) };
  }

  /**
   * A small least-recently-used map, for the decoded class tiles a filter
   * change repaints from.
   *
   * @param {number} limit Most entries kept.
   * @returns {{get: (key: string) => *, set: (key: string, value: *) => void, size: () => number}}
   */
  function createTileCache(limit) {
    /** @type {Map<string, *>} */
    const entries = new Map();
    return {
      get(key) {
        if (!entries.has(key)) return undefined;
        const value = entries.get(key);
        entries.delete(key);
        entries.set(key, value);
        return value;
      },
      set(key, value) {
        entries.delete(key);
        entries.set(key, value);
        while (entries.size > limit) {
          const oldest = entries.keys().next().value;
          if (oldest === undefined) break;
          entries.delete(oldest);
        }
      },
      size() {
        return entries.size;
      },
    };
  }

  self.pwaTerrainFilterCore = Object.freeze({
    OCTANTS: OCTANTS,
    MIN_ZOOM: MIN_ZOOM,
    MAX_ZOOM: MAX_ZOOM,
    TILE_SIZE: TILE_SIZE,
    COVERAGE_BOUNDS: COVERAGE_BOUNDS,
    SLOPE_LADDER: SLOPE_LADDER,
    ELEVATION_STEP_M: ELEVATION_STEP_M,
    ELEVATION_MAX_M: ELEVATION_MAX_M,
    B_LEVEL: B_LEVEL,
    B_NODATA: B_NODATA,
    PALETTE_INDEX: PALETTE_INDEX,
    PALETTE: PALETTE,
    HATCH_PERIOD: HATCH_PERIOD,
    HATCH_WIDTH: HATCH_WIDTH,
    PROTOCOL: PROTOCOL,
    DEFAULT_FILTER: DEFAULT_FILTER,
    decodeCell: decodeCell,
    createFilterClassifier: createFilterClassifier,
    paintTile: paintTile,
    noDataTile: noDataTile,
    coversPoint: coversPoint,
    tileInCoverage: tileInCoverage,
    availability: availability,
    normaliseFilter: normaliseFilter,
    parseFilter: parseFilter,
    serialiseFilter: serialiseFilter,
    filterHash: filterHash,
    summarise: summarise,
    tileUrl: tileUrl,
    protocolTileUrl: protocolTileUrl,
    parseProtocolUrl: parseProtocolUrl,
    createTileCache: createTileCache,
  });
})();
