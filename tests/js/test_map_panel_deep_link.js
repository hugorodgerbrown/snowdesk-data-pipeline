/*
 * tests/js/test_map_panel_deep_link.js — /?panel=<name> opens that sheet with
 * nothing selected (SNOW-803, static/js/map.js).
 *
 * The three account list pages became permanent redirects to the map with
 * the matching sheet open, and this parameter is what makes the redirect
 * land on an open sheet rather than a bare map. Each sheet module exposes
 * ``open()`` on a frozen window.pwa*Sheet bridge; map.js consumes the
 * parameter once and calls it. The cases:
 *
 *   - each of the three names opens its own bridge and no other;
 *   - the parameter is stripped from the address bar, so a refetch or a
 *     shared URL does not reopen the sheet, while ?d= and the hash survive;
 *   - a name nothing answers to is consumed silently, and so is a name
 *     the lookup table only inherits (``constructor``);
 *   - no parameter means no call at all;
 *   - a bridge that is not there (the surface's script did not load) is a
 *     no-op, not a throw in the map's boot.
 *
 * The MapLibre stub is the one test_map_favourite_deep_link.js boots with;
 * see that file's header for why the bundle is evaluated rather than
 * imported.
 */

import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/**
 * MapLibre stub — the one test_map_favourite_deep_link.js boots with.
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
         data-favourites-eligible="${eligible}"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/** Boot the map bundle at `url` with the three sheet bridges stubbed. */
async function bootAt(url) {
  window.history.replaceState({}, '', url);
  buildFixture(true);
  const bridges = {
    favourites: { open: vi.fn(), close: vi.fn(), isOpen: () => false },
    routes: { open: vi.fn(), close: vi.fn(), isOpen: () => false },
    reports: { open: vi.fn(), close: vi.fn(), isOpen: () => false },
  };
  window.pwaFavouritesSheet = bridges.favourites;
  window.pwaRoutesSheet = bridges.routes;
  window.pwaReportSheet = bridges.reports;

  const record = { flights: [], popups: [] };
  const mapStub = stubMapLibre(record);
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
    ok: true, json: () => Promise.resolve(EMPTY_FC),
  })));

  vi.resetModules();
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  await new Promise((resolve) => setTimeout(resolve, 20));
  return bridges;
}

afterAll(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
  window.history.replaceState({}, '', '/');
});

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  delete window.pwaFavouritesSheet;
  delete window.pwaRoutesSheet;
  delete window.pwaReportSheet;
});

describe('?panel= opens the named sheet', () => {
  it.each([
    ['favourites', 'favourites'],
    ['routes', 'routes'],
    ['reports', 'reports'],
  ])('/?panel=%s opens that bridge and no other', async (name, key) => {
    const bridges = await bootAt(`/?panel=${name}`);
    for (const [other, bridge] of Object.entries(bridges)) {
      expect(bridge.open).toHaveBeenCalledTimes(other === key ? 1 : 0);
    }
  });

  it('strips the parameter and keeps the rest of the address', async () => {
    await bootAt('/?panel=favourites&d=2026-02-16#CH-4115');
    expect(window.location.search).toBe('?d=2026-02-16');
    expect(window.location.hash).toBe('#CH-4115');
  });

  it('consumes a name nothing answers to, silently', async () => {
    const bridges = await bootAt('/?panel=downloads');
    expect(window.location.search).toBe('');
    for (const bridge of Object.values(bridges)) expect(bridge.open).not.toHaveBeenCalled();
  });

  it('treats an inherited property name as a name nothing answers to', async () => {
    // The lookup table is a plain object, so ``'constructor'`` and
    // ``'toString'`` resolve through its prototype to functions. Only an
    // own key may reach a bridge (CodeQL js/unvalidated-dynamic-method-call).
    const bridges = await bootAt('/?panel=constructor');
    expect(window.location.search).toBe('');
    for (const bridge of Object.values(bridges)) expect(bridge.open).not.toHaveBeenCalled();
  });

  it('does nothing without the parameter', async () => {
    const bridges = await bootAt('/?d=2026-02-16');
    expect(window.location.search).toBe('?d=2026-02-16');
    for (const bridge of Object.values(bridges)) expect(bridge.open).not.toHaveBeenCalled();
  });

  it('survives a missing bridge', async () => {
    window.history.replaceState({}, '', '/?panel=routes');
    buildFixture(true);
    delete window.pwaRoutesSheet;
    const mapStub = stubMapLibre({ flights: [], popups: [] });
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, json: () => Promise.resolve(EMPTY_FC),
    })));
    vi.resetModules();
    await import('../../static/js/search_core.js');
    await import('../../static/js/choropleth_core.js');
    expect(() => loadMapBundle()).not.toThrow();
    for (const handler of mapStub.handlers.load || []) await handler();
    expect(window.location.search).toBe('');
  });
});
