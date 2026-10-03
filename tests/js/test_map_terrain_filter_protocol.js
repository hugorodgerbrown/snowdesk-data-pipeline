/*
 * tests/js/test_map_terrain_filter_protocol.js — what the terrainfilter://
 * handler draws when the class-tile request does not return a tile (SNOW-978).
 *
 * The no-data hatch is a claim about the SURVEY: there is no terrain data
 * here. Only a real 204 inside the coverage rectangle (and B = 255 pixels
 * inside a tile) may make it. A request that fails — a network error or a
 * non-OK status — says nothing about the ground, so it must draw nothing,
 * and must not be cached, so the next pass over the tile tries again.
 *
 * The MapLibre stub records the protocol handler map.js registers when the
 * filter is first switched on; the handler is then called directly, the
 * way MapLibre would. ImageData and createImageBitmap are stubbed to hand
 * the painted pixels straight back.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const TILE_URL = 'https://tiles.example.invalid/terrain-class/v1/{z}/{x}/{y}.png';
const REGIONS_GEOJSON = { type: 'FeatureCollection', features: [] };
// Inside COVERAGE_BOUNDS (Verbier), and well east of it.
const COVERED = 'terrainfilter://12/2130/1455?v=ff-30---';
const UNCOVERED = 'terrainfilter://12/2200/1455?v=ff-30---';

const protocols = {};
/** What the next class-tile fetch does: a status, or 'reject'. */
let tileOutcome = 204;
let tileFetches = 0;

/**
 * Minimal MapLibre stub, with addProtocol recording the handler.
 *
 * @returns {object} The stub map.
 */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const layouts = new Map();
  const map = {
    on: (ev, a, b) => { (handlers[ev] ||= []).push(typeof a === 'function' ? a : b); },
    once: (ev, cb) => { (handlers[ev] ||= []).push(cb); },
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => {
      layers.set(def.id, def);
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => { layers.delete(id); layouts.delete(id); },
    removeSource: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
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
    hasImage: () => false,
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
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      return { setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }), remove: () => {} };
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
    addProtocol: (name, handler) => { protocols[name] = handler; },
  };
  return map;
}

/** The DOM map.js's boot reads, with the filter eligible. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-terrain-filter-eligible="true"
         data-terrain-class-tile-url="${TILE_URL}"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/** Call the handler as MapLibre would, and return the painted pixels. */
async function paint(url) {
  const { data } = await protocols.terrainfilter({ url }, new AbortController());
  return data.data;
}

const alphaAt = (pixels, x, y) => pixels[(y * 256 + x) * 4 + 3];
const allTransparent = (pixels) => pixels.every((v, i) => i % 4 !== 3 || v === 0);

beforeAll(async () => {
  window.localStorage.removeItem('snowdesk.map.overlay.terrain_filter');
  window.localStorage.removeItem('snowdesk.map.terrain_filter');
  buildFixture();
  window.pwaDb = {
    get: vi.fn(async () => undefined),
    put: vi.fn(async () => {}),
    delete: vi.fn(async () => {}),
  };
  stubMapLibre();
  vi.stubGlobal('ImageData', class {
    constructor(data, width, height) {
      this.data = data;
      this.width = width;
      this.height = height;
    }
  });
  vi.stubGlobal('createImageBitmap', vi.fn(async (source) => source));
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      if (String(url).startsWith('https://tiles.example.invalid/terrain-class/')) {
        tileFetches += 1;
        if (tileOutcome === 'reject') return Promise.reject(new TypeError('network down'));
        return Promise.resolve({ ok: tileOutcome >= 200 && tileOutcome < 300, status: tileOutcome });
      }
      const body = String(url).includes('regions.geojson') ? REGIONS_GEOJSON : {};
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
    }),
  );
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/terrain_filter_core.js');
  loadMapBundle();
  // The first switch-on installs the layer, which registers the protocol.
  window.pwaTerrainFilter.show();
});

afterAll(() => {
  vi.unstubAllGlobals();
  window.localStorage.removeItem('snowdesk.map.overlay.terrain_filter');
  window.localStorage.removeItem('snowdesk.map.terrain_filter');
  delete globalThis.maplibregl;
  delete window.pwaDb;
});

beforeEach(() => {
  tileFetches = 0;
});

describe('the terrainfilter:// handler', () => {
  it('is registered once the filter is switched on', () => {
    expect(typeof protocols.terrainfilter).toBe('function');
  });

  it('draws nothing for a failed request, and does not cache it', async () => {
    tileOutcome = 500;
    expect(allTransparent(await paint(COVERED))).toBe(true);
    // The next pass tries again — and a real 204 there is now hatched.
    tileOutcome = 204;
    const retried = await paint(COVERED);
    expect(tileFetches).toBe(2);
    expect(alphaAt(retried, 0, 0)).toBeGreaterThan(0);
    expect(alphaAt(retried, 4, 0)).toBe(0);
  });

  it('caches a 204, so the next repaint needs no network', async () => {
    await paint(COVERED);
    expect(tileFetches).toBe(0);
  });

  it('draws nothing for a network error inside coverage', async () => {
    tileOutcome = 'reject';
    expect(allTransparent(await paint('terrainfilter://12/2131/1455?v=ff-30---'))).toBe(true);
    tileOutcome = 'reject';
    await paint('terrainfilter://12/2131/1455?v=ff-30---');
    expect(tileFetches).toBe(2);
  });

  it('draws nothing for a 204 outside coverage', async () => {
    tileOutcome = 204;
    expect(allTransparent(await paint(UNCOVERED))).toBe(true);
  });
});
