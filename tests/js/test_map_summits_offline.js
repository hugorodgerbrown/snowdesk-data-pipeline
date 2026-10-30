/*
 * tests/js/test_map_summits_offline.js — search's SAC summit rows
 * read offline (SNOW-1320).
 *
 * map.js's `loadSummits` reads /api/ski-summits.json the first time search
 * is focused. Before this ticket a device without signal had no summit
 * rows at all: the feed was neither worker-cached nor stored. Now a read
 * that lands is written through to the overlay cache as `ski_summits`, and
 * a read that rejects (the radio off) or answers non-OK (a captive portal,
 * a 5xx) falls back to that stored list. These are the three branches.
 *
 * The download's half — `cacheOverlayFeedsForDownload` storing the same
 * row — is in test_map_download_content.js. Each case boots a fresh bundle
 * because `loadSummits` reads once per page; the stub and fixture follow
 * test_map_pinned_resorts.js.
 */

import { afterAll, afterEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };
const SUMMITS_URL = '/api/ski-summits.json';

/** What the server sends now. */
const LIVE = { summits: [{ key: 'pigne', name: "Pigne d'Arolla", lon: 7.46, lat: 45.99 }] };

/** What the device stored the last time it read the list. */
const STORED = { summits: [{ key: 'rosablanche', name: 'Rosablanche', lon: 7.33, lat: 46.06 }] };

/** Minimal MapLibre stub — nothing here reads a layer back. */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: () => null,
    getFilter: () => null,
    getLayoutProperty: () => undefined,
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: () => {},
    removeLayer: () => {},
    removeSource: () => {},
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

/** The DOM map.js's boot reads, on a page that lists summits. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-ski-routes-eligible="true"
         data-ski-summits-url="${SUMMITS_URL}"
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

let stored;

/**
 * Boot a fresh bundle whose summit read answers with `summits`.
 *
 * @param {function(): Promise<any>} summits The fetch's answer for the list.
 */
async function boot(summits) {
  localStorage.clear();
  buildFixture();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 1e10, usage: 0 }) },
    configurable: true,
  });
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  stored = new Map([['ski_summits', STORED]]);
  window.pwaMapOverlayCache = {
    getOverlay: vi.fn(async (key) => stored.get(key) || null),
    putOverlay: vi.fn(async (key, data) => {
      stored.set(key, data);
    }),
  };
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      if (String(url) === SUMMITS_URL) return summits();
      return Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) });
    }),
  );
  const map = stubMapLibre();
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of map.handlers.load || []) await handler();
}

/**
 * Focus search, then type `query`, as a reader does.
 *
 * @param {string} query
 * @returns {Promise<string>} The results list's text once it has some.
 */
async function search(query) {
  const input = /** @type {HTMLInputElement} */ (document.getElementById('search-input'));
  input.focus();
  input.dispatchEvent(new Event('focus'));
  input.value = query;
  input.dispatchEvent(new Event('input'));
  const results = document.getElementById('search-results');
  await waitFor(() => (results.textContent || '').includes(query));
  return results.textContent || '';
}

afterEach(() => {
  vi.unstubAllGlobals();
});

afterAll(() => {
  localStorage.clear();
  delete window.pwaMapOverlayCache;
  delete globalThis.maplibregl;
});

describe("search's summit rows (SNOW-1320)", () => {
  it('writes a list that lands through to the overlay cache', async () => {
    await boot(() => Promise.resolve({ ok: true, json: () => Promise.resolve(LIVE) }));

    expect(await search('Pigne')).toContain("Pigne d'Arolla");
    expect(window.pwaMapOverlayCache.putOverlay).toHaveBeenCalledWith('ski_summits', LIVE);
  });

  it('lists the stored summits when the read rejects', async () => {
    await boot(() => Promise.reject(new TypeError('Failed to fetch')));

    expect(await search('Rosablanche')).toContain('Rosablanche');
    expect(window.pwaMapOverlayCache.getOverlay).toHaveBeenCalledWith('ski_summits');
    expect(window.pwaMapOverlayCache.putOverlay).not.toHaveBeenCalled();
  });

  it('lists the stored summits when the read answers non-OK', async () => {
    await boot(() => Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) }));

    expect(await search('Rosablanche')).toContain('Rosablanche');
    expect(window.pwaMapOverlayCache.putOverlay).not.toHaveBeenCalled();
  });
});
