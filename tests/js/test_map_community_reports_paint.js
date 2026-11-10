/*
 * tests/js/test_map_community_reports_paint.js — the "On the ground" pins
 * the reader wrote are drawn blue (SNOW-1480), wired end to end through
 * map.js's installCommunityReportsLayer.
 *
 * The server marks each report `mine` for its author; map.js's paint
 * expressions turn that into colour, state by state:
 *
 *   - a live or past-day pin of yours is filled blue under a white mark;
 *   - a recent pin of yours keeps its white fill, with a blue ring and a
 *     blue mark;
 *   - a live pin's halo and age are blue for yours;
 *   - a cluster keeps the family colours, whoever's reports are in it;
 *   - everyone else's pins keep their family colours.
 *
 * Paint is data, so the bundle is booted against a MapLibre stub that keeps
 * every `addLayer` definition, and each expression is evaluated here for a
 * feature's properties by a small reading of the few operators it uses.
 * Then the offline cache: the payload kept for whoever opens the map on
 * this device carries no `mine`, while the live source still does.
 *
 * Booted as tests/js/test_map_mode_suppression.js boots it.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const TODAY = '2026-10-29';

/** The payload the community-reports fetch answers with: one of yours. */
const REPORTS = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [7.5, 46.1] },
    properties: {
      submission: '0b6f0000-0000-4000-8000-000000000000',
      types: ['POWDER'],
      type_labels: ['Powder'],
      family: 'SURFACE',
      observed_at: `${TODAY}T09:00:00+00:00`,
      observed_on: TODAY,
      region_id: null,
      region_name: null,
      mine: true,
    },
  }],
};

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/**
 * Read a MapLibre expression for one feature's properties — only the
 * operators the community-reports layers use.
 *
 * @param {*} expr
 * @param {Object<string, *>} props
 * @returns {*}
 */
function evaluate(expr, props) {
  if (!Array.isArray(expr)) return expr;
  const [op, ...args] = expr;
  switch (op) {
    case 'get': return props[args[0]] ?? null;
    case 'has': return args[0] in props;
    case '!': return !evaluate(args[0], props);
    case '==': return evaluate(args[0], props) === evaluate(args[1], props);
    case '>': return evaluate(args[0], props) > evaluate(args[1], props);
    case 'all': return args.every((a) => evaluate(a, props));
    case 'boolean': {
      for (let i = 0; i < args.length - 1; i += 1) {
        const v = evaluate(args[i], props);
        if (typeof v === 'boolean') return v;
      }
      return evaluate(args.at(-1), props);
    }
    case 'case': {
      for (let i = 0; i < args.length - 1; i += 2) {
        if (evaluate(args[i], props)) return evaluate(args[i + 1], props);
      }
      return evaluate(args.at(-1), props);
    }
    case 'match': {
      const input = evaluate(args[0], props);
      for (let i = 1; i < args.length - 1; i += 2) {
        if (args[i] === input) return evaluate(args[i + 1], props);
      }
      return evaluate(args.at(-1), props);
    }
    default: throw new Error(`unsupported expression operator: ${op}`);
  }
}

/** A MapLibre stub keeping every layer definition and source added. */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const sources = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => ((layers.get(id) || {}).layout || {})[prop],
    getPaintProperty: (id, prop) => ((layers.get(id) || {}).paint || {})[prop],
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => (sources.has(id) ? { setData: () => {} } : null),
    addSource: (id, def) => { sources.set(id, def); },
    addLayer: (def) => { layers.set(def.id, def); },
    removeLayer: (id) => { layers.delete(id); },
    removeSource: (id) => { sources.delete(id); },
    moveLayer: () => {},
    setLayoutProperty: () => {},
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
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 8, lat: 46.5 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 8, lat: 46.5 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
    layers,
    sources,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      return { setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }), remove: () => {} };
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/** The DOM map.js's boot reads, with the reports eligible and a today. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-community-reports-url="/api/community-reports.geojson"
         data-community-reports-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2027-05-31"></div>
    <div id="season-scrubber" data-today="${TODAY}" data-today-pct="50"
         data-season-start="2026-10-01" data-season-end="2027-05-31" data-state="ready">
      <div class="season-scrubber-track"><div class="season-scrubber-thumb"></div></div>
      <div class="season-scrubber-loading"></div>
    </div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/** Let the overlay-load handler's fetch settle. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

let mapStub;
const putOverlay = vi.fn(async () => {});

beforeAll(async () => {
  localStorage.clear();
  history.replaceState(null, '', '/map/');
  buildFixture();
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  window.pwaMapOverlayCache = { putOverlay, getOverlay: async () => null };
  vi.stubGlobal('fetch', vi.fn((url) => {
    const body = String(url).includes('community-reports')
      ? structuredClone(REPORTS)
      : EMPTY_FC;
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }));
  mapStub = stubMapLibre();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  await settle();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  history.replaceState(null, '', '/');
  delete globalThis.maplibregl;
  delete window.pwaMapOverlayCache;
});

const core = () => window.pwaCommunityReportsCore;

/** A layer's paint property, read for one feature's properties. */
function paint(layerId, prop, props) {
  const def = mapStub.layers.get(layerId);
  expect(def, `${layerId} installed`).toBeTruthy();
  return evaluate(def.paint[prop], props);
}

/** One unclustered report's properties. */
const pin = (state, mine, family = 'SURFACE') => ({ family, _state: state, mine });

describe('your own pins are blue (SNOW-1480)', () => {
  it('fills a live or past-day pin of yours blue, under a white mark', () => {
    for (const state of ['live', 'day']) {
      expect(paint('community-reports-point', 'circle-color', pin(state, true)))
        .toBe(core().MINE_COLOUR);
      expect(paint('community-reports-point', 'circle-stroke-color', pin(state, true)))
        .toBe('#ffffff');
      expect(paint('community-reports-glyph', 'icon-color', pin(state, true)))
        .toBe('#ffffff');
    }
  });

  it('rings a recent pin of yours blue, with a blue mark on white', () => {
    expect(paint('community-reports-point', 'circle-color', pin('recent', true)))
      .toBe('#ffffff');
    expect(paint('community-reports-point', 'circle-stroke-color', pin('recent', true)))
      .toBe(core().MINE_COLOUR);
    expect(paint('community-reports-glyph', 'icon-color', pin('recent', true)))
      .toBe(core().MINE_COLOUR);
  });

  it('draws a live pin of yours with a blue halo and a blue age', () => {
    expect(paint('community-reports-halo', 'circle-color', pin('live', true)))
      .toBe(core().MINE_COLOUR);
    expect(paint('community-reports-age', 'text-color', pin('live', true)))
      .toBe(core().MINE_COLOUR);
  });

  it("keeps everyone else's pins in their family colours", () => {
    const surface = core().FAMILY_COLOURS.SURFACE;
    // `mine: false` from the server, and no `mine` at all from the cache.
    for (const mine of [false, undefined]) {
      expect(paint('community-reports-point', 'circle-color', pin('live', mine))).toBe(surface);
      expect(paint('community-reports-point', 'circle-stroke-color', pin('recent', mine)))
        .toBe(surface);
      expect(paint('community-reports-glyph', 'icon-color', pin('recent', mine))).toBe(surface);
      expect(paint('community-reports-glyph', 'icon-color', pin('live', mine))).toBe('#ffffff');
      expect(paint('community-reports-halo', 'circle-color', pin('live', mine))).toBe(surface);
      expect(paint('community-reports-age', 'text-color', pin('live', mine))).toBe(surface);
    }
  });

  it('leaves the clusters in the family colours', () => {
    const colours = core().FAMILY_COLOURS;
    const cluster = (warning, surface) => ({ point_count: 3, warning, surface, mine: true });
    expect(paint('community-reports-clusters', 'circle-color', cluster(1, 0))).toBe(colours.WARNING);
    expect(paint('community-reports-clusters', 'circle-color', cluster(0, 2))).toBe(colours.SURFACE);
    expect(paint('community-reports-clusters', 'circle-color', cluster(0, 0))).toBe(colours.TRAIL);
    expect(JSON.stringify(mapStub.layers.get('community-reports-clusters').paint))
      .not.toContain(core().MINE_COLOUR);
  });
});

describe('the offline copy keeps no `mine` (SNOW-1480)', () => {
  it('draws the live payload with `mine`', () => {
    const { data } = mapStub.sources.get('community-reports');
    expect(data.features[0].properties.mine).toBe(true);
  });

  it('caches the payload with `mine` removed', () => {
    expect(putOverlay).toHaveBeenCalledWith('community_reports', expect.anything());
    const [, cached] = putOverlay.mock.calls.find(([key]) => key === 'community_reports');
    expect(cached.features).toHaveLength(1);
    expect(cached.features[0].properties).not.toHaveProperty('mine');
    expect(cached.features[0].properties.family).toBe('SURFACE');
  });
});
