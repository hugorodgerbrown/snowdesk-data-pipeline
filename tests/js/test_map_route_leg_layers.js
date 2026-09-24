/*
 * tests/js/test_map_route_leg_layers.js — a saved route drawn as its legs
 * and transitions, wired through map.js (SNOW-1017, replacing SNOW-910's
 * slope-coloured line).
 *
 * tests/js/test_route_legs_core.js covers the slicing, the numbering and
 * the opacity expression. What is left is the wiring, and four parts of
 * it fail SILENTLY — every layer still exists, nothing throws, and the map
 * is simply wrong:
 *
 *   - DOUBLE PAINTING. A legged route is drawn leg by leg, so it has to
 *     LEAVE `routes-line` and `routes-line-casing`. Get that filter wrong
 *     and the flat fuchsia line shows through every dash of a climb.
 *   - THE DASH SPLIT. `line-dasharray` is not data-driven, so a climb and a
 *     descent need a layer each, and the filters must split the legs
 *     between them with none drawn twice and none dropped.
 *   - THE TAP. `routes-line` no longer draws a legged route, so the two leg
 *     layers have to be in the marker-exclusion set or every such route
 *     becomes untappable and the tap falls through to the region.
 *   - THE SELECTION. Opening a leg on the rail dims the others through the
 *     cursor; closing it has to restore them, or the map stays dimmed
 *     after the rail has gone.
 *   - THE CURSOR (SNOW-1019). A selection and the cursor index are drawn
 *     on the line from the same subscription, and a pointer on the open
 *     route's line writes the index back — a tap there opening the leg it
 *     lands in rather than re-running the first tap's framing.
 *
 * SNOW-972's FRAMING invariant lives here too, because the two facts it
 * relates — where the camera comes to rest on a route, and the minzoom of
 * each mark drawn on that route — are both recorded by this harness. A
 * route framed below its own marks' minzoom is the defect.
 *
 * Booting map.js in jsdom follows tests/js/test_map_route_endpoints.js's
 * pattern — see its header for the rationale.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';
import { installRouteRailStub } from './_route_rail_stub.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** Four sampled boundaries bounding three segments: gentle, steep, unknown.
 *
 * The steep one is over 50°, so SNOW-964 names it a no-fall passage. It
 * lies in the second leg, the descent, which is what the passage edge's
 * colour is checked against.
 */
const SLOPE = {
  points: [[7.0, 46.0], [7.0, 46.005], [7.0, 46.01], [7.0, 46.015]],
  angles: [12.0, 52.0, null],
  passages: [{ from: 1, to: 1, m: 25.0, fall_line: 'descending' }],
  // The same steep segment carries a fall-line mark: the ground there
  // faces 205°, while the track runs due NORTH. Nothing in the stack may
  // answer 0 for this arrow.
  fall_lines: [{ i: 1, deg: 205 }],
};

/** Two legs over the sampled route: up the first segment, down the rest. */
const LEGS = [
  { i: 1, from: 0, to: 0, climbing: true, point_from: 0, point_to: 1 },
  { i: 2, from: 1, to: 2, climbing: false, point_from: 1, point_to: 3 },
];

/** A legged sampled route, a flat one, and a pending share with legs. */
const ROUTES_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [
          [7.0, 46.0, 1500], [7.0, 46.005, 2100], [7.0, 46.01, 1900], [7.0, 46.015, 1700],
        ],
      },
      properties: {
        uuid: 'sampled-route',
        name: 'Legged',
        // The bbox activateRoute frames the track with. A leg carries
        // NONE of this, which is what makes it the evidence that a tap
        // resolved back to the whole route.
        bounds: [7.0, 46.0, 7.0, 46.015],
        slope: SLOPE,
        legs: LEGS,
      },
    },
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[8.0, 47.0], [8.0, 47.02]],
      },
      // No `legs`: a track with no elevation, which detect_legs cuts into
      // nothing. The one kind of owned route still drawn flat.
      properties: { uuid: 'flat-route', name: 'No legs' },
    },
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[9.0, 45.0, 1100], [9.0, 45.015, 1700]],
      },
      properties: {
        // No uuid: a non-owner is never handed one. `_route_feature` is
        // shared between the owned and pending branches, though, so a
        // share DOES arrive carrying a slope record and legs — which is
        // what makes "a pending share draws no legs" a real assertion.
        token: 'tok-pending',
        pending: true,
        name: 'Shared with me',
        bounds: [9.0, 45.0, 9.0, 45.015],
        slope: SLOPE,
        legs: [{ i: 1, from: 0, to: 2, climbing: true, point_from: 0, point_to: 1 }],
      },
    },
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[6.0, 46.5, 1500], [6.0, 46.51, 1800]],
      },
      // An overlay payload cached before SNOW-1017: `legs` in sample
      // indices only, with no point indices to slice a line by.
      properties: {
        uuid: 'stale-route',
        name: 'Cached before legs',
        bounds: [6.0, 46.5, 6.0, 46.51],
        legs: [{ i: 1, from: 0, to: 0, climbing: true }],
      },
    },
  ],
};

/**
 * Evaluate the handful of MapLibre filter operators the route layers use
 * against one feature's properties.
 *
 * @param {Array<*>} filter A filter expression.
 * @param {object} properties The feature's properties.
 * @returns {*} The expression's value.
 */
function evaluate(filter, properties) {
  const [op, ...args] = filter;
  switch (op) {
    case 'all': return args.every((arg) => evaluate(arg, properties));
    case 'any': return args.some((arg) => evaluate(arg, properties));
    case '!': return !evaluate(args[0], properties);
    case 'has': return args[0] in properties;
    case 'get': return properties[args[0]] ?? null;
    case '==': return evaluate(args[0], properties) === args[1];
    case '!=': return evaluate(args[0], properties) !== args[1];
    default: throw new Error(`unsupported operator ${op}`);
  }
}

/**
 * The uuids a line layer draws out of the `routes` source's data.
 *
 * @param {string} layerId A layer on the `routes` source.
 * @returns {Array<string>}
 */
function drawnBy(layerId) {
  const filter = layers.get(layerId).filter;
  return sources.get('routes').data.features
    .filter((f) => !filter || evaluate(filter, f.properties))
    .map((f) => f.properties.uuid || f.properties.token);
}

/** The recording rail stub (tests/js/_route_rail_stub.js). */
let rail;

/**
 * The body map.js has seated in the route detail sheet, if it is open.
 *
 * SNOW-1018: a tap opens rail one, and the sheet opens from the rail's
 * menu — `rail.openDetails()` below is that press. The body is the
 * terrain lines alone; the name, figures and profile are the rail's.
 * `window.pwaRouteDetail.close()` between taps is what makes "the newest
 * one" meaningful — MapSheet's teardown empties the body.
 *
 * @returns {HTMLElement|null}
 */
function detailBody() {
  const sheetEl = document.getElementById('route-detail-sheet');
  if (!sheetEl || sheetEl.hasAttribute('hidden')) return null;
  return sheetEl.querySelector('[data-route-detail]');
}

/** Layer definitions as map.js added them, by id. */
const layers = new Map();
/** Source definitions as map.js added them, by id. */
const sources = new Map();
/** Every bbox map.js asked the camera to frame. */
const fitBoundsCalls = [];
/** And the options it framed each with, in step with the array above. */
const fitBoundsOptions = [];
/** Every setPaintProperty call, as [layerId, property, value]. */
const paintCalls = [];
/** What the next queryRenderedFeatures call should answer, by layer id. */
let queryAnswer = () => [];
/** How the stub projects a [lon, lat] to screen px; a test may replace it. */
let projectLngLat = () => ({ x: 0, y: 0 });

/**
 * MapLibre stub that records what installRoutesLayer builds.
 *
 * @returns {object} The map stub.
 */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (event, layerOrHandler, maybeHandler) => {
      if (typeof layerOrHandler === 'function') {
        (handlers[event] ||= []).push(layerOrHandler);
      } else if (typeof maybeHandler === 'function') {
        (handlers[`${event}:${layerOrHandler}`] ||= []).push(maybeHandler);
      }
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: (id) => (layers.get(id) || {}).filter || null,
    getLayoutProperty: (id, prop) => ((layers.get(id) || {}).layout || {})[prop],
    getPaintProperty: (id, prop) => ((layers.get(id) || {}).paint || {})[prop],
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources.get(id) || null,
    addSource: (id, def) => {
      sources.set(id, {
        ...def,
        setData: (data) => { sources.get(id).data = data; },
      });
    },
    addLayer: (def) => { layers.set(def.id, def); },
    removeLayer: (id) => layers.delete(id),
    removeSource: (id) => sources.delete(id),
    moveLayer: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layer = layers.get(id);
      if (layer) (layer.layout ||= {})[prop] = value;
    },
    setPaintProperty: (id, prop, value) => {
      paintCalls.push([id, prop, value]);
      const layer = layers.get(id);
      if (layer) (layer.paint ||= {})[prop] = value;
    },
    setFilter: () => {},
    setFeatureState: () => {},
    removeFeatureState: () => {},
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    loaded: () => true,
    areTilesLoaded: () => true,
    listImages: () => [],
    hasImage: () => true,
    addImage: () => {},
    triggerRepaint: () => {},
    fitBounds: (bbox, options) => {
      fitBoundsCalls.push(bbox);
      fitBoundsOptions.push(options || {});
    },
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 7, lat: 46 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: (lngLat) => projectLngLat(lngLat),
    unproject: () => ({ lng: 7, lat: 46 }),
    queryRenderedFeatures: (point, options) => queryAnswer(options),
    resize: () => {},
    handlers,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      const popup = {
        setHTML: () => popup,
        setDOMContent: () => popup,
        setLngLat: () => popup,
        addTo: () => popup,
        getElement: () => document.createElement('div'),
        on: () => {},
        remove: () => {},
      };
      return popup;
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/** The narrowest 2D-canvas double the route marker colours need. */
function stubCanvas2D() {
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function getContext(type) {
    if (type !== '2d') return original ? original.call(this, type) : null;
    return {
      fillStyle: '#000000',
      fillRect: () => {},
      getImageData: () => ({ data: Uint8ClampedArray.from([0, 0, 0, 255]) }),
    };
  };
  return () => { HTMLCanvasElement.prototype.getContext = original; };
}

/** The DOM map.js's boot reads, plus the legend section the key lives in. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-community-reports-url="/api/community-reports.geojson"
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <div id="route-detail-sheet" hidden tabindex="-1" data-overlay></div>
    <template id="route-detail-template">
      <div>
        <div data-route-detail-figures></div>
        <div data-route-detail-bulletin></div>
      </div>
    </template>
    <section id="map-route-legs-section" hidden></section>`;
}

let mapStub;
let core;
let legsCore;

beforeAll(async () => {
  localStorage.clear();
  stubCanvas2D();
  buildFixture();
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(
        String(url).includes('routes.geojson') ? ROUTES_FC : EMPTY_FC,
      ),
    })),
  );

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  await import('../../static/js/route_slope_core.js');
  await import('../../static/js/route_legs_core.js');
  await import('../../static/js/route_cursor_core.js');
  await import('../../static/js/route_cursor_map_core.js');
  // SNOW-973: the sheet the tap opens, and the controller it attaches
  // through. Both before the bundle, as the page loads them.
  await import('../../static/js/map_sheet.js');
  await import('../../static/js/map_route_detail.js');
  core = globalThis.pwaRouteSlopeCore;
  legsCore = globalThis.pwaRouteLegsCore;
  rail = installRouteRailStub();
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();

  await window.pwaRoutesOverlay.show();
});

describe('the leg source', () => {
  it('is installed beside the routes source', () => {
    expect(sources.has('route-legs')).toBe(true);
  });

  it('holds one line per leg of the owned legged route only', () => {
    // The flat route has no legs and the pending share is drawn as a
    // share, so both contribute nothing.
    const data = sources.get('route-legs').data;

    expect(data.features.map((f) => f.properties)).toEqual([
      { uuid: 'sampled-route', i: 1, climbing: true },
      { uuid: 'sampled-route', i: 2, climbing: false },
    ]);
  });
});

describe('routes-line and its casing', () => {
  it('drop a legged route, so it is not painted flat underneath', () => {
    // The double-painting guard. `has` and not a test on the value: the
    // server omits `legs` entirely for a route it finds no leg in.
    const flat = ['all', ['!=', ['get', 'pending'], true], ['!', ['has', 'legs']]];

    expect(layers.get('routes-line').filter).toEqual(flat);
    expect(layers.get('routes-line-casing').filter)
      .toEqual(['any', ['==', ['get', 'pending'], true], flat]);
  });

  it('draw the routes the leg layers do not, and only those', () => {
    expect(drawnBy('routes-line')).toEqual(['flat-route', 'stale-route']);
    expect(drawnBy('routes-line-casing'))
      .toEqual(['flat-route', 'tok-pending', 'stale-route']);
  });

  it('keep a route whose cached legs cannot be sliced, rather than lose it', () => {
    // The stale payload's legs carry no point indices, so no leg layer
    // can draw it. The source copy drops its `legs`, and the flat line
    // keeps it — where the server's own payload, which the rail reads,
    // keeps its legs.
    const stale = sources.get('routes').data.features
      .find((f) => f.properties.uuid === 'stale-route');

    expect(stale.properties).not.toHaveProperty('legs');
    expect(sources.get('route-legs').data.features
      .some((f) => f.properties.uuid === 'stale-route')).toBe(false);

    tapLayer('routes-line', { uuid: 'stale-route', name: 'Cached before legs' });
    expect(rail.last().feature.properties.legs).toEqual([
      { i: 1, from: 0, to: 0, climbing: true },
    ]);
  });

  it('leave the pending line alone — its dash carries a different fact', () => {
    expect(layers.get('routes-line-pending').filter)
      .toEqual(['==', ['get', 'pending'], true]);
  });
});

describe('the leg layers', () => {
  it('split the legs by direction, with none drawn twice or dropped', () => {
    expect(layers.get('routes-leg-climb').filter).toEqual(['==', ['get', 'climbing'], true]);
    expect(layers.get('routes-leg-descent').filter).toEqual(['!=', ['get', 'climbing'], true]);
  });

  it('dash a climb and draw a descent solid', () => {
    // `line-dasharray` is not data-driven, which is why there are two.
    expect(layers.get('routes-leg-climb').paint['line-dasharray']).toEqual([2, 1.5]);
    expect(layers.get('routes-leg-climb').layout['line-cap']).toBe('butt');
    expect(layers.get('routes-leg-descent').paint['line-dasharray']).toBeUndefined();
    expect(layers.get('routes-leg-descent').layout['line-cap']).toBe('round');
  });

  it('paint in the rail\'s two colours', () => {
    expect(layers.get('routes-leg-climb').paint['line-color']).toBe(legsCore.LEG_CLIMB_COLOUR);
    expect(layers.get('routes-leg-descent').paint['line-color'])
      .toBe(legsCore.LEG_DESCENT_COLOUR);
  });

  it('carry a casing of their own, under both', () => {
    const ids = [...layers.keys()];

    expect(layers.get('routes-leg-casing').source).toBe('route-legs');
    expect(ids.indexOf('routes-leg-casing')).toBeLessThan(ids.indexOf('routes-leg-climb'));
    expect(ids.indexOf('routes-leg-casing')).toBeLessThan(ids.indexOf('routes-leg-descent'));
  });

  it('sit under a pending share, which carries the action', () => {
    const ids = [...layers.keys()];

    expect(ids.indexOf('routes-line-pending'))
      .toBeGreaterThan(ids.indexOf('routes-leg-descent'));
  });

  it('are reached by the routes overlay switch', () => {
    const routeLayers = window.snowdeskMapState.overlayLayers.routes;

    for (const id of [
      'routes-leg-casing', 'routes-leg-climb', 'routes-leg-descent',
      'routes-transitions', 'routes-transition-labels',
    ]) {
      expect(routeLayers).toContain(id);
      expect(layers.get(id).layout.visibility).toBe('visible');
    }
    // The roundel is still painted from the flat line.
    expect(routeLayers[0]).toBe('routes-line');
  });
});

describe('the transition markers', () => {
  it('mark legs − 1 transitions on the owned route', () => {
    const features = sources.get('route-transitions').data.features;

    expect(features.map((f) => f.properties)).toEqual([
      { uuid: 'sampled-route', n: 1, climbing: false },
    ]);
    expect(features[0].geometry.coordinates).toEqual([7.0, 46.005]);
  });

  it('number each circle, both from z11', () => {
    expect(layers.get('routes-transitions').type).toBe('circle');
    expect(layers.get('routes-transitions').minzoom).toBe(11);
    expect(layers.get('routes-transition-labels').minzoom).toBe(11);
    expect(layers.get('routes-transition-labels').layout['text-field'])
      .toEqual(['to-string', ['get', 'n']]);
  });

  it('sit over the crux rings and under the endpoint markers', () => {
    const ids = [...layers.keys()];

    expect(ids.indexOf('routes-transitions')).toBeGreaterThan(ids.indexOf('routes-cruxes'));
    expect(ids.indexOf('routes-transition-labels'))
      .toBeGreaterThan(ids.indexOf('routes-transitions'));
    expect(ids.indexOf('routes-transition-labels'))
      .toBeLessThan(ids.indexOf('routes-endpoints'));
  });
});

describe('the no-fall passage layers (SNOW-964)', () => {
  it('draw off a source that holds only the passages', () => {
    expect(layers.get('routes-passage-edge').source).toBe('route-passages');
    expect(layers.get('routes-passage-core').source).toBe('route-passages');
    expect(layers.get('routes-passage-edge').filter).toBeUndefined();
    expect(sources.get('route-passages').data.features).toHaveLength(1);
  });

  it('paint the edge in its leg\'s colour', () => {
    // `case` on explicit equality, never `match` on a boolean: MapLibre's
    // validator rejects a boolean branch label, and the rejection swapped
    // the whole basemap for the offline fallback style (SNOW-1019).
    expect(layers.get('routes-passage-edge').paint['line-color']).toEqual([
      'case',
      ['==', ['get', 'climbing'], true], legsCore.LEG_CLIMB_COLOUR,
      ['==', ['get', 'climbing'], false], legsCore.LEG_DESCENT_COLOUR,
      '#c026d3',
    ]);
    // The steep segment lies in leg 2, the descent.
    expect(sources.get('route-passages').data.features[0].properties.climbing).toBe(false);
  });

  it('sandwich the leg lines', () => {
    // The edge UNDER the leg line and the core OVER it: that is what makes
    // the mark read as a split in the line rather than a line beside it.
    const ids = [...layers.keys()];

    expect(ids.indexOf('routes-passage-edge')).toBeLessThan(ids.indexOf('routes-leg-climb'));
    expect(ids.indexOf('routes-passage-core'))
      .toBeGreaterThan(ids.indexOf('routes-leg-descent'));
  });

  it('keep the edge inside the leg casing and the core inside the edge', () => {
    const edge = layers.get('routes-passage-edge').paint['line-width'];
    const inner = layers.get('routes-passage-core').paint['line-width'];
    const casing = layers.get('routes-leg-casing').paint['line-width'];

    for (let i = 3; i < edge.length; i += 2) {
      expect(edge[i + 1]).toBeLessThanOrEqual(casing[i + 1]);
      expect(inner[i + 1]).toBeLessThan(edge[i + 1]);
    }
  });

  it('paint the core in its own light colour', () => {
    expect(layers.get('routes-passage-core').paint['line-color'])
      .toBe(core.PASSAGE_CORE_COLOUR);
  });
});

describe('the fall-line arrow layer', () => {
  /** The ids in the order installRoutesLayer added them. */
  const order = () => [...layers.keys()];

  it('draws from its own point source, placed at the segment midpoint', () => {
    // A symbol cannot be placed on a line layer, so the arrows need a
    // Point source — and the point is the middle of the marked segment,
    // which is where the aspect was sampled.
    const features = sources.get('route-fall-lines').data.features;

    expect(features).toHaveLength(1);
    expect(features[0].geometry.coordinates[1]).toBeCloseTo(46.0075, 9);
  });

  it('rotates the icon by the bearing, aligned to the map', () => {
    // THE CLAIM THIS WHOLE LAYER MAKES. `icon-rotate` off the feature's
    // own `deg`, and `icon-rotation-alignment: 'map'` so it stays a
    // compass bearing when the reader rotates the map — a screen-aligned
    // arrow would point somewhere else the moment they did.
    const layout = layers.get('routes-fall-lines').layout;

    expect(layout['icon-rotate']).toEqual(['get', 'deg']);
    expect(layout['icon-rotation-alignment']).toBe('map');
    expect(sources.get('route-fall-lines').data.features[0].properties.deg)
      .toBe(205);
  });

  it('sits over the leg lines and under the rings and the markers', () => {
    // An arrow is ambient; a ring is an instruction to look at one
    // place, and a start dot is how a reader orients. The arrow yields.
    const ids = order();

    expect(ids.indexOf('routes-fall-lines'))
      .toBeGreaterThan(ids.indexOf('routes-passage-core'));
    expect(ids.indexOf('routes-fall-lines'))
      .toBeLessThan(ids.indexOf('routes-cruxes'));
    expect(ids.indexOf('routes-fall-lines'))
      .toBeLessThan(ids.indexOf('routes-endpoints'));
  });

  it('lets the collision engine thin it, unlike every other route mark', () => {
    // The one mark here that may be dropped: the survivors say the same
    // thing about the same face. A dropped crux ring would understate
    // the day, which is why those set the opposite.
    expect(layers.get('routes-fall-lines').layout['icon-allow-overlap'])
      .toBe(false);
    expect(layers.get('routes-cruxes').layout['icon-allow-overlap']).toBe(true);
  });

  it('blocks nothing else from being placed', () => {
    // A basemap label losing out to an ambient arrow is the wrong trade.
    expect(layers.get('routes-fall-lines').layout['icon-ignore-placement'])
      .toBe(true);
  });

  it('is held back a zoom step further than the rings', () => {
    // At z11 a 250 m spacing is about 9 CSS pixels against a 20 px
    // arrow, so the marks would read as texture on the track.
    expect(layers.get('routes-fall-lines').minzoom)
      .toBeGreaterThan(layers.get('routes-cruxes').minzoom);
  });

  it('is painted in its own ink, with the ring\'s halo', () => {
    const paint = layers.get('routes-fall-lines').paint;

    expect(paint['icon-color']).toBe(core.FALL_LINE_COLOUR);
    // Over the dark leg casing the ink alone would vanish.
    expect(paint['icon-halo-color']).toBe('#ffffff');
  });

  it('is reached by the routes overlay switch', () => {
    expect(layers.get('routes-fall-lines').layout.visibility).toBe('visible');
  });

  it('marks nothing on the route nothing has sampled', () => {
    // An unsampled route has no record, so it has no marks — the same
    // silence the flat line keeps, rather than an arrow guessed from the
    // track's own coordinates.
    const uuids = sources.get('route-fall-lines').data.features
      .map((f) => f.properties.uuid);

    expect(uuids).toEqual(['sampled-route']);
  });
});

/** Fire the map-level click, with one layer answering the query. */
function tapLayer(layerId, properties, point = { x: 10, y: 10 }) {
  fitBoundsCalls.length = 0;
  queryAnswer = (options) => (
    (options.layers || []).includes(layerId)
      ? [{ layer: { id: layerId }, properties }]
      : []
  );
  for (const handler of mapStub.handlers.click || []) {
    handler({ point, lngLat: { lng: 7, lat: 46.01 } });
  }
  queryAnswer = () => [];
}

/** Tap a leg of the legged route — exactly what a leg carries. */
function tapLeg(layerId = 'routes-leg-descent') {
  tapLayer(layerId, { uuid: 'sampled-route', i: 2, climbing: false });
}

describe('tapping a legged route', () => {
  it('frames it without a zoom cap, so its marks can render', () => {
    // SNOW-972. A region is tens of kilometres across; a route is often
    // one or two, so a borrowed cap stopped a route reaching a legible size.
    tapLeg();

    expect(fitBoundsOptions.at(-1)).not.toHaveProperty('maxZoom');
  });

  it('frames it far enough in for every mark drawn on it, markers included', () => {
    // THE INVARIANT, read off the layers as installed rather than naming
    // any of them: a mark added later, or moved a step further in, is
    // covered here without this test being touched.
    tapLeg();
    const options = fitBoundsOptions.at(-1);

    const marks = [...layers.values()].filter(
      (layer) => (layer.type === 'symbol' || layer.type === 'circle')
        && layer.source !== 'routes'
        && String(layer.id).startsWith('routes-')
        && typeof layer.minzoom === 'number',
    );
    // A guard on the guard: with no marks found this would pass vacuously.
    expect(marks.map((layer) => layer.id)).toContain('routes-transitions');

    const deepest = Math.max(...marks.map((layer) => layer.minzoom));
    expect(options.maxZoom === undefined || options.maxZoom >= deepest).toBe(true);
  });

  it('opens the route the leg belongs to, from either layer', () => {
    // The bbox is on the ROUTE feature and not on the leg, so framing it
    // is proof the uuid resolved back to the whole route.
    tapLeg('routes-leg-descent');
    expect(fitBoundsCalls).toEqual([[[7.0, 46.0], [7.0, 46.015]]]);

    tapLeg('routes-leg-climb');
    expect(fitBoundsCalls).toEqual([[[7.0, 46.0], [7.0, 46.015]]]);
  });

  it('does not query the passage or transition layers, which add no route', () => {
    const queried = [];
    queryAnswer = (options) => {
      queried.push(...(options.layers || []));
      return [];
    };
    for (const handler of mapStub.handlers.click || []) {
      handler({ point: { x: 10, y: 10 }, lngLat: { lng: 7, lat: 46.01 } });
    }
    queryAnswer = () => [];

    expect(queried).toContain('routes-leg-climb');
    expect(queried).toContain('routes-leg-descent');
    expect(queried).not.toContain('routes-passage-edge');
    expect(queried).not.toContain('routes-transitions');
  });

  it('names the no-fall passage and what the track does with it', () => {
    window.pwaRouteDetail.close();
    tapLeg();
    rail.openDetails();

    const text = detailBody().textContent;

    expect(text).toContain('1 no-fall passage');
    expect(text).toContain('down the fall line');
  });

  it('hands the rail the whole route the leg belongs to', () => {
    tapLeg();

    const { feature } = rail.last();
    expect(feature.properties.uuid).toBe('sampled-route');
    expect(feature.properties.legs).toHaveLength(2);
  });
});

describe('opening a leg on the rail', () => {
  /** The newest opacity set on one layer. */
  const opacityOf = (id) => layers.get(id).paint['line-opacity'];

  it('dims every other leg, and closing it restores the lines', () => {
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    rail.state.cursor = cursor;
    tapLeg();

    cursor.openLeg(LEGS[1]);
    const dimmed = legsCore.dimOpacity({ uuid: 'sampled-route', i: 2 }, 1, 0.25);
    expect(opacityOf('routes-leg-climb')).toEqual(dimmed);
    expect(opacityOf('routes-leg-descent')).toEqual(dimmed);
    expect(opacityOf('routes-leg-casing'))
      .toEqual(legsCore.dimOpacity({ uuid: 'sampled-route', i: 2 }, 0.55, 0.15));
    // The passages dim with their leg; one on a flat route (no `i`) stays.
    for (const id of ['routes-passage-edge', 'routes-passage-core']) {
      expect(opacityOf(id)).toEqual(['case', ['has', 'i'], dimmed, 1]);
    }

    cursor.closeLeg();
    expect(opacityOf('routes-passage-edge')).toBe(1);
    expect(opacityOf('routes-passage-core')).toBe(1);
    expect(opacityOf('routes-leg-climb')).toBe(1);
    expect(opacityOf('routes-leg-descent')).toBe(1);
    expect(opacityOf('routes-leg-casing')).toBe(0.55);
    rail.state.cursor = null;
  });

  it('stops following the cursor once another route opens', () => {
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    rail.state.cursor = cursor;
    tapLeg();
    rail.state.cursor = null;
    tapLayer('routes-line', { uuid: 'flat-route', name: 'No legs' });
    paintCalls.length = 0;

    cursor.openLeg(LEGS[0]);

    expect(paintCalls).toEqual([]);
    expect(opacityOf('routes-leg-climb')).toBe(1);
  });

  it('installs the leg layers dimmed when a basemap swap rebuilds them', async () => {
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    rail.state.cursor = cursor;
    tapLeg();
    cursor.openLeg(LEGS[1]);

    // What setStyle leaves behind: none of our layers or sources, which
    // forces the styledata handler past its guard and through the
    // re-install. The rail and its open leg survive the swap.
    for (const id of [...layers.keys()]) layers.delete(id);
    for (const id of [...sources.keys()]) sources.delete(id);
    paintCalls.length = 0;
    for (const handler of mapStub.handlers.styledata || []) await handler();

    expect(layers.has('routes-leg-climb')).toBe(true);
    const open = { uuid: 'sampled-route', i: 2 };
    expect(opacityOf('routes-leg-climb')).toEqual(legsCore.dimOpacity(open, 1, 0.25));
    expect(opacityOf('routes-leg-descent')).toEqual(legsCore.dimOpacity(open, 1, 0.25));
    expect(opacityOf('routes-passage-edge'))
      .toEqual(['case', ['has', 'i'], legsCore.dimOpacity(open, 1, 0.25), 1]);
    expect(opacityOf('routes-leg-casing')).toEqual(legsCore.dimOpacity(open, 0.55, 0.15));
    // Painted at install, not patched afterwards.
    expect(paintCalls.filter(([id]) => id.startsWith('routes-leg-'))).toEqual([]);

    cursor.closeLeg();
    rail.state.cursor = null;
  });

  it('never dims for a pending share', () => {
    rail.state.cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    tapLayer('routes-line-pending', {
      token: 'tok-pending',
      pending: true,
      name: 'Shared with me',
      bounds: JSON.stringify([9.0, 45.0, 9.0, 45.015]),
    });
    rail.state.cursor.openLeg({ i: 1, from: 0, to: 2 });

    expect(opacityOf('routes-leg-climb')).toBe(1);
    rail.state.cursor = null;
  });
});

describe('a sampled route somebody shared', () => {
  it('draws no legs, no markers and no passages', () => {
    for (const id of ['route-legs', 'route-transitions', 'route-passages']) {
      expect(sources.get(id).data.features.every((f) => f.properties.uuid === 'sampled-route'))
        .toBe(true);
    }
  });

  it('opens the rail with the cached pending share', () => {
    window.pwaRouteDetail.close();
    tapLayer('routes-line-pending', {
      token: 'tok-pending',
      pending: true,
      name: 'Shared with me',
      bounds: JSON.stringify([9.0, 45.0, 9.0, 45.015]),
    });

    const { feature } = rail.last();
    expect(feature.properties.token).toBe('tok-pending');
    expect(feature.geometry.coordinates.length).toBeGreaterThan(0);
  });
});

describe('the legend key', () => {
  it('is revealed once a legged route is drawn', () => {
    expect(document.getElementById('map-route-legs-section').hidden).toBe(false);
  });
});

describe('the route cursor on the map (SNOW-1019)', () => {
  /**
   * Project the sampled route onto a vertical screen line: x = 0, and y
   * falling 10 px per 0.001° north, so the three segment middles
   * (46.0025, 46.0075, 46.0125) sit at y = 125, 75 and 25.
   */
  const alongTheRoute = ([lng, lat]) => ({ x: (lng - 7.0) * 10000, y: (46.015 - lat) * 10000 });

  /** Open the sampled route on the rail with a fresh cursor. */
  const openSampledRoute = () => {
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    rail.state.cursor = cursor;
    tapLeg();
    return cursor;
  };

  /** Hover the mouse over a screen point. */
  const hover = (point) => {
    for (const handler of mapStub.handlers.mousemove || []) handler({ point });
  };

  it('draws its layers over the lines, reached by the routes switch', () => {
    const ids = [...layers.keys()];
    const routeLayers = window.snowdeskMapState.overlayLayers.routes;

    for (const id of [
      'routes-cursor-selection-casing', 'routes-cursor-selection', 'routes-cursor-point',
    ]) {
      expect(ids.indexOf(id)).toBeGreaterThan(ids.indexOf('routes-line-pending'));
      expect(routeLayers).toContain(id);
      expect(layers.get(id).layout.visibility).toBe('visible');
    }
    expect(layers.get('routes-cursor-point').type).toBe('circle');
  });

  it('draws a selection as its stretch of line, and clears it', () => {
    const cursor = openSampledRoute();

    cursor.select({ kind: 'band', from: 1, to: 2 });
    expect(sources.get('route-cursor-selection').data.features[0].geometry.coordinates)
      .toEqual([[7.0, 46.005], [7.0, 46.01], [7.0, 46.015]]);

    cursor.clearSelection();
    expect(sources.get('route-cursor-selection').data.features).toEqual([]);
    rail.state.cursor = null;
  });

  it('draws the cursor index as a dot on its segment, and hides it on null', () => {
    const cursor = openSampledRoute();

    cursor.setIndex(1);
    const [dot] = sources.get('route-cursor-point').data.features;
    expect(dot.geometry.coordinates[1]).toBeCloseTo(46.0075);

    cursor.setIndex(null);
    expect(sources.get('route-cursor-point').data.features).toEqual([]);
    rail.state.cursor = null;
  });

  it('opens the leg under a tap on the open route and moves the cursor there', () => {
    projectLngLat = alongTheRoute;
    const cursor = openSampledRoute();
    const opened = rail.state.calls.length;

    // y = 70 is nearest the second segment's middle, in leg 2.
    tapLayer('routes-leg-descent', { uuid: 'sampled-route', i: 2, climbing: false }, { x: 2, y: 70 });

    expect(cursor.state().openLeg).toMatchObject({ i: 2, from: 1, to: 2 });
    expect(cursor.state().index).toBe(1);
    // Not a second first tap: no re-framing, and the rail is not reopened.
    expect(fitBoundsCalls).toEqual([]);
    expect(rail.state.calls).toHaveLength(opened);
    projectLngLat = () => ({ x: 0, y: 0 });
    rail.state.cursor = null;
  });

  it('moves the cursor on a mouse hover near the line, and not away from it', () => {
    projectLngLat = alongTheRoute;
    const cursor = openSampledRoute();

    hover({ x: 5, y: 120 });
    expect(cursor.state().index).toBe(0);

    hover({ x: 80, y: 25 });
    expect(cursor.state().index).toBe(0);
    projectLngLat = () => ({ x: 0, y: 0 });
    rail.state.cursor = null;
  });

  it('repaints the selection and dot when a basemap swap rebuilds the layers', async () => {
    const cursor = openSampledRoute();
    cursor.select({ kind: 'passage', from: 1, to: 1 });
    cursor.setIndex(1);

    for (const id of [...layers.keys()]) layers.delete(id);
    for (const id of [...sources.keys()]) sources.delete(id);
    for (const handler of mapStub.handlers.styledata || []) await handler();

    expect(sources.get('route-cursor-selection').data.features).toHaveLength(1);
    expect(sources.get('route-cursor-point').data.features).toHaveLength(1);
    cursor.clearSelection();
    cursor.setIndex(null);
    rail.state.cursor = null;
  });

  it('stops answering the pointer once the rail has let the cursor go', () => {
    projectLngLat = alongTheRoute;
    const cursor = openSampledRoute();
    rail.state.cursor = null;

    hover({ x: 0, y: 125 });

    expect(cursor.state().index).toBeNull();
    projectLngLat = () => ({ x: 0, y: 0 });
  });
});
