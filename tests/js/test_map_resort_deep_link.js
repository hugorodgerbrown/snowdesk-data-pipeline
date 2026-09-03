/*
 * tests/js/test_map_resort_deep_link.js — /?resort=<slug> flies to that
 * resort's pin (SNOW-807, static/js/map.js).
 *
 * The resort page's "reports near here" link is /?panel=reports&resort=<slug>.
 * The sheet half is test_map_panel_deep_link.js; this is the camera half,
 * which resolves the resort by identity in the resorts source — its
 * feature id is the slug since SNOW-796 — and flies to it, the favourite
 * deep link's shape applied to a source that already exists. Cases:
 *
 *   - a known slug flies the camera to the pin's coordinates;
 *   - a slug nothing carries flies nowhere and says nothing;
 *   - the parameter is consumed, and ?d= and the hash survive;
 *   - the resorts overlay switched off is switched back on through the
 *     panel's own path (the stored preference flips to 'true').
 *
 * The MapLibre stub is the one the other deep-link suites boot with.
 */

import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const RESORTS_KEY = 'snowdesk.map.overlay.resorts';
const VERBIER = [7.2203, 46.0956];

const RESORTS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: VERBIER },
      properties: { id: 'verbier', name: 'Verbier', region_id: 'CH-4115', tier: 'CORE' },
    },
  ],
};
const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/**
 * MapLibre stub recording camera moves.
 *
 * @param {object} record - Collector for `flyTo` calls and mounted popups.
 * @returns {object} The stub map.
 */
function stubMapLibre(record) {
  const handlers = {};
  const layouts = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    // Load-bearing for this suite: the deep link unbinds its own
    // 'sourcedata' listener, and a no-op off() would let a second event run
    // it again — the exact defect the once-only case is written against.
    off: (ev, fn) => {
      handlers[ev] = (handlers[ev] || []).filter((h) => h !== fn);
    },
    once: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layouts.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => {
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => {
      layouts.delete(id);
    },
    removeSource: () => {},
    // installFavouritesLayer raises its pin layer above the choropleth on
    // every install; without this the throw lands inside the lazy-load
    // promise, which map.js swallows, and the layer silently never appears.
    moveLayer: () => {},
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
    hasImage: () => true,
    addImage: () => {},
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: (options) => {
      record.flights.push(options);
    },
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
      const popup = {
        setLngLat(lngLat) { popup.lngLat = lngLat; return popup; },
        setHTML(html) { popup.html = html; return popup; },
        setDOMContent(node) { popup.node = node; return popup; },
        addTo() { record.popups.push(popup); return popup; },
        on() { return popup; },
        remove() {},
      };
      return popup;
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/**
 * The DOM map.js's boot reads.
 *
 * @param {boolean} eligible - Value for `data-favourites-eligible`.
 * @returns {void}
 */
function buildFixture(eligible) {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings/"
         data-resorts-url="/api/resorts.json"
         data-favourites-url="/favourites/favourites.geojson"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-favourites-eligible="${eligible}"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

/** Fire the 'sourcedata' event MapLibre emits once a source has loaded. */
function emitSourceData(map, sourceId) {
  for (const handler of [...(map.handlers.sourcedata || [])]) {
    handler({ sourceId, isSourceLoaded: true, dataType: 'source' });
  }
}

/** Boot the map bundle at `url` with the resorts feed stubbed. */
async function bootAt(url) {
  window.history.replaceState({}, '', url);
  buildFixture(true);
  const record = { flights: [], popups: [] };
  const mapStub = stubMapLibre(record);
  vi.stubGlobal('fetch', vi.fn((input) => {
    const href = String(input);
    const body = href.includes('resorts.geojson') ? RESORTS_GEOJSON : EMPTY_FC;
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }));
  vi.resetModules();
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  await waitFor(() => !!mapStub.getLayer('resorts-pin'), 1000);
  emitSourceData(mapStub, 'resorts');
  await new Promise((resolve) => setTimeout(resolve, 30));
  return { map: mapStub, ...record };
}

afterAll(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
  window.history.replaceState({}, '', '/');
});

beforeEach(() => {
  localStorage.clear();
});

describe('?resort=<slug>', () => {
  it('flies to the resort pin by its slug', async () => {
    localStorage.setItem(RESORTS_KEY, 'true');
    const { flights } = await bootAt('/?resort=verbier');
    expect(flights.length).toBeGreaterThanOrEqual(1);
    expect(flights[flights.length - 1].center).toEqual(VERBIER);
  });

  it('switches the resorts overlay on through the stored preference', async () => {
    localStorage.setItem(RESORTS_KEY, 'false');
    const { flights } = await bootAt('/?resort=verbier');
    expect(localStorage.getItem(RESORTS_KEY)).toBe('true');
    expect(flights[flights.length - 1].center).toEqual(VERBIER);
  });

  it('consumes the parameter and keeps the rest of the address', async () => {
    localStorage.setItem(RESORTS_KEY, 'true');
    await bootAt('/?resort=verbier&d=2026-02-16#CH-4115');
    expect(window.location.search).toBe('?d=2026-02-16');
    expect(window.location.hash).toBe('#CH-4115');
  });

  it('flies nowhere for a slug nothing carries', async () => {
    localStorage.setItem(RESORTS_KEY, 'true');
    const { flights } = await bootAt('/?resort=nowhere');
    expect(flights.filter((f) => f.center && f.center[0] === VERBIER[0])).toEqual([]);
    expect(window.location.search).toBe('');
  });
});
