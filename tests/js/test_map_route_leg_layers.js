/*
 * tests/js/test_map_route_leg_layers.js — a saved route drawn as its legs
 * and transitions, wired through map.js (SNOW-1017, replacing SNOW-910's
 * slope-coloured line), and in slope classes again from z14.
 *
 * tests/js/test_route_legs_core.js covers the slicing and the numbering.
 * What is left is the wiring, and four parts of
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
 *   - THE CURSOR (SNOW-1019). The cursor index is drawn on the line from
 *     the map's one subscription, and a tap on a route's line writes the
 *     index back: the first tap opens the panel with the point already
 *     placed, a later one moves it, and neither frames the route. Since
 *     2026-10-02 nothing else writes it — no hover, and no leg selection,
 *     so no layer is ever dimmed.
 *
 * SNOW-972's FRAMING invariant lives here too, because the two facts it
 * relates — where the camera comes to rest on a route, and the minzoom of
 * each mark drawn on that route — are both recorded by this harness. A
 * route framed below its own marks' minzoom is the defect.
 *
 * Booting map.js in jsdom follows tests/js/test_map_route_endpoints.js's
 * pattern — see its header for the rationale.
 */

import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';
import { installRouteRailStub } from './_route_rail_stub.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** Four sampled boundaries bounding three segments: gentle, steep, unknown.
 *
 * The steep one is over 50°, so SNOW-964 names it a no-fall passage. Since
 * SNOW-1019 the map draws no mark for it; the detail sheet's words still
 * name it.
 */
const SLOPE = {
  points: [[7.0, 46.0], [7.0, 46.005], [7.0, 46.01], [7.0, 46.015]],
  angles: [12.0, 52.0, null],
  passages: [{ from: 1, to: 1, m: 25.0, fall_line: 'descending' }],
  // The same steep segment carries a fall-line mark (the ground faces
  // 205°). Since SNOW-1019 the map draws none; the record keeps it.
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
/** Every panBy call, as [offset, options]. */
const panCalls = [];
/** `once` handlers by event, which a test fires by hand. */
const onceHandlers = {};

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
    once: (event, fn) => { (onceHandlers[event] ||= []).push(fn); },
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    panBy: (offset, options) => { panCalls.push([offset, options]); },
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

/** A leg core's opacity: `base`, and from z14 nothing on a sampled route. */
const coreOpacity = (base) => [
  'step', ['zoom'], base, 14, ['case', ['==', ['get', 'sampled'], true], 0, base],
];

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
      { uuid: 'sampled-route', i: 1, climbing: true, sampled: true },
      { uuid: 'sampled-route', i: 2, climbing: false, sampled: true },
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

describe('the slope classes from z14', () => {
  it('take over from a sampled route\'s leg lines at one zoom', () => {
    // An opacity step, not a maxzoom: the switch is per route, and a
    // route the sampler has not reached keeps its leg core at z14.
    for (const id of ['routes-leg-climb', 'routes-leg-descent']) {
      expect(layers.get(id).maxzoom).toBeUndefined();
      expect(layers.get(id).paint['line-opacity']).toEqual(coreOpacity(1));
    }
    for (const id of ['routes-slope-line', 'routes-slope-unknown']) {
      expect(layers.get(id).minzoom).toBe(14);
      expect(layers.get(id).source).toBe('routes-slopes');
    }
    // The casing is the legs' at every zoom, so the legs stay countable.
    expect(layers.get('routes-leg-casing').maxzoom).toBeUndefined();
  });

  it('hold every segment of the owned sampled route, tagged with its leg', () => {
    const data = sources.get('routes-slopes').data;

    expect(data.features.map((f) => [f.properties.uuid, f.properties.i])).toEqual([
      ['sampled-route', 1], ['sampled-route', 2], ['sampled-route', 2],
    ]);
  });

  it('paint a known segment by its class and an unknown one grey', () => {
    expect(layers.get('routes-slope-line').filter).toEqual(['!=', ['get', 'unknown'], true]);
    expect(layers.get('routes-slope-line').paint['line-color']).toEqual(core.colourExpression());
    expect(layers.get('routes-slope-unknown').filter).toEqual(['==', ['get', 'unknown'], true]);
    expect(layers.get('routes-slope-unknown').paint['line-color']).toBe(core.UNKNOWN_COLOUR);
  });

  it('sit over the leg casing and under a pending share', () => {
    const ids = [...layers.keys()];

    expect(ids.indexOf('routes-slope-line')).toBeGreaterThan(ids.indexOf('routes-leg-casing'));
    expect(ids.indexOf('routes-line-pending')).toBeGreaterThan(ids.indexOf('routes-slope-unknown'));
  });

  it('open the route a tapped segment belongs to', () => {
    for (const id of ['routes-slope-line', 'routes-slope-unknown']) {
      fitBoundsOptions.length = 0;
      tapLayer(id, { uuid: 'sampled-route', i: 2, slope_class: 5 });

      expect(rail.last().feature.properties.uuid).toBe('sampled-route');
    }
  });

  it('are reached by the routes overlay switch', () => {
    for (const id of ['routes-slope-line', 'routes-slope-unknown']) {
      expect(window.snowdeskMapState.overlayLayers.routes).toContain(id);
      expect(layers.get(id).layout.visibility).toBe('visible');
    }
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

  it('sit under the endpoint markers', () => {
    const ids = [...layers.keys()];

    expect(ids.indexOf('routes-transition-labels'))
      .toBeGreaterThan(ids.indexOf('routes-transitions'));
    expect(ids.indexOf('routes-transition-labels'))
      .toBeLessThan(ids.indexOf('routes-endpoints'));
  });
});

describe('the marks SNOW-1019 took off the map', () => {
  it('installs no fall-line arrow or passage split, though the record carries them', () => {
    // Rail two's bank ribbon replaced the arrows and drew the no-fall
    // passages as bars; rail two went with SNOW-1065 and neither came back.
    // SLOPE still carries `fall_lines` and `passages`, so this holds the
    // marks off rather than passing for want of data.
    const ids = [...layers.keys(), ...sources.keys()];
    const marks = /fall-line|passage/;

    expect(SLOPE.fall_lines).toHaveLength(1);
    expect(SLOPE.passages).toHaveLength(1);
    expect(ids.filter((id) => marks.test(id))).toEqual([]);
    expect(window.snowdeskMapState.overlayLayers.routes
      .filter((id) => marks.test(id))).toEqual([]);
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
  it('opens the panel with the point already placed where the tap landed', () => {
    projectLngLat = ([lng, lat]) => ({ x: (lng - 7.0) * 10000, y: (46.015 - lat) * 10000 });
    window.pwaRouteRail.close();
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    rail.state.cursor = cursor;
    const opened = rail.state.calls.length;

    // y = 70 is nearest the second segment's middle (y = 75).
    tapLayer('routes-leg-descent', { uuid: 'sampled-route', i: 2, climbing: false }, { x: 2, y: 70 });

    expect(rail.state.calls).toHaveLength(opened + 1);
    expect(cursor.state().index).toBe(1);
    expect(fitBoundsCalls).toEqual([]);
    projectLngLat = () => ({ x: 0, y: 0 });
    rail.state.cursor = null;
  });

  it('opens the route the leg belongs to, from either layer', () => {
    // The leg carries the uuid and nothing else, so the whole route in the
    // rail is proof the uuid resolved back to it.
    tapLeg('routes-leg-descent');
    expect(rail.last().feature.properties.uuid).toBe('sampled-route');

    tapLeg('routes-leg-climb');
    expect(rail.last().feature.properties.uuid).toBe('sampled-route');
  });

  it('leaves the camera where it is: the reader can already see the line', () => {
    // The framing invariants (no zoom cap, deep enough for every mark)
    // moved with the fit to the share-link arrival,
    // tests/js/test_map_route_share.js.
    tapLeg();

    expect(fitBoundsCalls).toEqual([]);
  });

  it('does not query the transition layers, which add no route', () => {
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

describe('no leg selection (2026-10-02)', () => {
  /** The opacity set on one layer. */
  const opacityOf = (id) => layers.get(id).paint['line-opacity'];

  it('paints every leg at one strength, and never re-paints it for a point', () => {
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    rail.state.cursor = cursor;
    tapLeg();
    paintCalls.length = 0;

    cursor.setIndex(2);
    cursor.setIndex(null);

    expect(opacityOf('routes-leg-climb')).toEqual(coreOpacity(1));
    expect(opacityOf('routes-leg-descent')).toEqual(coreOpacity(1));
    expect(opacityOf('routes-leg-casing')).toBe(0.55);
    expect(opacityOf('routes-slope-line')).toBe(1);
    expect(opacityOf('routes-slope-unknown')).toBe(1);
    expect(paintCalls.filter(([, prop]) => prop === 'line-opacity')).toEqual([]);
    rail.state.cursor = null;
  });

  it('has no dimming expression left to paint with', () => {
    expect(legsCore.dimOpacity).toBeUndefined();
  });
});

describe('a sampled route somebody shared', () => {
  it('draws no legs and no markers', () => {
    for (const id of ['route-legs', 'route-transitions']) {
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

describe('the first tap places the point on every kind of route (2026-10-02)', () => {
  afterEach(() => {
    projectLngLat = () => ({ x: 0, y: 0 });
    rail.state.cursor = null;
  });

  it('places it on a pending share, matched by its token', () => {
    // The share's slope record runs along the sampled route's meridian,
    // so its segment middles project to y = 125, 75 and 25.
    projectLngLat = ([lng, lat]) => ({ x: (lng - 7.0) * 10000, y: (46.015 - lat) * 10000 });
    window.pwaRouteRail.close();
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    rail.state.cursor = cursor;

    tapLayer('routes-line-pending', { token: 'tok-pending', pending: true }, { x: 2, y: 70 });

    expect(rail.last().feature.properties.token).toBe('tok-pending');
    expect(cursor.state().index).toBe(1);
  });

  it('places it on a route never sampled, by share of its line, and draws the dot', () => {
    // No slope record: one leg, so one segment, whose middle is half way
    // up the line at 46.505.
    projectLngLat = ([lng, lat]) => ({ x: (lng - 6.0) * 10000, y: (46.51 - lat) * 10000 });
    window.pwaRouteRail.close();
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(1);
    rail.state.cursor = cursor;

    tapLayer('routes-line', { uuid: 'stale-route' }, { x: 1, y: 40 });

    expect(rail.last().feature.properties.uuid).toBe('stale-route');
    expect(cursor.state().index).toBe(0);
    const [dot] = sources.get('route-cursor-point').data.features;
    expect(dot.geometry.coordinates[1]).toBeCloseTo(46.505);
    expect(dot.properties.colour).toBeUndefined();
    cursor.setIndex(null);
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

  /**
   * Open the sampled route on the rail with a fresh cursor, and clear the
   * point the opening tap placed.
   */
  const openSampledRoute = () => {
    const cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    rail.state.cursor = cursor;
    tapLeg();
    cursor.setIndex(null);
    return cursor;
  };

  /** Hover the mouse over a screen point. */
  const hover = (point) => {
    for (const handler of mapStub.handlers.mousemove || []) handler({ point });
  };

  it('draws its layers over the lines, reached by the routes switch', () => {
    const ids = [...layers.keys()];
    const routeLayers = window.snowdeskMapState.overlayLayers.routes;

    const id = 'routes-cursor-point';
    expect(ids.indexOf(id)).toBeGreaterThan(ids.indexOf('routes-line-pending'));
    expect(routeLayers).toContain(id);
    expect(layers.get(id).layout.visibility).toBe('visible');
    expect(layers.get(id).type).toBe('circle');
  });

  it('paints the dot in its feature\'s slope colour, else the route colour (SNOW-1052)', () => {
    const colour = layers.get('routes-cursor-point').paint['circle-color'];

    expect(colour[0]).toBe('coalesce');
    expect(colour[1]).toEqual(['get', 'colour']);
  });

  it('draws the dot as a ring with a clear centre from z14 (SNOW-1064)', () => {
    const paint = layers.get('routes-cursor-point').paint;
    // Filled below z14, clear from it, so the class colour under it shows.
    expect(paint['circle-opacity']).toEqual(['step', ['zoom'], 1, 14, 0]);
    // The ring is the segment's class colour; below z14 the stroke is the halo.
    expect(paint['circle-stroke-color'][3]).toBe(14);
    expect(paint['circle-stroke-color'][4][1]).toEqual(['get', 'colour']);
    expect(paint['circle-stroke-width']).toEqual(['step', ['zoom'], 2, 14, 3]);
    const halo = layers.get('routes-cursor-point-halo');
    expect(halo.minzoom).toBe(14);
    expect(halo.source).toBe('route-cursor-point');
    const ids = [...layers.keys()];
    expect(ids.indexOf('routes-cursor-point-halo')).toBe(ids.indexOf('routes-cursor-point') - 1);
    expect(window.snowdeskMapState.overlayLayers.routes).toContain('routes-cursor-point-halo');
  });

  it('draws no selection stretch (SNOW-1052)', () => {
    expect(sources.has('route-cursor-selection')).toBe(false);
    expect(layers.has('routes-cursor-selection')).toBe(false);
    expect(layers.has('routes-cursor-selection-casing')).toBe(false);
  });

  it('draws the cursor index as a dot on its segment, and hides it on null', () => {
    const cursor = openSampledRoute();

    cursor.setIndex(1);
    const [dot] = sources.get('route-cursor-point').data.features;
    expect(dot.geometry.coordinates[1]).toBeCloseTo(46.0075);
    expect(dot.properties.colour).toMatch(/^#[0-9a-f]{6}$/);

    cursor.setIndex(null);
    expect(sources.get('route-cursor-point').data.features).toEqual([]);
    rail.state.cursor = null;
  });

  it('moves the point to a tap on the open route', () => {
    projectLngLat = alongTheRoute;
    const cursor = openSampledRoute();
    const opened = rail.state.calls.length;
    cursor.setIndex(0);

    // y = 70 is nearest the second segment's middle.
    tapLayer('routes-leg-descent', { uuid: 'sampled-route', i: 2, climbing: false }, { x: 2, y: 70 });

    expect(cursor.state().index).toBe(1);
    // Not a second first tap: no re-framing, and the rail is not reopened.
    expect(fitBoundsCalls).toEqual([]);
    expect(rail.state.calls).toHaveLength(opened);
    projectLngLat = () => ({ x: 0, y: 0 });
    rail.state.cursor = null;
  });

  it('leaves the point where it is on a mouse hover over the line (2026-10-02)', () => {
    projectLngLat = alongTheRoute;
    const cursor = openSampledRoute();

    hover({ x: 5, y: 120 });
    expect(cursor.state().index).toBeNull();

    cursor.setIndex(2);
    hover({ x: 0, y: 75 });
    expect(cursor.state().index).toBe(2);
    projectLngLat = () => ({ x: 0, y: 0 });
    cursor.setIndex(null);
    rail.state.cursor = null;
  });

  it('repaints the dot when a basemap swap rebuilds the layers', async () => {
    const cursor = openSampledRoute();
    cursor.setIndex(1);

    for (const id of [...layers.keys()]) layers.delete(id);
    for (const id of [...sources.keys()]) sources.delete(id);
    for (const handler of mapStub.handlers.styledata || []) await handler();

    expect(sources.get('route-cursor-point').data.features).toHaveLength(1);
    cursor.setIndex(null);
    rail.state.cursor = null;
  });

  it('gives the leader line the cursor dot\'s screen point', () => {
    projectLngLat = alongTheRoute;
    const cursor = openSampledRoute();
    expect(window.pwaRouteCursorMap.point()).toBeNull();

    cursor.setIndex(1);
    const point = window.pwaRouteCursorMap.point();

    // The second segment's middle, 46.0075, projects to y = 75.
    expect(point.x).toBeCloseTo(0);
    expect(point.y).toBeCloseTo(75);
    projectLngLat = () => ({ x: 0, y: 0 });
    cursor.setIndex(null);
    rail.state.cursor = null;
  });

  it('stops answering a tap once the rail has let the cursor go', () => {
    projectLngLat = alongTheRoute;
    const cursor = openSampledRoute();
    rail.state.cursor = null;

    tapLayer('routes-leg-descent', { uuid: 'sampled-route', i: 2, climbing: false }, { x: 0, y: 125 });

    expect(cursor.state().index).toBeNull();
    projectLngLat = () => ({ x: 0, y: 0 });
  });
});

describe('keeping the cursor dot in view (SNOW-1019)', () => {
  // A 375 × 812 phone canvas with the route panel pinned to its top
  // (SNOW-1068), its bottom edge at 250: the visible map is y 262 (the
  // panel's foot plus the 12 px inset) to 812.
  const CANVAS = { left: 0, top: 0, right: 375, bottom: 812, width: 375, height: 812 };
  const RAIL = { left: 12, top: 12, right: 363, bottom: 250, width: 351, height: 238 };
  // The three segment middles project to y = 125, 75 and 25 — all under
  // the panel — at x = 200.
  const behindTheRail = ([lng, lat]) => ({
    x: 200 + (lng - 7.0) * 10000,
    y: (46.015 - lat) * 10000,
  });

  /** Land the pan in flight, as MapLibre's moveend would. */
  const landPan = () => {
    const handlers = onceHandlers.moveend || [];
    onceHandlers.moveend = [];
    for (const handler of handlers) handler();
  };

  let mapSpy;
  let railSpy;
  let cursor;

  /** Tap the open route's second leg at a screen point. */
  const tapLeg2 = (point) => {
    tapLayer('routes-leg-descent', { uuid: 'sampled-route', i: 2, climbing: false }, point);
  };

  const setUp = () => {
    projectLngLat = behindTheRail;
    mapSpy = vi.spyOn(document.getElementById('map'), 'getBoundingClientRect')
      .mockReturnValue(CANVAS);
    railSpy = vi.spyOn(rail.element, 'getBoundingClientRect').mockReturnValue(RAIL);
    cursor = globalThis.pwaRouteCursorCore.createRouteCursor(3);
    rail.state.cursor = cursor;
    tapLeg();
    cursor.setIndex(null);
    landPan();
    panCalls.length = 0;
  };

  const tearDown = () => {
    landPan();
    cursor.setIndex(null);
    rail.state.cursor = null;
    projectLngLat = () => ({ x: 0, y: 0 });
    mapSpy.mockRestore();
    railSpy.mockRestore();
  };

  it('pans a dot out from under the panel, keeping the zoom', () => {
    setUp();

    cursor.setIndex(0);

    expect(panCalls).toHaveLength(1);
    const [[dx, dy], options] = panCalls[0];
    // 125 → 250 + 12 + 24.
    expect(dx).toBe(0);
    expect(dy).toBeCloseTo(-161);
    expect(options).not.toHaveProperty('zoom');
    tearDown();
  });

  it('makes one pan for a scrub, not a queue', () => {
    setUp();

    cursor.setIndex(0);
    cursor.setIndex(1);
    cursor.setIndex(2);

    expect(panCalls).toHaveLength(1);
    // The pan lands: the index that arrived mid-pan is checked once more.
    landPan();
    expect(panCalls).toHaveLength(2);
    tearDown();
  });

  it('does not pan for an index the map wrote under the pointer', () => {
    setUp();

    tapLeg2({ x: 200, y: 75 });

    expect(cursor.state().index).toBe(1);
    expect(panCalls).toEqual([]);
    tearDown();
  });

  it('does not pan while the reader drags the map', () => {
    setUp();
    for (const handler of mapStub.handlers.dragstart || []) handler();

    cursor.setIndex(0);

    expect(panCalls).toEqual([]);
    for (const handler of mapStub.handlers.dragend || []) handler();
    tearDown();
  });

  it('pans for a map-written index once the panel grows over it', () => {
    // A tap on the line wrote the index where the reader could see it;
    // then the panel grew (a point header, a title wrapping) over that
    // place. A layout change is not a tap, so this one pans.
    setUp();
    projectLngLat = ([lng, lat]) => ({
      x: 200 + (lng - 7.0) * 10000,
      y: 300 + (46.015 - lat) * 10000,
    });
    // Segment 1's middle projects to y = 375, inside 262–812.
    tapLeg2({ x: 200, y: 375 });
    expect(cursor.state().index).toBe(1);
    expect(panCalls).toEqual([]);

    railSpy.mockReturnValue({ ...RAIL, bottom: 400, height: 388 });
    document.dispatchEvent(new CustomEvent('snowdesk:route-rail-resized'));

    expect(panCalls).toHaveLength(1);
    // 375 → 400 + 12 + 24.
    expect(panCalls[0][0][1]).toBeCloseTo(-61);
    tearDown();
  });

  it('gives the leader no map stop while the dot is under the panel', () => {
    setUp();
    for (const handler of mapStub.handlers.dragstart || []) handler();

    cursor.setIndex(0);

    expect(window.pwaRouteCursorMap.point()).toBeNull();
    for (const handler of mapStub.handlers.dragend || []) handler();
    tearDown();
  });
});
