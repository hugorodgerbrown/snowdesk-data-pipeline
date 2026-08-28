/*
 * tests/js/test_map_write_listeners_bind_without_load.js — the three
 * "a panel wrote, repaint the layer" listeners in static/js/map.js bind
 * without waiting for MapLibre's `load` event (SNOW-752).
 *
 * Scenario: none — a boot-order property of one module, observed by not
 * firing an event. No browser could show it and no manual script could
 * describe it.
 *
 * `map.on('load')` waits on the first visually complete render. A basemap
 * style that never loads — offline with nothing cached, an unreachable tile
 * origin — means it never fires, and every listener registered inside it is
 * silently never registered at all. That is not hypothetical: it is the same
 * trap docs/decisions and static/js/map_layer_sync_status.js's history
 * already record, and `snowdesk:favourites-changed` sat inside that handler
 * from SNOW-414 until this ticket.
 *
 * It matters because the LAYERS do not share that dependency. They install
 * from `snowdesk:overlay-load`, at IIFE level, so a map with a failed style
 * could still be drawing a user's pins — and then have no way to notice one
 * being deleted. Pins that cannot be removed is a worse state than pins that
 * never appear.
 *
 * So this suite boots the bundle and deliberately NEVER calls the `load`
 * handlers, then asks each of the three write announcements to do its job.
 * If any of them is moved back inside that handler, the matching test here
 * goes red rather than the behaviour going quietly missing in the one
 * situation nobody tests by hand.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

const ONE_ROUTE = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[7.5, 46.1], [7.54, 46.14]] },
      properties: { uuid: 'r-1', name: 'Rosablanche', bounds: [7.5, 46.1, 7.54, 46.14] },
    },
  ],
};

/** MapLibre stub that NEVER fires `load` — the whole point of this suite. */
function stubMapLibre() {
  const handlers = {};
  const layouts = new Map();
  const sources = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layouts.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources.get(id) || null,
    addSource: (id, def) => {
      sources.set(id, { ...def, setData: vi.fn() });
    },
    addLayer: (def) => {
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => layouts.delete(id),
    removeSource: (id) => sources.delete(id),
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
    // Both false, as they are for a style that never finished: this is the
    // state the pane and a dead tile origin both produce.
    isStyleLoaded: () => false,
    loaded: () => false,
    getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    areTilesLoaded: () => false,
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

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

/** Every URL this bundle has fetched since the last clear. */
function fetched(needle) {
  return globalThis.fetch.mock.calls.filter(([url]) =>
    String(url).includes(needle),
  ).length;
}

let mapStub;

beforeAll(async () => {
  localStorage.clear();
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-favourites-url="/favourites/favourites.geojson"
         data-favourites-eligible="true"
         data-community-reports-url="/api/community-reports.geojson"
         data-community-reports-eligible="true"
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 1e10, usage: 0 }) },
    configurable: true,
  });
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const body = String(url).includes('routes') ? ONE_ROUTE : EMPTY_FC;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  mapStub = stubMapLibre();

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  loadMapBundle();
  // DELIBERATELY NOT run: `for (const h of mapStub.handlers.load) await h()`.
  // Every other map suite does; this one is about what still works when it
  // never happens.
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

describe('with MapLibre’s load event never fired', () => {
  it('the suite really is testing the unloaded case', () => {
    // Guards the guard: if a later edit makes the bundle fire `load` itself,
    // or the stub starts reporting a loaded style, every assertion below
    // would pass for the wrong reason.
    expect(mapStub.loaded()).toBe(false);
    expect(mapStub.isStyleLoaded()).toBe(false);
  });

  it('still installs an overlay the user enables', async () => {
    // The premise of the rest: the LAYER does not need the load handler, so
    // a map in this state can genuinely be drawing a user's own data.
    window.pwaRoutesOverlay.show();

    await waitFor(() => mapStub.sources.has('routes'));
    expect(mapStub.sources.has('routes')).toBe(true);
  });

  it('refetches routes on snowdesk:routes-changed', async () => {
    globalThis.fetch.mockClear();

    document.dispatchEvent(new CustomEvent('snowdesk:routes-changed'));

    await waitFor(() => fetched('routes') > 0);
    expect(fetched('routes')).toBeGreaterThan(0);
  });

  it('refetches community reports on snowdesk:reports-changed', async () => {
    window.pwaCommunityReportsOverlay.show();
    await waitFor(() => mapStub.sources.has('community-reports'));
    globalThis.fetch.mockClear();

    document.dispatchEvent(new CustomEvent('snowdesk:reports-changed'));

    await waitFor(() => fetched('community-reports') > 0);
    expect(fetched('community-reports')).toBeGreaterThan(0);
  });

  it('refetches favourites on snowdesk:favourites-changed', async () => {
    // The listener this ticket MOVED. Inside `map.on('load')` — where it
    // lived from SNOW-414 — this assertion fails: a deleted pin would stay
    // on a map whose style never loaded, with nothing able to take it off.
    globalThis.fetch.mockClear();

    document.dispatchEvent(new CustomEvent('snowdesk:favourites-changed'));

    await waitFor(() => fetched('favourites.geojson') > 0);
    expect(fetched('favourites.geojson')).toBeGreaterThan(0);
  });

  it('draws an optimistic pin on snowdesk:favourite-pending', async () => {
    // Moved with it, and for the same reason: an offline create is exactly
    // the case where the style is most likely to have failed, and the
    // pending pin is the only feedback that the tap was captured.
    document.dispatchEvent(
      new CustomEvent('snowdesk:favourite-pending', {
        detail: { lat: 46.1, lon: 7.5, name: 'Queued' },
      }),
    );

    await waitFor(() => mapStub.sources.has('favourites'));
    expect(mapStub.sources.has('favourites')).toBe(true);
  });
});
