// @ts-check
/*
 * static/js/beyond_switzerland_core.js — the swisstopo winter style spliced
 * to draw past the Swiss border (SNOW-1460).
 *
 * swisstopo's winter style draws only Switzerland and Liechtenstein: its
 * base.vt carries no features past the border (a z12 tile over St Anton is
 * 209 bytes, against 166 KB over Valais). Past it the style is extended
 * with three things, all from this file's plan, which
 * static/js/map_beyond_switzerland.js adds to the map on every style.load:
 *
 *   - OPENSTREETMAP, RESTYLED. OpenFreeMap's OpenMapTiles vector tiles,
 *     read through `osmclip://` (static/js/osm_clip_worker.js clips them to
 *     outside the swisstopo extent), drawn with the winter style's own layer
 *     definitions. base.vt uses the same schema, so every winter layer on a
 *     source-layer OpenMapTiles has is copied with its source swapped and
 *     placed just above the original. The rest are small class fixes:
 *       - peaks: OpenMapTiles has `peak` where swisstopo has `alpine_peak`
 *         (3000 m and up) and `main_peak` (2000 m and up);
 *       - roads: swisstopo sizes its roads by `is_route`, which OpenMapTiles
 *         lacks, so it is read from the road class instead;
 *       - lifts: OpenMapTiles files a lift as class `aerialway` with the
 *         kind in `subclass`, where swisstopo's class is the kind;
 *       - borders: OSM's own line along Switzerland's and Liechtenstein's
 *         border is left out, since swisstopo draws it;
 *       - fills: woods (OpenMapTiles' `wood`, which the winter filter does
 *         not name), glaciers (which the winter style fades out at z14,
 *         where relief.vt takes over) and bare rock get fills of their own.
 *     The winter style's own layers are left unfiltered: base.vt already
 *     stops at the border, so forests, lakes and glaciers never draw twice.
 *   - OUR RELIEF past swisstopo's detail: the `alpinerelief://` raster
 *     (static/js/alpine_relief_core.js), above swisstopo's flat edge
 *     shading (the hillshade layers) and below everything else.
 *   - SCREE. OpenMapTiles' `rock` landcover that is not `bare_rock` gets
 *     swisstopo's own scree dot pattern, from the winter sprite.
 *
 * And the 3D gate: with the map_3d flag too, 3D opens over the Alps,
 * ALPINE_GATE, which leaves out the Pyrenees and Corsica.
 *
 * Exports (frozen `self.pwaBeyondSwitzerlandCore`):
 *
 *   BASEMAP_KEY / OSM_SOURCE_ID / RELIEF_SOURCE_ID / RELIEF_LAYER_ID
 *   LAYER_PREFIX / OSM_ATTRIBUTION / RELIEF_ATTRIBUTION / OMT_LAYERS
 *   ALPINE_GATE
 *   isWinterStyle(style)
 *   isSpliced(style)
 *   osmSource() / reliefSource()
 *   fixLayer(layer)
 *   plan(style)
 */
(function () {
  'use strict';

  /** The basemap this applies to (BASEMAP_STYLES in config/settings/base.py). */
  const BASEMAP_KEY = 'swisstopo_winter';

  /** The added sources. */
  const OSM_SOURCE_ID = 'beyond-osm';
  const RELIEF_SOURCE_ID = 'beyond-relief';
  const RELIEF_LAYER_ID = 'beyond-relief';

  /** Every added layer's id starts with this. */
  const LAYER_PREFIX = 'beyond-';

  /** The credit OpenStreetMap's licence asks for, and OpenMapTiles'. */
  const OSM_ATTRIBUTION =
    '<a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">© OpenStreetMap contributors</a>'
    + ' · <a href="https://openmaptiles.org/" target="_blank" rel="noopener">© OpenMapTiles</a>';

  /**
   * The elevation's credit: the exact string map_3d_core.js's elevation
   * source carries, so the attribution's exact-string union shows it once
   * in 3D.
   */
  const RELIEF_ATTRIBUTION =
    '<a href="https://mapterhorn.com/attribution" target="_blank" rel="noopener">© Mapterhorn</a>'
    + ' · <a href="https://github.com/tilezen/joerd/blob/master/docs/attribution.md" target="_blank" rel="noopener">Terrain Tiles</a>';

  /** The source-layers OpenMapTiles has (OpenFreeMap's planet TileJSON). */
  const OMT_LAYERS = Object.freeze([
    'aerodrome_label', 'aeroway', 'boundary', 'building', 'housenumber',
    'landcover', 'landuse', 'mountain_peak', 'park', 'place', 'poi',
    'transportation', 'transportation_name', 'water', 'water_name', 'waterway',
  ]);

  /** The OSM tiles' zoom range (OpenFreeMap's planet). */
  const OSM_MIN_ZOOM = 0;
  const OSM_MAX_ZOOM = 14;

  /** The relief raster's zoom range and tile size (alpine_relief_core.js). */
  const RELIEF_MIN_ZOOM = 6;
  const RELIEF_MAX_ZOOM = 14;
  const RELIEF_TILE_SIZE = 512;

  /**
   * Where 3D opens with this flag and map_3d: the Alps, about 5.0–16.6°E
   * and 43.7–48.3°N. West to east along the south edge, back along the
   * north. Closed. The Pyrenees (west of 3.4°E) and Corsica (south of
   * 43.1°N) fall outside it.
   *
   * @type {ReadonlyArray<ReadonlyArray<number>>}
   */
  const ALPINE_GATE = Object.freeze([
    Object.freeze([5.0, 43.7]),
    Object.freeze([16.6, 43.7]),
    Object.freeze([16.6, 48.3]),
    Object.freeze([5.0, 48.3]),
    Object.freeze([5.0, 43.7]),
  ]);

  /** OpenMapTiles' peak classes, as swisstopo's by height. */
  const PEAK_CLASS = Object.freeze([
    'match', ['get', 'class'],
    ['peak', 'volcano'],
    ['case',
      ['>=', ['to-number', ['get', 'ele'], 0], 3000], 'alpine_peak',
      ['>=', ['to-number', ['get', 'ele'], 0], 2000], 'main_peak',
      'peak'],
    ['get', 'class'],
  ]);

  /** A lift's kind as its class, swisstopo's way. */
  const TRANSPORT_CLASS = Object.freeze([
    'case', ['==', ['get', 'class'], 'aerialway'], ['get', 'subclass'], ['get', 'class'],
  ]);

  /**
   * swisstopo's `is_route` from OpenMapTiles' road class: the main network
   * (99) from z9, the local roads and tracks (11) from z11, the rest (paths
   * among them) never a route, so they draw from z13.
   */
  const IS_ROUTE = Object.freeze([
    'match', ['get', 'class'],
    ['motorway', 'trunk', 'primary', 'secondary', 'tertiary'], 99,
    ['minor', 'service', 'track'], 11,
    0,
  ]);

  /** The colours the winter style fills woods and glaciers with. */
  const WOOD_COLOUR = 'rgb(57, 142, 99)';
  const GLACIER_COLOUR = 'rgb(205, 232, 244)';
  const ROCK_COLOUR = 'rgb(214, 214, 216)';

  /**
   * A deep copy of plain JSON.
   *
   * @template T
   * @param {T} value
   * @returns {T}
   */
  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  /**
   * Whether a style is swisstopo's winter style (not its light twin, which
   * reads the same base.vt).
   *
   * @param {any} style
   * @returns {boolean}
   */
  function isWinterStyle(style) {
    if (!style || !Array.isArray(style.layers)) return false;
    const sprite = typeof style.sprite === 'string' ? style.sprite : JSON.stringify(style.sprite || '');
    const name = typeof style.name === 'string' ? style.name : '';
    return sprite.includes('basemap-winter') || name.startsWith('basemap-winter');
  }

  /**
   * Whether a style already carries the splice.
   *
   * @param {any} style
   * @returns {boolean}
   */
  function isSpliced(style) {
    return !!(style && style.sources && style.sources[OSM_SOURCE_ID]);
  }

  /** The OSM vector source, through the clip protocol. */
  function osmSource() {
    return {
      type: 'vector',
      tiles: ['osmclip://{z}/{x}/{y}'],
      minzoom: OSM_MIN_ZOOM,
      maxzoom: OSM_MAX_ZOOM,
      attribution: OSM_ATTRIBUTION,
    };
  }

  /** The relief raster source, through the relief protocol. */
  function reliefSource() {
    return {
      type: 'raster',
      tiles: ['alpinerelief://{z}/{x}/{y}'],
      tileSize: RELIEF_TILE_SIZE,
      minzoom: RELIEF_MIN_ZOOM,
      maxzoom: RELIEF_MAX_ZOOM,
      attribution: RELIEF_ATTRIBUTION,
    };
  }

  /**
   * Swap every `["get", name]` (and `["has", name]`) in an expression.
   *
   * @param {any} value
   * @param {Record<string, any>} gets name → replacement for ["get", name].
   * @param {Record<string, any>} has name → replacement for ["has", name].
   * @returns {any}
   */
  function rewrite(value, gets, has) {
    if (!Array.isArray(value)) {
      if (value && typeof value === 'object') {
        /** @type {Record<string, any>} */
        const out = {};
        for (const [key, inner] of Object.entries(value)) out[key] = rewrite(inner, gets, has);
        return out;
      }
      return value;
    }
    if (value.length === 2 && typeof value[1] === 'string') {
      if (value[0] === 'get' && Object.hasOwn(gets, value[1])) return clone(gets[value[1]]);
      if (value[0] === 'has' && Object.hasOwn(has, value[1])) return clone(has[value[1]]);
    }
    return value.map((inner) => rewrite(inner, gets, has));
  }

  /**
   * Whether a filter is written as an expression rather than in the legacy
   * syntax, which names properties bare and cannot be mixed with one.
   *
   * @param {any} filter
   * @returns {boolean}
   */
  function isExpression(filter) {
    const text = JSON.stringify(filter);
    return text.includes('["get",') || text.includes('["geometry-type"]') || text.includes('["zoom"]');
  }

  /**
   * A boundary filter that leaves out Switzerland's and Liechtenstein's own
   * borders: swisstopo draws them, and OSM's, cut at a simplified extent,
   * would draw beside them in pieces.
   *
   * @param {any} filter
   * @returns {any}
   */
  function withoutSwissBorder(filter) {
    const codes = ['CHE', 'LIE'];
    if (filter && isExpression(filter)) {
      const side = (/** @type {string} */ key) =>
        ['!', ['in', ['coalesce', ['get', key], ''], ['literal', codes]]];
      return ['all', clone(filter), side('adm0_l'), side('adm0_r')];
    }
    const legacy = [['!in', 'adm0_l', ...codes], ['!in', 'adm0_r', ...codes]];
    return filter ? ['all', clone(filter), ...legacy] : ['all', ...legacy];
  }

  /**
   * A winter layer's OSM copy with OpenMapTiles' class fixes: a fresh id,
   * the OSM source, and the expressions rewritten for its source-layer.
   *
   * @param {any} layer A winter layer on base.vt.
   * @returns {any}
   */
  function fixLayer(layer) {
    const copy = clone(layer);
    copy.id = LAYER_PREFIX + layer.id;
    copy.source = OSM_SOURCE_ID;
    copy.metadata = { ...(copy.metadata || {}), 'snowdesk:beyond': layer.id };
    /** @type {Record<string, any>} */
    let gets = {};
    /** @type {Record<string, any>} */
    let has = {};
    if (layer['source-layer'] === 'mountain_peak') {
      gets = { class: PEAK_CLASS };
    } else if (layer['source-layer'] === 'transportation') {
      gets = { class: TRANSPORT_CLASS, is_route: IS_ROUTE };
      has = { is_route: ['!=', IS_ROUTE, 0] };
    } else if (layer['source-layer'] === 'boundary') {
      copy.filter = withoutSwissBorder(copy.filter);
    } else if (layer['source-layer'] === 'landcover' && Array.isArray(copy.filter)
      && copy.filter[0] === 'in' && copy.filter[1] === 'subclass') {
      // Glaciers have a fill of their own (glacierFill), which does not
      // fade out at z14.
      copy.filter = copy.filter.filter((/** @type {any} */ v) => v !== 'glacier');
    }
    for (const key of ['paint', 'layout']) {
      if (copy[key]) copy[key] = rewrite(copy[key], gets, has);
    }
    // Only an expression filter can be rewritten; a legacy one names its
    // property directly and stays as it is.
    if (Array.isArray(copy.filter) && isExpression(copy.filter)) {
      copy.filter = rewrite(copy.filter, gets, has);
    }
    return copy;
  }

  /**
   * The fills OpenMapTiles' landcover needs that the winter style does not
   * give it: woods its filter misses, glaciers past z14, bare rock.
   *
   * @returns {any[]}
   */
  function landcoverFixes() {
    const base = { type: 'fill', source: OSM_SOURCE_ID, 'source-layer': 'landcover' };
    return [
      {
        ...base,
        id: `${LAYER_PREFIX}wood`,
        minzoom: 5,
        filter: ['all', ['==', ['get', 'class'], 'wood'], ['!=', ['get', 'subclass'], 'forest']],
        paint: {
          'fill-color': WOOD_COLOUR,
          'fill-opacity': ['interpolate', ['linear'], ['zoom'], 5, 0, 10, 0.1, 14, 0.15],
        },
      },
      {
        ...base,
        id: `${LAYER_PREFIX}glacier`,
        minzoom: 5,
        filter: ['==', ['get', 'class'], 'ice'],
        paint: {
          'fill-color': GLACIER_COLOUR,
          'fill-opacity': ['interpolate', ['linear'], ['zoom'], 5, 0, 6, 0.3, 14, 0.6],
        },
      },
      {
        ...base,
        id: `${LAYER_PREFIX}rock`,
        minzoom: 9,
        filter: ['==', ['get', 'class'], 'rock'],
        paint: {
          'fill-color': ROCK_COLOUR,
          'fill-opacity': ['interpolate', ['linear'], ['zoom'], 9, 0, 11, 0.35],
        },
      },
    ];
  }

  /**
   * A winter scree layer's copy on OpenMapTiles' scree: `rock` that is not
   * `bare_rock`, in swisstopo's middle density (OSM has no `weight`).
   *
   * @param {any} layer A winter layer on relief.vt's scree.
   * @returns {any}
   */
  function screeLayer(layer) {
    const copy = clone(layer);
    copy.id = LAYER_PREFIX + layer.id;
    copy.source = OSM_SOURCE_ID;
    copy['source-layer'] = 'landcover';
    copy.filter = ['all', ['==', ['get', 'class'], 'rock'], ['!=', ['get', 'subclass'], 'bare_rock']];
    copy.metadata = { ...(copy.metadata || {}), 'snowdesk:beyond': layer.id };
    if (copy.paint) copy.paint = rewrite(copy.paint, { weight: 10 }, {});
    return copy;
  }

  /**
   * The source id the winter style reads a swisstopo tileset from.
   *
   * @param {any} style
   * @param {string} tileset e.g. `ch.swisstopo.base.vt`.
   * @returns {?string}
   */
  function sourceFor(style, tileset) {
    for (const [id, source] of Object.entries(style.sources || {})) {
      const url = /** @type {any} */ (source).url || '';
      const tiles = /** @type {any} */ (source).tiles || [];
      if (String(url).includes(tileset) || tiles.some((/** @type {string} */ t) => String(t).includes(tileset))) {
        return id;
      }
    }
    return null;
  }

  /**
   * @typedef {Object} Plan
   * @property {Record<string, any>} sources The sources to add.
   * @property {Array<{layer: any, beforeId: ?string}>} layers In the order
   *   to add them; each goes under `beforeId` (null: on top).
   */

  /**
   * What to add to a winter style to draw it past the border. Null for any
   * other style, or one already spliced, so a second call adds nothing.
   *
   * @param {any} style A style as `map.getStyle()` gives it.
   * @returns {?Plan}
   */
  function plan(style) {
    if (!isWinterStyle(style) || isSpliced(style)) return null;
    const base = sourceFor(style, 'ch.swisstopo.base.vt');
    const relief = sourceFor(style, 'ch.swisstopo.relief.vt');
    if (!base) return null;
    const omt = new Set(OMT_LAYERS);
    const layers = /** @type {any[]} */ (style.layers);
    /** @type {Array<{layer: any, beforeId: ?string}>} */
    const added = [];
    // The relief goes above the last hillshade layer: swisstopo's flat
    // edge shading under it, everything else over it.
    let reliefAfter = -1;
    layers.forEach((layer, index) => {
      if (layer.source === relief && layer['source-layer'] === 'hillshade') reliefAfter = index;
    });
    layers.forEach((layer, index) => {
      const next = layers[index + 1];
      const beforeId = next ? next.id : null;
      if (index === reliefAfter) {
        added.push({
          layer: { id: RELIEF_LAYER_ID, type: 'raster', source: RELIEF_SOURCE_ID, paint: { 'raster-fade-duration': 0 } },
          beforeId,
        });
      }
      if (layer.source === relief && layer['source-layer'] === 'scree') {
        added.push({ layer: screeLayer(layer), beforeId });
        return;
      }
      if (layer.source !== base || !omt.has(layer['source-layer'])) return;
      added.push({ layer: fixLayer(layer), beforeId });
      if (layer.id === 'landcover') {
        for (const fill of landcoverFixes()) added.push({ layer: fill, beforeId });
      }
    });
    if (reliefAfter < 0) {
      // No hillshade to sit on: the relief goes under the first layer drawn.
      added.unshift({
        layer: { id: RELIEF_LAYER_ID, type: 'raster', source: RELIEF_SOURCE_ID, paint: { 'raster-fade-duration': 0 } },
        beforeId: layers[1] ? layers[1].id : null,
      });
    }
    return {
      sources: { [OSM_SOURCE_ID]: osmSource(), [RELIEF_SOURCE_ID]: reliefSource() },
      layers: added,
    };
  }

  self.pwaBeyondSwitzerlandCore = Object.freeze({
    BASEMAP_KEY,
    OSM_SOURCE_ID,
    RELIEF_SOURCE_ID,
    RELIEF_LAYER_ID,
    LAYER_PREFIX,
    OSM_ATTRIBUTION,
    RELIEF_ATTRIBUTION,
    OMT_LAYERS,
    ALPINE_GATE,
    isWinterStyle,
    isSpliced,
    osmSource,
    reliefSource,
    fixLayer,
    plan,
  });
})();
