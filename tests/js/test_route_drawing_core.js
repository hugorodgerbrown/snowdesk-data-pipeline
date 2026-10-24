/*
 * tests/js/test_route_drawing_core.js — the one drawing every map draws a
 * saved route with (SNOW-1212).
 *
 * The map page, the trip page and the meeting picker all build their route
 * layers from this module, so what is asserted here holds on all three:
 * the legs below ROUTE_SLOPE_MINZOOM and the slope classes from it, the
 * flat line only for a route with no drawable legs, and the markers. The
 * map page's wiring of it — its cursor between the two
 * groups — stays in tests/js/test_map_route_leg_layers.js.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/route_markers_core.js';
import '../../static/js/slope_classes_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/route_legs_core.js';
import '../../static/js/route_drawing_core.js';

const core = self.pwaRouteDrawingCore;

/** A legged, sampled route: up one segment, down the next. */
const ROUTE = {
  type: 'Feature',
  geometry: {
    type: 'LineString',
    coordinates: [[7.0, 46.0, 1500], [7.0, 46.005, 2100], [7.0, 46.01, 1900]],
  },
  properties: {
    slope: { points: [[7.0, 46.0], [7.0, 46.005], [7.0, 46.01]], angles: [12, 34] },
    legs: [
      { i: 1, from: 0, to: 0, climbing: true, point_from: 0, point_to: 1 },
      { i: 2, from: 1, to: 1, climbing: false, point_from: 1, point_to: 2 },
    ],
  },
};

const COLLECTION = { type: 'FeatureCollection', features: [ROUTE] };

/** The options a trip page passes. */
function options(extra) {
  return Object.assign({ prefix: 'p', sources: core.sourceIds('p') }, extra || {});
}

/**
 * A MapLibre stand-in that records what it is given.
 *
 * @param {Array<object>} [styleLayers] The style's own layers.
 * @returns {object}
 */
function recordingMap(styleLayers) {
  const sources = {};
  const layers = [];
  const images = [];
  return {
    sources,
    layers,
    images,
    getSource: (id) => sources[id] || null,
    addSource: (id, def) => { sources[id] = def; },
    addLayer: (def) => layers.push(def),
    hasImage: (id) => images.some((image) => image.id === id),
    addImage: (id, data, ratio) => images.push({ id, data, ratio }),
    getStyle: () => ({ layers: styleLayers || [] }),
  };
}

describe('lineLayers', () => {
  it('stacks the flat line, the legs and the slope classes, bottom first', () => {
    expect(core.lineLayers(options()).map((layer) => layer.id)).toEqual([
      'p-line-casing',
      'p-line',
      'p-leg-casing',
      'p-leg-climb',
      'p-leg-descent',
      'p-slope-line',
      'p-slope-unknown',
    ]);
  });

  it('switches to the slope classes at ROUTE_SLOPE_MINZOOM', () => {
    const layers = core.lineLayers(options());
    const slope = layers.filter((layer) => layer.id.startsWith('p-slope'));

    expect(core.ROUTE_SLOPE_MINZOOM).toBe(14);
    for (const layer of slope) expect(layer.minzoom).toBe(14);
    const climb = layers.find((layer) => layer.id === 'p-leg-climb');
    expect(climb.paint['line-opacity']).toEqual(core.legCoreOpacity(1));
  });

  it('fades a sampled leg at z14 and keeps an unsampled one', () => {
    expect(core.legCoreOpacity(1)).toEqual([
      'step', ['zoom'], 1,
      14, ['case', ['==', ['get', 'sampled'], true], 0, 1],
    ]);
  });

  it('dashes the climb and not the descent', () => {
    const layers = core.lineLayers(options());
    const climb = layers.find((layer) => layer.id === 'p-leg-climb');
    const descent = layers.find((layer) => layer.id === 'p-leg-descent');

    expect(climb.paint['line-dasharray']).toEqual([2, 1.5]);
    expect(descent.paint).not.toHaveProperty('line-dasharray');
  });

  it('keeps a legged route off the flat line by default', () => {
    const [casing, line] = core.lineLayers(options());

    expect(casing.filter).toEqual(['!', ['has', 'legs']]);
    expect(line.filter).toEqual(['!', ['has', 'legs']]);
  });

  it('takes the caller\'s flat filters, and none at all when given null', () => {
    const [casing, line] = core.lineLayers(options({
      casingFilter: ['==', 1, 1],
      lineFilter: null,
    }));

    expect(casing.filter).toEqual(['==', 1, 1]);
    expect(line).not.toHaveProperty('filter');
  });

  it('adds every layer hidden when asked to', () => {
    const layers = core.lineLayers(options({ visible: false }))
      .concat(core.markLayers(options({ visible: false })));

    for (const layer of layers) expect(layer.layout.visibility).toBe('none');
  });
});

describe('markLayers', () => {
  it('stacks the transitions, their numbers and the end markers', () => {
    const layers = core.markLayers(options({ textFont: ['Inter Regular'] }));

    expect(layers.map((layer) => layer.id))
      .toEqual(['p-transitions', 'p-transition-labels', 'p-endpoints']);
    expect(layers[0].minzoom).toBe(core.TRANSITION_MINZOOM);
    expect(layers[1].layout['text-font']).toEqual(['Inter Regular']);
    expect(layers[2].minzoom).toBe(core.ENDPOINT_MINZOOM);
  });
});

describe('sourceData', () => {
  it('cuts the route into its legs, transitions and ends', () => {
    const data = core.sourceData(COLLECTION);

    expect(data.legs.features).toHaveLength(2);
    expect(data.transitions.features).toHaveLength(1);
    expect(data.endpoints.features).toHaveLength(2);
    expect(data.slopes.features).toHaveLength(2);
    expect(data.route.features[0].properties).toHaveProperty('legs');
  });

  it('strips legs it cannot draw, so the flat line keeps the route', () => {
    const broken = structuredClone(ROUTE);
    delete broken.properties.legs[1].point_to;

    const data = core.sourceData({ type: 'FeatureCollection', features: [broken] });

    expect(data.legs.features).toEqual([]);
    expect(data.route.features[0].properties).not.toHaveProperty('legs');
  });

  it('answers empty collections for nothing at all', () => {
    const data = core.sourceData(null);

    for (const key of Object.keys(data)) expect(data[key].features).toEqual([]);
  });
});

describe('styleTextFont', () => {
  it('takes the first literal stack a symbol layer names', () => {
    const font = core.styleTextFont({
      layers: [
        { type: 'line' },
        { type: 'symbol', layout: { 'text-font': ['get', 'font'] } },
        { type: 'symbol', layout: { 'text-font': ['Frutiger Regular'] } },
      ],
    });

    expect(font).toEqual(['Frutiger Regular']);
  });

  it('falls back to Noto Sans for a style with no labels', () => {
    expect(core.styleTextFont(null)).toEqual(['Noto Sans Regular']);
  });
});

describe('install', () => {
  it('adds the five sources and every layer under one prefix', () => {
    const map = recordingMap([{ type: 'symbol', layout: { 'text-font': ['Inter Regular'] } }]);

    const ids = core.install(map, COLLECTION, { prefix: 'trip-route' });

    expect(Object.keys(map.sources)).toEqual([
      'trip-route', 'trip-route-legs', 'trip-route-slopes',
      'trip-route-transitions', 'trip-route-endpoints',
    ]);
    expect(ids).toEqual(core.sourceIds('trip-route'));
    expect(map.layers).toHaveLength(10);
    const labels = map.layers.find((layer) => layer.id === 'trip-route-transition-labels');
    expect(labels.layout['text-font']).toEqual(['Inter Regular']);
  });

  it('registers the two end markers once, at the core pixel ratio', () => {
    const map = recordingMap();

    core.install(map, COLLECTION, { prefix: 'a' });
    core.install(map, COLLECTION, { prefix: 'b' });

    expect(map.images.map((image) => image.id)).toEqual([core.START_ICON, core.END_ICON]);
    expect(map.images[0].ratio).toEqual({ pixelRatio: self.pwaRouteMarkersCore.PIXEL_RATIO });
  });

  it('is free to call again on a style reload', () => {
    const map = recordingMap();

    core.install(map, COLLECTION, { prefix: 'trip-route' });
    core.install(map, COLLECTION, { prefix: 'trip-route' });

    expect(map.layers).toHaveLength(10);
  });
});
