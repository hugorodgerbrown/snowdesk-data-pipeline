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
    'weather',
    'terrain_filter',
    'ski_tours',
    'rest_zones',
    'routes',
    'pins',
    'boundaries',
    'location',
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
    weather: Object.freeze(['weather-point']),
    ski_tours: Object.freeze(['ski-tours-line', 'ski-tours-line-down']),
    ski_marks: Object.freeze(['ski-tours-summit']),
    rest_zones: Object.freeze(['rest-zones-fill']),
    routes: Object.freeze(['routes-leg-climb', 'routes-leg-descent']),
    route_line: Object.freeze(['routes-line']),
    route_slope: Object.freeze(['routes-slope-line', 'routes-slope-unknown']),
    route_transitions: Object.freeze(['routes-transitions']),
    route_ends: Object.freeze(['routes-endpoints']),
    resorts: Object.freeze(['resorts-pin']),
    favourites: Object.freeze(['favourites-pin']),
    boundary_major: Object.freeze(['major-regions-line']),
    boundary_minor: Object.freeze(['sub-regions-line']),
    boundary_micro: Object.freeze(['regions-line']),
    boundary_unsupported: Object.freeze(['regions-line-unsupported']),
    boundary_grouping: Object.freeze(['bulletin-groupings-line']),
  });

  /** The boundary tiers, each a row of the boundaries key. */
  const BOUNDARIES = Object.freeze(['major', 'minor', 'micro', 'unsupported', 'grouping']);

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
   *   least one route on it (map.js's own condition).
   * @property {boolean} [downloads] The downloaded-maps overlay is drawn.
   * @property {boolean} [location] The reader's position dot is inside
   *   the view.
   * @property {boolean} [problemTerrainOn] Problem terrain is switched
   *   on, whether or not this view is one it paints.
   * @property {boolean} [terrainFilterOn] Steep terrain likewise.
   * @property {Object<string, boolean>} [switchedOn] Per LAYERS group,
   *   whether its layers are switched on at all (in the style and not
   *   hidden), whatever the zoom and the viewport.
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

    const tours = rendered(v, 'ski_tours');
    const marks = rendered(v, 'ski_marks');
    // A tour with no terrain yet is one line, `climbing` null, drawn as
    // the way up.
    items['tour-up'] = tours.some((f) => props(f).climbing !== false);
    items['tour-down'] = tours.some((f) => props(f).climbing === false);
    items['tour-summit'] = marks.some((f) => props(f).kind !== 'hut');
    items['tour-hut'] = marks.some((f) => props(f).kind === 'hut');

    // A route is its legs, or one flat line until it has them; from z14
    // the line is coloured by slope, and its marks join it from z10–11.
    const legs = rendered(v, 'routes');
    items['route-climb'] = legs.some((f) => props(f).climbing === true);
    items['route-descent'] = legs.some((f) => props(f).climbing !== true);
    items['route-flat'] = rendered(v, 'route_line').length > 0;
    items['route-slope'] = rendered(v, 'route_slope').length > 0;
    items['route-transition'] = rendered(v, 'route_transitions').length > 0;
    items['route-ends'] = rendered(v, 'route_ends').length > 0;
    const routeParts = ['route-climb', 'route-descent', 'route-flat', 'route-slope'];

    let boundaries = false;
    for (const tier of BOUNDARIES) {
      items['boundary-' + tier] = rendered(v, 'boundary_' + tier).length > 0;
      boundaries = boundaries || items['boundary-' + tier];
    }

    const keys = {
      // Problem slopes are painted in the danger colours and their key
      // says so, so the scale shows while either one needs it — even with
      // the regions' own colour faded to nothing by the opacity row.
      danger: (!!v.bulletins && rendered(v, 'danger').length > 0) || !!v.problemTerrain,
      problem_terrain: !!v.problemTerrain,
      community_reports: reports.length > 0,
      weather: rendered(v, 'weather').length > 0,
      terrain_filter: !!v.terrainFilter,
      ski_tours: tours.length > 0 || marks.length > 0,
      rest_zones: zones.length > 0,
      routes: !!v.routeLegsOn && routeParts.some((name) => items[name]),
      pins: resorts || favourites,
      boundaries: boundaries,
      location: !!v.location,
      downloads: !!v.downloads,
    };
    // The second page's marks say what is SWITCHED ON, wherever the view
    // is and at any zoom: a layer with nothing in view is still on.
    const switched = v.switchedOn || {};
    const on = {
      danger: !!v.bulletins,
      problem_terrain: !!(v.problemTerrainOn ?? v.problemTerrain),
      community_reports: !!switched.community_reports || reports.length > 0,
      weather: !!switched.weather || keys.weather,
      terrain_filter: !!(v.terrainFilterOn ?? v.terrainFilter),
      ski_tours: !!switched.ski_tours || !!switched.ski_marks || keys.ski_tours,
      rest_zones: !!switched.rest_zones || zones.length > 0,
      routes: !!v.routeLegsOn,
      pins: !!switched.resorts || !!switched.favourites || resorts || favourites,
      // The grouping outline is the bulletins' own, not a boundary tier's.
      boundaries: ['major', 'minor', 'micro'].some((tier) => !!switched['boundary_' + tier])
        || boundaries,
      location: !!v.location,
      downloads: !!v.downloads,
    };
    return { keys, items, on };
  }

  /**
   * The first tier's one-line credit, from the style's attribution text.
   *
   * Each string is PLAIN TEXT — the caller reads the attribution element's
   * textContent, so the browser has already dropped the tags and decoded
   * the entities, and nothing here parses HTML ("© swisstopo · swisstopo —
   * swissALTI3D · © BAFU"). The line names each provider once: the
   * product after " — " dropped, the © folded so "swisstopo" and
   * "© swisstopo" are one name. The full credits stay in the second tier,
   * which is where the licences are honoured in full.
   *
   * @param {string[]} attributions
   * @returns {string} "© swisstopo · BAFU", or "" when nothing is credited.
   */
  function shortSources(attributions) {
    /** @type {string[]} */
    const names = [];
    const seen = new Set();
    for (const raw of attributions || []) {
      for (const part of String(raw || '').split(/\s*[·|]\s*/)) {
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
