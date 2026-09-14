/*
 * tests/js/test_map_route_slope_layers.js — the slope-coloured route line,
 * wired through map.js (SNOW-910).
 *
 * tests/js/test_route_slope_core.js covers the buckets and the segment
 * geometry. What is left is the wiring, and three parts of it fail
 * SILENTLY — every layer still exists, nothing throws, and the map is
 * simply wrong:
 *
 *   - DOUBLE PAINTING. A sampled route is drawn segment by segment by the
 *     two layers here, so it has to LEAVE `routes-line`. Get that filter
 *     wrong and the flat fuchsia line is still underneath, showing at
 *     every butt-capped join. Cheap to see on a screen and easy to miss in
 *     a diff.
 *   - THE UNKNOWN SPLIT. `line-dasharray` is not data-driven, so the
 *     unknowns need a layer of their own — and the coloured layer's `step`
 *     needs a number. If an unknown segment reached that expression it
 *     would fall on the first stop, which is the GENTLE colour: unsurveyed
 *     ground painted as safe, the one outcome this feature exists to
 *     prevent.
 *   - THE TAP. `routes-line` no longer draws a sampled route, so the two
 *     new layers have to be in the marker-exclusion set or every coloured
 *     route becomes untappable and the tap falls through to the region
 *     underneath.
 *
 * Booting map.js in jsdom follows tests/js/test_map_route_endpoints.js's
 * pattern — see its header for the rationale.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** Four sampled boundaries bounding three segments: gentle, steep, unknown.
 *
 * The steep one is over 50°, so SNOW-964 names it a no-fall passage —
 * which is what the two passage layers below are filtered on. The
 * unknown one is deliberately NOT in the list: the server never names
 * unsurveyed ground, and the layer order here is what proves a segment
 * cannot be drawn dashed and split at once.
 */
const SLOPE = {
  points: [[7.0, 46.0], [7.0, 46.005], [7.0, 46.01], [7.0, 46.015]],
  angles: [12.0, 52.0, null],
  passages: [{ from: 1, to: 1, m: 25.0, fall_line: 'descending' }],
};

/** One sampled route and one that has never been sampled. */
const ROUTES_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[7.0, 46.0, 1500], [7.0, 46.015, 2100]],
      },
      properties: {
        uuid: 'sampled-route',
        name: 'Coloured',
        // The bbox activateRoute frames the track with. A slope segment
        // carries NONE of this, which is what makes it the evidence that
        // a tap resolved back to the whole route.
        bounds: [7.0, 46.0, 7.0, 46.015],
        slope: SLOPE,
      },
    },
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[8.0, 47.0], [8.0, 47.02]],
      },
      properties: { uuid: 'flat-route', name: 'Never sampled' },
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
        // sampled share DOES arrive carrying a slope record — which is
        // what makes "the chart declines to use it" a real assertion.
        token: 'tok-pending',
        pending: true,
        name: 'Shared with me',
        bounds: [9.0, 45.0, 9.0, 45.015],
        slope: SLOPE,
      },
    },
  ],
};

/** Every DOM node handed to a popup, newest last. */
const popupNodes = [];

/** Layer definitions as map.js added them, by id. */
const layers = new Map();
/** Source definitions as map.js added them, by id. */
const sources = new Map();
/** Every bbox map.js asked the camera to frame. */
const fitBoundsCalls = [];
/** What the next queryRenderedFeatures call should answer, by layer id. */
let queryAnswer = () => [];

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
    setPaintProperty: () => {},
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
    fitBounds: (bbox) => { fitBoundsCalls.push(bbox); },
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 7, lat: 46 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
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
        setDOMContent: (node) => {
          popupNodes.push(node);
          return popup;
        },
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
    <section id="map-route-slope-section" hidden></section>`;
}

let mapStub;
let core;

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
  // SNOW-960: the popup's chart. Without it `appendElevationProfile`
  // returns early and the two profile assertions below would pass
  // vacuously against a popup carrying no <svg> at all.
  await import('../../static/js/elevation_profile_core.js');
  core = globalThis.pwaRouteSlopeCore;
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();

  await window.pwaRoutesOverlay.show();
});

describe('the slope segment source', () => {
  it('is installed beside the routes source', () => {
    expect(sources.has('route-slopes')).toBe(true);
  });

  it('holds one feature per segment of the sampled route only', () => {
    // Three segments off the one sampled route; the never-sampled route
    // contributes nothing.
    const data = sources.get('route-slopes').data;

    expect(data.features).toHaveLength(3);
    expect(data.features.every((f) => f.properties.uuid === 'sampled-route'))
      .toBe(true);
  });
});

describe('routes-line', () => {
  it('drops a sampled route, so it is not painted flat underneath', () => {
    // The double-painting guard. `has` and not a test on the value: the
    // server omits the property entirely for a route nothing has sampled.
    expect(layers.get('routes-line').filter).toEqual([
      'all', ['!=', ['get', 'pending'], true], ['!', ['has', 'slope']],
    ]);
  });

  it('still draws a route that has never been sampled', () => {
    // The flat line is not retired — most routes are in that state for
    // some window after upload, and older ones until the backfill runs.
    expect(layers.get('routes-line').filter[2]).toEqual(['!', ['has', 'slope']]);
  });

  it('leaves the casing alone — it is one colour and never varies', () => {
    expect(layers.get('routes-line-casing').filter).toBeUndefined();
  });

  it('leaves the pending line alone — its dash carries a different fact', () => {
    expect(layers.get('routes-line-pending').filter)
      .toEqual(['==', ['get', 'pending'], true]);
  });
});

describe('routes-slope-line', () => {
  it('excludes the unknown segments from its step expression', () => {
    // An unknown reaching `step` would fall on its first stop, which is
    // the gentle colour — unsurveyed ground painted as safe.
    expect(layers.get('routes-slope-line').filter)
      .toEqual(['!=', ['get', 'unknown'], true]);
  });

  it('paints from the core\'s buckets, in order', () => {
    const expression = layers.get('routes-slope-line').paint['line-color'];

    expect(expression[0]).toBe('step');
    expect(expression[1]).toEqual(['get', 'slope_class']);
    expect(expression[2]).toBe(core.CLASSES[0].hex);
    // Then (stop, colour) per class after the first: 1 → CLASSES[1], …
    for (let i = 1; i < core.CLASSES.length; i += 1) {
      expect(expression[1 + i * 2]).toBe(i);
      expect(expression[2 + i * 2]).toBe(core.CLASSES[i].hex);
    }
  });

  it('is the same width as the flat line it replaces', () => {
    // A sampled route and an unsampled one are the same object and must
    // read as the same weight of thing.
    expect(layers.get('routes-slope-line').paint['line-width'])
      .toEqual(layers.get('routes-line').paint['line-width']);
  });

  it('uses butt caps, so one segment\'s colour does not smear into the next', () => {
    expect(layers.get('routes-slope-line').layout['line-cap']).toBe('butt');
  });
});

describe('routes-slope-unknown', () => {
  it('draws the unknown segments and only those', () => {
    expect(layers.get('routes-slope-unknown').filter)
      .toEqual(['==', ['get', 'unknown'], true]);
  });

  it('is dashed, which is why it is a layer of its own', () => {
    expect(layers.get('routes-slope-unknown').paint['line-dasharray']).toBeTruthy();
  });

  it('is painted off the steepness scale entirely', () => {
    const colour = layers.get('routes-slope-unknown').paint['line-color'];

    expect(colour).toBe(core.UNKNOWN_COLOUR);
    expect(core.CLASSES.map((c) => c.hex)).not.toContain(colour);
  });
});

describe('the routes overlay switch', () => {
  it('reaches both new layers', () => {
    // Otherwise turning routes off leaves the coloured half on screen.
    const routeLayers = window.snowdeskMapState.overlayLayers.routes;

    expect(routeLayers).toContain('routes-slope-line');
    expect(routeLayers).toContain('routes-slope-unknown');
  });

  it('installs them at the overlay\'s current visibility', () => {
    expect(layers.get('routes-slope-line').layout.visibility).toBe('visible');
    expect(layers.get('routes-slope-unknown').layout.visibility).toBe('visible');
  });
});

describe('the no-fall passage layers (SNOW-964)', () => {
  /** The ids in the order installRoutesLayer added them. */
  const order = () => [...layers.keys()];

  it('draw off the same source as the colour they mark', () => {
    // Not a second source: a mark on different geometry from the colour
    // it marks is a mark that can disagree with it.
    expect(layers.get('routes-passage-edge').source).toBe('route-slopes');
    expect(layers.get('routes-passage-core').source).toBe('route-slopes');
  });

  it('draw only the segments the server named', () => {
    expect(layers.get('routes-passage-edge').filter)
      .toEqual(['==', ['get', 'passage'], true]);
    expect(layers.get('routes-passage-core').filter)
      .toEqual(['==', ['get', 'passage'], true]);
  });

  it('sandwich the two slope layers', () => {
    // MapLibre paints later layers over earlier ones, so the edge has to
    // be UNDER the coloured line and the core OVER both — that is what
    // makes the mark read as a split in the line rather than as a second
    // line beside it.
    const ids = order();

    expect(ids.indexOf('routes-passage-edge'))
      .toBeLessThan(ids.indexOf('routes-slope-line'));
    expect(ids.indexOf('routes-passage-core'))
      .toBeGreaterThan(ids.indexOf('routes-slope-unknown'));
  });

  it('paints the edge with the band colour, not a flat ink', () => {
    // A passage grows outward through the 45–50 band, so a flat
    // `slope-50` edge would report 47° ground as over 50.
    expect(layers.get('routes-passage-edge').paint['line-color'])
      .toEqual(layers.get('routes-slope-line').paint['line-color']);
  });

  it('paints the core off the steepness scale', () => {
    expect(layers.get('routes-passage-core').paint['line-color'])
      .toBe(core.PASSAGE_CORE_COLOUR);
  });

  it('keeps the edge at or inside the casing at every zoom stop', () => {
    // The casing frames the mark over a pale basemap, and it draws off
    // the `routes` source — widening IT to suit a wider mark would
    // thicken every route on the map.
    const edge = layers.get('routes-passage-edge').paint['line-width'];
    const casing = layers.get('routes-line-casing').paint['line-width'];

    for (let i = 3; i < edge.length; i += 2) {
      expect(edge[i + 1]).toBeLessThanOrEqual(casing[i + 1]);
    }
  });

  it('keeps the core narrower than the edge at every zoom stop', () => {
    // Or there is no band colour left either side of it, and the mark
    // reads as a white line rather than as a split one.
    const edge = layers.get('routes-passage-edge').paint['line-width'];
    const inner = layers.get('routes-passage-core').paint['line-width'];

    for (let i = 3; i < edge.length; i += 2) {
      expect(inner[i + 1]).toBeLessThan(edge[i + 1]);
    }
  });

  it('uses butt caps, like every other per-segment layer', () => {
    expect(layers.get('routes-passage-edge').layout['line-cap']).toBe('butt');
    expect(layers.get('routes-passage-core').layout['line-cap']).toBe('butt');
  });

  it('leaves routes-slope-line untouched', () => {
    // The passage keeps the colour and the weight of the ground under
    // it: the split is added to the line, it does not replace it.
    expect(layers.get('routes-slope-line').paint['line-width'])
      .toEqual(layers.get('routes-line').paint['line-width']);
    expect(layers.get('routes-slope-line').filter)
      .toEqual(['!=', ['get', 'unknown'], true]);
  });

  it('is reached by the routes overlay switch', () => {
    expect(layers.get('routes-passage-edge').layout.visibility).toBe('visible');
    expect(layers.get('routes-passage-core').layout.visibility).toBe('visible');
  });

  it('never marks the unsurveyed segment', () => {
    // The dash interrupts the line ALONG its length and the split
    // divides it ACROSS its width; the two are orthogonal and must never
    // land on one segment.
    const marked = sources.get('route-slopes').data.features
      .filter((f) => f.properties.passage);

    expect(marked).toHaveLength(1);
    expect(marked[0].properties).not.toHaveProperty('unknown');
  });
});

describe('tapping a coloured route', () => {
  /** Fire the map-level click, with the slope layers answering the query. */
  function tapSlopeSegment(layerId) {
    fitBoundsCalls.length = 0;
    queryAnswer = (options) => (
      (options.layers || []).includes(layerId)
        ? [{
          layer: { id: layerId },
          // Exactly what a segment carries: the uuid and its class. No
          // name, no figures, no bbox.
          properties: { uuid: 'sampled-route', slope_class: 1 },
        }]
        : []
    );
    for (const handler of mapStub.handlers.click || []) {
      handler({ point: { x: 10, y: 10 }, lngLat: { lng: 7, lat: 46.01 } });
    }
    queryAnswer = () => [];
  }

  it('opens the route the segment belongs to', () => {
    // The regression this guards: `routes-line` no longer draws a sampled
    // route, so if these layers were not in the exclusion set the tap
    // would fall straight through to the region underneath.
    tapSlopeSegment('routes-slope-line');

    // The bbox is on the ROUTE feature and not on the segment, so framing
    // it is proof the uuid resolved back to the whole route.
    expect(fitBoundsCalls).toEqual([[[7.0, 46.0], [7.0, 46.015]]]);
  });

  it('opens it from an unknown segment too', () => {
    tapSlopeSegment('routes-slope-unknown');

    expect(fitBoundsCalls).toEqual([[[7.0, 46.0], [7.0, 46.015]]]);
  });

  it('does not query the passage layers, which add no geometry', () => {
    // `routes-slope-line` still draws every passage, with the same
    // coordinates and the same uuid, so nothing left the tap path the
    // way `routes-line` did for a sampled route in SNOW-910. Querying
    // them as well would return the same route twice more per tap.
    const queried = [];
    queryAnswer = (options) => {
      queried.push(...(options.layers || []));
      return [];
    };
    for (const handler of mapStub.handlers.click || []) {
      handler({ point: { x: 10, y: 10 }, lngLat: { lng: 7, lat: 46.01 } });
    }
    queryAnswer = () => [];

    expect(queried).toContain('routes-slope-line');
    expect(queried).not.toContain('routes-passage-edge');
    expect(queried).not.toContain('routes-passage-core');
  });

  it('colours the profile of the route it opens', () => {
    popupNodes.length = 0;
    tapSlopeSegment('routes-slope-line');

    const strokes = [...popupNodes.at(-1).querySelectorAll('path')]
      .map((path) => path.getAttribute('stroke'));

    expect(strokes.some((stroke) => /--color-slope-/.test(stroke))).toBe(true);
  });
});

describe('tapping a sampled route somebody shared', () => {
  it('leaves its profile uncoloured', () => {
    // Codex flagged this on #933. The colouring was briefly extended to
    // pending shares on the grounds that the chart is not the line, and
    // the line's teal dash already carries "not yours yet". But the
    // six-swatch key and its link to the slope caveats live on the MAP
    // legend, and `anyRouteSampled` excludes pending routes from the
    // condition that reveals it — so a visitor who follows a sampled
    // share and owns no sampled route of their own would meet the whole
    // palette with nothing on screen to say what it means, on the one
    // path where the reader is newest to the feature. A pending share is
    // not coloured, anywhere.
    popupNodes.length = 0;
    queryAnswer = (options) => (
      (options.layers || []).includes('routes-line-pending')
        ? [{
          layer: { id: 'routes-line-pending' },
          properties: {
            token: 'tok-pending',
            pending: true,
            name: 'Shared with me',
            bounds: JSON.stringify([9.0, 45.0, 9.0, 45.015]),
          },
        }]
        : []
    );
    for (const handler of mapStub.handlers.click || []) {
      handler({ point: { x: 10, y: 10 }, lngLat: { lng: 9, lat: 45.01 } });
    }
    queryAnswer = () => [];

    const strokes = [...popupNodes.at(-1).querySelectorAll('path')]
      .map((path) => path.getAttribute('stroke'));

    // A chart was drawn — the share carries elevation — and none of it
    // names a slope token.
    expect(strokes.length).toBeGreaterThan(0);
    for (const stroke of strokes) expect(stroke).not.toMatch(/--color-slope-/);
  });
});

describe('the legend key', () => {
  it('is revealed once a sampled route is drawn', () => {
    expect(document.getElementById('map-route-slope-section').hidden).toBe(false);
  });
});
