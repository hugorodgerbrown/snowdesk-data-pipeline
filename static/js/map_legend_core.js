/*
 * static/js/map_legend_core.js — the pure half of the map legend's first
 * tier, "On the map now".
 *
 * The (i) card used to list every key the map could ever need, on every
 * visit: the danger scale, pins, field reports, slope classes, route legs,
 * attribution. Hugo, 3 Nov 2026: "This is now getting out of hand — we
 * need to move to a 2-tier legend". The card now opens on a key for what
 * is DRAWN IN THE CURRENT VIEW, and keeps the rest one level down, behind
 * "All keys & sources" (the layers menu's own on/off-then-detail pattern).
 *
 * "Drawn in the current view" is three tests, and a key needs all three:
 * the layer is switched on (and not held by Terrain mode), the zoom draws
 * it, and something of it is inside the viewport. map_legend.js asks the
 * map; this module turns the answers into which keys and which of their
 * rows show, and touches neither the DOM nor the map.
 *
 * Exports (frozen `self.pwaMapLegendCore`):
 *
 *   KEYS                 — every first-tier key, in card order
 *   LAYERS               — the map layers whose rendered features each
 *                          view-tested key is read from
 *   legendState(view)    — { keys, items, on } for a probed view
 *   shortSources(list)   — the one-line credit for the first tier
 */

// @ts-check

(function () {
  'use strict';

  /**
   * Every first-tier key, in the order the card lists them: the dated
   * keys first (Terrain mode drops them as a block), then the ones every
   * mode can draw.
   */
  const KEYS = Object.freeze([
    'danger',
    'problem_terrain',
    'community_reports',
    'terrain_filter',
    'rest_zones',
    'routes',
    'pins',
    'downloads',
  ]);

  /**
   * The layers each view-tested key is read from. The danger key reads the
   * regions' own layers — the fill fades to nothing by z10, but the edge
   * band and the level labels carry the colour on from there.
   */
  const LAYERS = Object.freeze({
    danger: Object.freeze(['regions-fill', 'regions-edge', 'regions-level-label']),
    community_reports: Object.freeze(['community-reports-point', 'community-reports-clusters']),
    rest_zones: Object.freeze(['rest-zones-fill']),
    routes: Object.freeze(['routes-leg-climb', 'routes-leg-descent']),
    resorts: Object.freeze(['resorts-pin']),
    favourites: Object.freeze(['favourites-pin']),
  });

  /** The field-report families the key has a row for. */
  const FAMILIES = Object.freeze(['WARNING', 'SURFACE', 'TRAIL']);

  /**
   * @typedef {{properties?: Object<string, any>|null}} RenderedFeature
   *
   * @typedef {Object} LegendView
   * @property {boolean} [bulletins] The bulletins layer is drawn (on, and
   *   not held by Terrain mode or resort editing).
   * @property {boolean} [problemTerrain] Problem terrain is on, and the
   *   view is one it paints (zoom 12+, inside its coverage).
   * @property {boolean} [terrainFilter] Steep terrain is on, and the view
   *   is one it paints.
   * @property {boolean} [routeLegsOn] The routes overlay is drawn with at
   *   least one route cut into legs (map.js's own condition).
   * @property {boolean} [downloads] The downloaded-maps overlay is drawn.
   * @property {(slug: string) => boolean} [isPinnedResort] Whether the
   *   reader pinned a resort, by slug (map.js's pwaFavouritedResorts).
   * @property {Object<string, RenderedFeature[]>} [rendered] What
   *   queryRenderedFeatures returned for each LAYERS group, keyed as LAYERS.
   */

  /**
   * @param {LegendView} view
   * @param {string} key
   * @returns {RenderedFeature[]}
   */
  function rendered(view, key) {
    const list = view.rendered && view.rendered[key];
    return Array.isArray(list) ? list : [];
  }

  /**
   * @param {RenderedFeature} feature
   * @returns {Object<string, any>}
   */
  function props(feature) {
    return (feature && feature.properties) || {};
  }

  /**
   * Which keys and which rows the first tier shows for a view.
   *
   * `keys[k]` — the key shows. `items[name]` — one row inside a key shows
   * (a key with a single kind of mark has none). `on[k]` — the layer is
   * switched on at all, wherever the view is: the second tier marks it.
   *
   * A clustered field report counts for every family and both states: a
   * cluster hides which pins it holds, so the honest key is the whole one.
   *
   * @param {LegendView} view
   * @returns {{keys: Object<string, boolean>, items: Object<string, boolean>, on: Object<string, boolean>}}
   */
  function legendState(view) {
    const v = view || {};
    /** @type {Object<string, boolean>} */
    const items = {};

    const reports = rendered(v, 'community_reports');
    const clustered = reports.some((f) => props(f).point_count != null);
    for (const family of FAMILIES) {
      items['report-' + family] = clustered
        || reports.some((f) => (props(f).family || 'WARNING') === family);
    }
    items['report-live'] = clustered || reports.some((f) => props(f)._state === 'live');
    items['report-recent'] = clustered || reports.some((f) => props(f)._state === 'recent');

    const zones = rendered(v, 'rest_zones');
    const inForce = (/** @type {RenderedFeature} */ f) => props(f).in_force === true;
    items['rest-binding'] = zones.some((f) => inForce(f) && props(f).binding === true);
    items['rest-recommended'] = zones.some((f) => inForce(f) && props(f).binding !== true);
    items['rest-out-of-season'] = zones.some((f) => !inForce(f));

    // A pinned resort is a resort dot in the favourites blue, drawn with
    // the resorts; a resort feature's `id` is its slug.
    const pinned = typeof v.isPinnedResort === 'function' ? v.isPinnedResort : () => false;
    const resortList = rendered(v, 'resorts');
    const resorts = resortList.length > 0;
    const favourites = rendered(v, 'favourites').length > 0;
    items['pin-resort'] = resortList.some((f) => !pinned(String(props(f).id)));
    items['pin-pinned'] = resortList.some((f) => pinned(String(props(f).id)));
    items['pin-favourite'] = favourites;

    const keys = {
      danger: !!v.bulletins && rendered(v, 'danger').length > 0,
      problem_terrain: !!v.problemTerrain,
      community_reports: reports.length > 0,
      terrain_filter: !!v.terrainFilter,
      rest_zones: zones.length > 0,
      routes: !!v.routeLegsOn && rendered(v, 'routes').length > 0,
      pins: resorts || favourites,
      downloads: !!v.downloads,
    };
    const on = {
      danger: !!v.bulletins,
      problem_terrain: !!v.problemTerrain,
      community_reports: reports.length > 0,
      terrain_filter: !!v.terrainFilter,
      rest_zones: zones.length > 0,
      routes: !!v.routeLegsOn,
      pins: resorts || favourites,
      downloads: !!v.downloads,
    };
    return { keys, items, on };
  }

  /**
   * The first tier's one-line credit, from the style's attribution strings.
   *
   * Each string is the HTML a source carries ("<a …>© swisstopo</a>",
   * "swisstopo — swissALTI3D", "© BAFU"). The line names each provider
   * once: tags stripped, the product after " — " dropped, the © folded so
   * "swisstopo" and "© swisstopo" are one name. The full strings stay in
   * the second tier, which is where the licences are honoured in full.
   *
   * @param {string[]} attributions
   * @returns {string} "© swisstopo · BAFU", or "" when nothing is credited.
   */
  function shortSources(attributions) {
    /** @type {string[]} */
    const names = [];
    const seen = new Set();
    for (const raw of attributions || []) {
      const text = String(raw || '')
        .replace(/<[^>]*>/g, '')
        .replace(/&copy;/g, '©')
        .replace(/&amp;/g, '&')
        .replace(/&nbsp;/g, ' ');
      for (const part of text.split(/\s*(?:·|&middot;|\|)\s*/)) {
        const name = part.split(/\s+[—–-]\s+/)[0].replace(/^©\s*/, '').trim();
        if (!name) continue;
        const folded = name.toLowerCase();
        if (seen.has(folded)) continue;
        seen.add(folded);
        names.push(name);
      }
    }
    return names.length ? '© ' + names.join(' · ') : '';
  }

  self.pwaMapLegendCore = Object.freeze({
    KEYS,
    LAYERS,
    legendState,
    shortSources,
  });
})();
