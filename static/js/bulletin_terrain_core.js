/*
 * static/js/bulletin_terrain_core.js — the pure half of the map's
 * problem-terrain layer (SNOW-979, SNOW-1194).
 *
 * "Highlight problem terrain" — the layers menu's row under Conditions, and
 * the region sheet's switch of the same name — paints every region's
 * steep ground by what that region's bulletin says about it. It is the
 * terrain filter (SNOW-978) with its settings taken from the bulletins
 * rather than from the reader: the same terrain-class tiles, the same
 * `paintTile`, a different classifier and palette. The server sends the
 * day's rules for every region (/api/problem-terrain/, grouped by
 * bulletin); this turns them into a per-pixel answer.
 *
 * FOUR STATES, and blank is never one of them for steep ground:
 *
 *   DESCRIBED   — ground of 30° or more inside a problem's aspects and
 *                 heights, painted the EAWS colour of the highest-rated
 *                 problem that admits it;
 *   UNDECIDED   — ground a problem describes but the pixel test cannot
 *                 place (a treeline band, a prose-only trait), a flat grey;
 *   UNDESCRIBED — steep ground no problem describes, a warm-grey hatch;
 *   NODATA      — no terrain data, the terrain filter's own slate hatch.
 *
 * A bulletin's aspects and heights mark its core zone, not a boundary, so
 * UNDESCRIBED is not "safe": it is painted precisely so it cannot be read
 * as blank. Ground under 30° and level ground are left transparent, and so
 * is everything outside the region. See
 * docs/decisions/the-bulletin-terrain-layer-paints-what-it-cannot-decide.md.
 *
 * CLIPPING. `regionMask` rasterises one region's boundary into a tile's
 * pixel grid (even-odd scanline fill, sampled at pixel centres, in Web
 * Mercator). A tile can straddle several regions, each with its own rules,
 * so `regionIndex` stacks their masks into one grid of region slots and
 * `indexClassifier` sends each pixel to its own region's classifier — a
 * pixel in no region is NONE. Pure arithmetic rather than a canvas Path2D,
 * so the clipping is tested in jsdom like everything else here. `ringsBBox`
 * and `tileBBox` let the caller skip, cheaply, every region a tile cannot
 * touch.
 *
 * Reads `self.pwaTerrainFilterCore` at parse time, so terrain_filter_core.js
 * must load first.
 *
 * Every function here is pure.
 *
 * Exports (frozen `self.pwaBulletinTerrainCore`):
 *
 *   PROTOCOL / STEEP_BAND / STATE / PALETTE / FILL_ALPHA
 *   normaliseRules(raw)          → Rule[]
 *   normaliseDay(raw)            → Day (the /api/problem-terrain/ payload)
 *   createClassifier(rules)      → (cell) => PALETTE index
 *   ringsOf(geometry)            → lng/lat rings of a Polygon / MultiPolygon
 *   ringsBBox(rings) / tileBBox(tile) / bboxesOverlap(a, b)
 *   regionMask(rings, tile, size?) → Uint8Array | null (null: tile outside)
 *   regionIndex(masks)           → Uint8Array | null, slot k+1 = masks[k]
 *   indexClassifier(classifiers, index)
 *   dayHash(day)
 *   protocolTileUrl(hash) / parseProtocolUrl(url)
 */

// @ts-check

(function () {
  'use strict';

  const TERRAIN = self.pwaTerrainFilterCore;

  /**
   * @typedef {object} Rule
   * @property {number} rating EAWS level, 1–5.
   * @property {number} mask Aspect bitmask, bit n for octant n; 255 = every aspect.
   * @property {number} lower Lowest matching height (m), -Infinity for none.
   * @property {number} upper Height a pixel must be under (m), Infinity for none.
   * @property {boolean} decided False when the pixel test cannot place it.
   */

  /**
   * @typedef {object} Cell
   * @property {'data'|'level'|'nodata'} kind
   * @property {number} height
   * @property {number} octant
   * @property {number} band
   */

  /**
   * @typedef {object} TileCoord
   * @property {number} z
   * @property {number} x
   * @property {number} y
   */

  /** @typedef {ReadonlyArray<ReadonlyArray<ReadonlyArray<number>>>} Rings */

  /** @typedef {[number, number, number, number]} BBox west, south, east, north */

  /**
   * @typedef {object} Day
   * @property {string} date The day, YYYY-MM-DD, or '' when unknown.
   * @property {Map<string, Rule[]>} bulletins Rules by bulletin id.
   * @property {Map<string, string>} regions Bulletin id by region id.
   */

  /** The custom protocol MapLibre fetches painted tiles through. */
  const PROTOCOL = 'bulletinterrain';

  /** Only ground at 30° or more is painted: band 6 is 30–35°. */
  const STEEP_BAND = 6;

  const ALL_ASPECTS = 0xff;

  /**
   * Palette indices. A described pixel is `DESCRIBED_BASE + rating`, so
   * a considerable (3) problem paints index 6.
   */
  const STATE = Object.freeze({
    NONE: 0,
    NODATA: 1,
    UNDESCRIBED: 2,
    UNDECIDED: 3,
    DESCRIBED_BASE: 3,
  });

  /**
   * How opaque the described colours are: about two-thirds, so the
   * hillshade still shapes the ground under them (mockup option 1, Hugo,
   * 22 Oct).
   */
  const FILL_ALPHA = 173;

  /**
   * Parse `#rrggbb` into three channels.
   *
   * @param {string} hex
   * @returns {number[]}
   */
  function hexChannels(hex) {
    const n = parseInt(hex.slice(1), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }

  /**
   * One palette entry from a token, its value and an alpha.
   *
   * @param {string} id
   * @param {string} token
   * @param {string} hex
   * @param {number} alpha
   * @param {object} [extra]
   * @returns {Readonly<{id: string, token: string, hex: string, rgba: ReadonlyArray<number>}>}
   */
  function entry(id, token, hex, alpha, extra) {
    return Object.freeze({
      id,
      token,
      hex,
      rgba: Object.freeze([...hexChannels(hex), alpha]),
      ...(extra || {}),
    });
  }

  /**
   * What each state paints. The EAWS colours are the standard's own
   * (src/css/main.css --color-eaws-*); the two greys are this layer's,
   * `--color-bulletin-terrain-*`. A Vitest test holds every hex equal to
   * its token, as terrain_filter_core.js's does.
   */
  const PALETTE = Object.freeze([
    TERRAIN.PALETTE[TERRAIN.PALETTE_INDEX.NONE],
    TERRAIN.PALETTE[TERRAIN.PALETTE_INDEX.NODATA],
    // Strokes the other way from the no-data hatch, and warm rather than
    // slate, so the two hatches are never mistaken for each other.
    entry('undescribed', '--color-bulletin-terrain-undescribed', '#57534e', 150, { hatch: true, hatchBack: true }),
    entry('undecided', '--color-bulletin-terrain-undecided', '#78716c', 110),
    entry('low', '--color-eaws-low', '#ccff66', FILL_ALPHA),
    entry('moderate', '--color-eaws-moderate', '#ffff00', FILL_ALPHA),
    entry('considerable', '--color-eaws-considerable', '#ff9900', FILL_ALPHA),
    entry('high', '--color-eaws-high', '#ff0000', FILL_ALPHA),
    entry('very_high', '--color-eaws-very-high', '#820100', FILL_ALPHA),
  ]);

  /**
   * A height bound from the wire, or the open end when there is none.
   *
   * @param {*} value
   * @param {number} open -Infinity or Infinity.
   * @returns {number}
   */
  function bound(value, open) {
    if (value === null || value === undefined || value === '') return open;
    const n = Number(value);
    return Number.isFinite(n) ? n : open;
  }

  /**
   * Read the server's rules, dropping anything unusable.
   *
   * @param {*} raw The parsed `json_script` — a list of
   *   `{rating, aspects, lower, upper, decided}`.
   * @returns {Rule[]}
   */
  function normaliseRules(raw) {
    if (!Array.isArray(raw)) return [];
    /** @type {Rule[]} */
    const rules = [];
    for (const r of raw) {
      if (!r || typeof r !== 'object') continue;
      const rating = Number(r.rating);
      if (!Number.isInteger(rating) || rating < 1 || rating > 5) continue;
      const aspects = Array.isArray(r.aspects)
        ? r.aspects.filter((/** @type {*} */ a) => Number.isInteger(a) && a >= 0 && a <= 7)
        : [];
      rules.push({
        rating,
        mask: aspects.length
          ? aspects.reduce((/** @type {number} */ acc, /** @type {number} */ a) => acc | (1 << a), 0)
          : ALL_ASPECTS,
        lower: bound(r.lower, -Infinity),
        upper: bound(r.upper, Infinity),
        decided: r.decided !== false,
      });
    }
    return rules;
  }

  /**
   * Read the day's payload: every region's bulletin, every bulletin's rules.
   *
   * A region naming a bulletin the payload does not carry is dropped, so
   * every region in `regions` has rules to paint (possibly none, which
   * paints its steep ground as undescribed).
   *
   * @param {*} raw The parsed /api/problem-terrain/ response.
   * @returns {Day}
   */
  function normaliseDay(raw) {
    const src = raw && typeof raw === 'object' ? raw : {};
    /** @type {Map<string, Rule[]>} */
    const bulletins = new Map();
    const rawBulletins = src.bulletins && typeof src.bulletins === 'object' ? src.bulletins : {};
    for (const [id, rules] of Object.entries(rawBulletins)) {
      bulletins.set(id, normaliseRules(rules));
    }
    /** @type {Map<string, string>} */
    const regions = new Map();
    const rawRegions = src.regions && typeof src.regions === 'object' ? src.regions : {};
    for (const [regionId, id] of Object.entries(rawRegions)) {
      if (typeof id === 'string' && bulletins.has(id)) regions.set(regionId, id);
    }
    return { date: typeof src.date === 'string' ? src.date : '', bulletins, regions };
  }

  /**
   * Whether a rule admits a pixel of data.
   *
   * @param {Rule} rule
   * @param {Cell} cell A `data` cell.
   * @returns {boolean}
   */
  function admits(rule, cell) {
    return (rule.mask & (1 << cell.octant)) !== 0
      && cell.height >= rule.lower
      && cell.height < rule.upper;
  }

  /**
   * Build the per-pixel classifier for one region's rules.
   *
   * Precedence: no data, then not steep (transparent), then the highest
   * decided rating that admits the pixel, then UNDECIDED if an undecided
   * rule admits it on what it does state, else UNDESCRIBED.
   *
   * @param {ReadonlyArray<Rule>} rules From normaliseRules.
   * @returns {(cell: Cell) => number} The PALETTE index.
   */
  function createClassifier(rules) {
    const decided = rules.filter((r) => r.decided).sort((a, b) => b.rating - a.rating);
    const undecided = rules.filter((r) => !r.decided);
    return function classify(cell) {
      if (cell.kind === 'nodata') return STATE.NODATA;
      if (cell.kind === 'level' || cell.band < STEEP_BAND) return STATE.NONE;
      for (const rule of decided) {
        if (admits(rule, cell)) return STATE.DESCRIBED_BASE + rule.rating;
      }
      for (const rule of undecided) {
        if (admits(rule, cell)) return STATE.UNDECIDED;
      }
      return STATE.UNDESCRIBED;
    };
  }

  /**
   * The outer and inner rings of a GeoJSON Polygon or MultiPolygon.
   *
   * Every ring goes into one list: the even-odd fill makes a hole a hole
   * and an island an island without knowing which is which.
   *
   * @param {*} geometry
   * @returns {number[][][]}
   */
  function ringsOf(geometry) {
    if (!geometry || !Array.isArray(geometry.coordinates)) return [];
    if (geometry.type === 'Polygon') return geometry.coordinates;
    if (geometry.type === 'MultiPolygon') return geometry.coordinates.flat();
    return [];
  }

  /**
   * The lng/lat bounding box of a region's rings.
   *
   * @param {Rings} rings
   * @returns {BBox|null} null when there are no points.
   */
  function ringsBBox(rings) {
    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;
    for (const ring of rings) {
      for (const p of ring) {
        if (p[0] < west) west = p[0];
        if (p[0] > east) east = p[0];
        if (p[1] < south) south = p[1];
        if (p[1] > north) north = p[1];
      }
    }
    return Number.isFinite(west) ? [west, south, east, north] : null;
  }

  /**
   * The lng/lat bounding box of one XYZ tile.
   *
   * @param {TileCoord} tile
   * @returns {BBox}
   */
  function tileBBox(tile) {
    const n = 2 ** tile.z;
    const lng = (/** @type {number} */ x) => (x / n) * 360 - 180;
    const lat = (/** @type {number} */ y) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI;
    return [lng(tile.x), lat(tile.y + 1), lng(tile.x + 1), lat(tile.y)];
  }

  /**
   * Whether two boxes share any ground, edges included.
   *
   * @param {BBox} a
   * @param {BBox} b
   * @returns {boolean}
   */
  function bboxesOverlap(a, b) {
    return a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3];
  }

  /**
   * Project lng/lat to pixels within one tile (Web Mercator).
   *
   * @param {number} lng
   * @param {number} lat
   * @param {TileCoord} tile
   * @param {number} size Tile edge in pixels.
   * @returns {number[]} `[px, py]`, fractional, possibly outside 0..size.
   */
  function toTilePixels(lng, lat, tile, size) {
    const world = size * 2 ** tile.z;
    const phi = (lat * Math.PI) / 180;
    const x = ((lng + 180) / 360) * world;
    const y = ((1 - Math.log(Math.tan(phi) + 1 / Math.cos(phi)) / Math.PI) / 2) * world;
    return [x - tile.x * size, y - tile.y * size];
  }

  /**
   * Rasterise a region's rings into one tile's pixel grid.
   *
   * Even-odd scanline fill, sampled at each pixel's centre. A tile the
   * rings' bounding box misses entirely is `null`, so the caller can skip
   * fetching its class tile at all.
   *
   * @param {Rings} rings lng/lat rings, from ringsOf.
   * @param {TileCoord} tile
   * @param {number} [size] Tile edge in pixels (TILE_SIZE).
   * @returns {Uint8Array|null} 1 inside, 0 outside; null when nothing is inside.
   */
  function regionMask(rings, tile, size) {
    const edge = size || TERRAIN.TILE_SIZE;
    /** @type {number[][][]} */
    const projected = [];
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;
    for (const ring of rings) {
      if (!ring || ring.length < 3) continue;
      const pts = ring.map((p) => toTilePixels(p[0], p[1], tile, edge));
      for (const [x, y] of pts) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
      projected.push(pts);
    }
    if (!projected.length || maxX < 0 || maxY < 0 || minX > edge || minY > edge) return null;

    const mask = new Uint8Array(edge * edge);
    let any = false;
    /** @type {number[]} */
    const crossings = [];
    const rowFrom = Math.max(0, Math.floor(minY));
    const rowTo = Math.min(edge - 1, Math.ceil(maxY));
    for (let py = rowFrom; py <= rowTo; py += 1) {
      const yc = py + 0.5;
      crossings.length = 0;
      for (const pts of projected) {
        for (let i = 0, j = pts.length - 1; i < pts.length; j = i, i += 1) {
          const [xi, yi] = pts[i];
          const [xj, yj] = pts[j];
          if ((yi > yc) !== (yj > yc)) {
            crossings.push(xi + ((yc - yi) * (xj - xi)) / (yj - yi));
          }
        }
      }
      crossings.sort((a, b) => a - b);
      for (let k = 0; k + 1 < crossings.length; k += 2) {
        // Pixel centres px + 0.5 in [from, to).
        const from = Math.max(0, Math.ceil(crossings[k] - 0.5));
        const to = Math.min(edge - 1, Math.ceil(crossings[k + 1] - 0.5) - 1);
        for (let px = from; px <= to; px += 1) {
          mask[py * edge + px] = 1;
          any = true;
        }
      }
    }
    return any ? mask : null;
  }

  /**
   * Stack several regions' masks into one grid of region slots.
   *
   * Slot k + 1 is `masks[k]`; 0 is no region. Neighbouring regions share
   * an edge, not ground, so where two masks both claim a pixel (a pixel
   * centre exactly on the shared boundary) the later one simply wins.
   *
   * @param {ReadonlyArray<Uint8Array|null>} masks From regionMask, in slot order.
   * @returns {Uint8Array|null} null when no mask has any pixel.
   */
  function regionIndex(masks) {
    /** @type {Uint8Array|null} */
    let index = null;
    masks.forEach((mask, k) => {
      if (!mask) return;
      if (!index) index = new Uint8Array(mask.length);
      for (let i = 0; i < mask.length; i += 1) {
        if (mask[i]) index[i] = k + 1;
      }
    });
    return index;
  }

  /**
   * Send each pixel to its own region's classifier.
   *
   * The shape terrain_filter_core.paintTile calls: `(cell, px, py, tile)`.
   *
   * @param {ReadonlyArray<(cell: Cell) => number>} classifiers Slot k + 1's is classifiers[k].
   * @param {Uint8Array} index From regionIndex, TILE_SIZE² entries.
   * @returns {(cell: Cell, px: number, py: number, tile: TileCoord) => number}
   */
  function indexClassifier(classifiers, index) {
    const size = Math.round(Math.sqrt(index.length));
    return function byRegion(cell, px, py, _tile) {
      const slot = index[py * size + px];
      return slot ? classifiers[slot - 1](cell) : STATE.NONE;
    };
  }

  /**
   * The tile URL's cache-buster: another day, or the same day re-issued
   * with different rules or regions, is a different URL, so MapLibre never
   * shows a stale paint. A short FNV-1a digest of everything that paints.
   *
   * @param {Day} day From normaliseDay.
   * @returns {string}
   */
  function dayHash(day) {
    const part = (/** @type {number} */ v) => (Number.isFinite(v) ? String(v) : '');
    const ids = [...day.bulletins.keys()].sort();
    const body = ids.map((id) => id + ':' + (day.bulletins.get(id) || [])
      .map((r) => [r.rating, r.mask.toString(16), part(r.lower), part(r.upper), r.decided ? 1 : 0].join('.'))
      .join('_')).join('|')
      + '#' + [...day.regions.entries()].sort().map((e) => e.join('=')).join(',');
    let h = 0x811c9dc5;
    for (let i = 0; i < body.length; i += 1) {
      h ^= body.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return `${day.date}~${h.toString(36)}`;
  }

  /**
   * @param {string} hash From dayHash().
   * @returns {string}
   */
  function protocolTileUrl(hash) {
    return `${PROTOCOL}://{z}/{x}/{y}?v=${encodeURIComponent(hash)}`;
  }

  /**
   * @param {string} url e.g. `bulletinterrain://13/4262/2905?v=…`.
   * @returns {TileCoord|null}
   */
  function parseProtocolUrl(url) {
    const m = /^bulletinterrain:\/\/(\d+)\/(\d+)\/(\d+)(?:[?#]|$)/.exec(String(url));
    if (!m) return null;
    return { z: Number(m[1]), x: Number(m[2]), y: Number(m[3]) };
  }

  self.pwaBulletinTerrainCore = Object.freeze({
    PROTOCOL,
    STEEP_BAND,
    STATE,
    PALETTE,
    FILL_ALPHA,
    normaliseRules,
    normaliseDay,
    createClassifier,
    ringsOf,
    ringsBBox,
    tileBBox,
    bboxesOverlap,
    regionMask,
    regionIndex,
    indexClassifier,
    dayHash,
    protocolTileUrl,
    parseProtocolUrl,
  });
})();
