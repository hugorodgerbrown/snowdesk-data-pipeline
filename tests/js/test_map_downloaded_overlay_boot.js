/*
 * tests/js/test_map_downloaded_overlay_boot.js — the downloads panel's
 * "Display on the map" switch is a PERSISTED preference: a reload comes
 * back with the overlay in the state the user left it, squares and all.
 *
 * It was session-scoped for the life of SNOW-645 — deliberately, as an
 * "inspection mode" — and Hugo reported that as a bug, in the two forms it
 * actually shows up in: "if I toggle the 'display on map' and then refresh
 * the page, it's a) not displaying the areas on the map, and b) resetting
 * itself when I refresh the page." One cause, two symptoms, and the second
 * is the one that gives the game away: three other panels carry the same
 * switch, under the same label, and all three survive a reload.
 *
 * Both halves are asserted here because they fail independently. The flag
 * can restore while the map stays blank — `installRegionsLayers` creates
 * the two layers visible from that flag, but visible layers over an empty
 * source paint nothing, and every other path that fills the source is a
 * reaction to something happening LATER (a basemap swap, a lazy country
 * load, a settling download, the switch itself). A boot where none of those
 * fires is exactly the case being fixed.
 *
 * jsdom boot follows test_map_downloaded_overlay_colour.js's harness; see
 * its header. This file's difference is that localStorage is seeded BEFORE
 * the bundle is evaluated, since the preference is read once at parse time.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const STORAGE_KEY = 'snowdesk.map.overlay.downloads';
const TEMPLATE_LIBERTY = 'https://tiles-liberty.example.invalid/{z}/{x}/{y}.pbf';
const CACHED_TILES_ZOOM = 14;

/** Empty regions collection — this suite only cares about the overlay. */
const REGIONS_GEOJSON = { type: 'FeatureCollection', features: [] };

/** Minimal MapLibre stub — layouts and the cached-tiles data are the subject. */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const layouts = new Map();
  const images = new Map();
  let cachedTilesData = null;
  const map = {
    on: (ev, a, b) => { (handlers[ev] ||= []).push(typeof a === 'function' ? a : b); },
    once: (ev, cb) => { (handlers[ev] ||= []).push(cb); },
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => {
      const layout = layouts.get(id);
      return layout ? layout[prop] : undefined;
    },
    getPaintProperty: (id, prop) => {
      const paint = layers.get(id);
      return paint ? paint[prop] : undefined;
    },
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) =>
      id === 'cached-tiles'
        ? { setData: (data) => { cachedTilesData = data; } }
        : id === 'basemap'
          ? { tiles: [TEMPLATE_LIBERTY] }
          : null,
    addSource: () => {},
    addLayer: (def) => {
      layers.set(def.id, { ...(def.paint || {}) });
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => { layers.delete(id); layouts.delete(id); },
    removeSource: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
    setPaintProperty: (id, prop, value) => {
      const paint = layers.get(id);
      if (paint) paint[prop] = value;
    },
    setFilter: () => {},
    setFeatureState: () => {},
    removeFeatureState: () => {},
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: { basemap: { type: 'vector' } } }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    loaded: () => true,
    areTilesLoaded: () => true,
    listImages: () => Array.from(images.keys()),
    hasImage: (id) => images.has(id),
    addImage: (id, image, options) => { images.set(id, { image, options }); },
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
    layouts,
    images,
    getCachedTilesData: () => cachedTilesData,
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

/** Cache Storage stub — one pinned bucket per `{name, urls}` entry. */
function installCachesStub(buckets) {
  Object.defineProperty(window, 'caches', {
    value: {
      keys: vi.fn(async () => buckets.map((b) => b.name)),
      open: vi.fn(async (name) => {
        const bucket = buckets.find((b) => b.name === name);
        const urls = bucket ? bucket.urls : [];
        return {
          keys: async () => urls.map((url) => ({ url })),
          put: async () => {},
          match: async () => undefined,
        };
      }),
      delete: vi.fn(async () => {}),
    },
    configurable: true,
    writable: true,
  });
}

/** `window.pwaDb` stub holding one downloaded region under the active basemap. */
function installDbStub(regions) {
  const rows = new Map([
    ['basemap.regions', regions],
    ['basemap.customAreas', []],
  ]);
  window.pwaDb = {
    get: vi.fn(async (_store, key) => (rows.has(key) ? { key, value: rows.get(key) } : undefined)),
    put: vi.fn(async (_store, row) => { rows.set(row.key, row.value); }),
    delete: vi.fn(async () => {}),
  };
}

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <ul id="basemap-menu">
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="openfreemap_liberty"
          data-basemap-url="https://tiles.example.invalid/liberty.json"
          aria-checked="true"
        >OpenFreeMap</button>
      </li>
    </ul>`;
}

/**
 * jsdom ships no 2D canvas, and `map.js` resolves an identity colour to
 * three channels by filling a 1×1 one — the only way to parse every CSS
 * colour syntax a token might hold. This is the narrowest double that keeps
 * that path honest: it parses the `rgb(r, g, b)` values THIS file sets on
 * the tokens, so an image built for the wrong basemap's colour still fails.
 *
 * @returns {() => void} Restores the original `getContext`.
 */
function stubCanvas2D() {
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function getContext(type) {
    if (type !== '2d') return original ? original.call(this, type) : null;
    let channels = [0, 0, 0, 255];
    return {
      set fillStyle(value) {
        const m = /rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/.exec(String(value));
        channels = m ? [Number(m[1]), Number(m[2]), Number(m[3]), 255] : [0, 0, 0, 255];
      },
      get fillStyle() {
        return `rgb(${channels[0]}, ${channels[1]}, ${channels[2]})`;
      },
      fillRect: () => {},
      getImageData: () => ({ data: Uint8ClampedArray.from(channels) }),
    };
  };
  return () => { HTMLCanvasElement.prototype.getContext = original; };
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

let mapStub;
let restoreCanvas;

beforeAll(async () => {
  buildFixture();
  restoreCanvas = stubCanvas2D();
  // The state a user leaves behind by switching the panel's toggle on, as
  // it survives the reload: the preference in localStorage, the download
  // itself in IndexedDB and Cache Storage.
  window.localStorage.setItem(STORAGE_KEY, 'true');
  installDbStub([
    {
      region_id: 'CH-2101',
      name: 'Aletsch',
      template: TEMPLATE_LIBERTY,
      basemapKey: 'openfreemap_liberty',
      bytes: 1000,
      savedAt: '2026-08-01T10:00:00.000Z',
    },
  ]);
  installCachesStub([
    {
      name: 'snowdesk-basemap-pinned-CH-2101',
      urls: [`https://tiles-liberty.example.invalid/${CACHED_TILES_ZOOM}/100/200.pbf`],
    },
  ]);

  mapStub = stubMapLibre();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * 1024 * 1024, usage: 0 }) },
    configurable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const body = String(url).includes('regions.geojson') ? REGIONS_GEOJSON : {};
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom; installRegionsLayers (and so the
  // two cached-tiles-* layers) hangs off it. Nothing else is dispatched
  // afterwards — no basemap swap, no country load, no download — because a
  // plain reload is the case under test.
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  restoreCanvas();
  vi.unstubAllGlobals();
  window.localStorage.removeItem(STORAGE_KEY);
  delete globalThis.maplibregl;
  delete window.pwaDb;
});

describe('the downloads overlay preference across a reload', () => {
  it('comes back switched on', () => {
    // What the panel's switch reads on open: the preference, not the paint.
    expect(window.pwaDownloadedOverlay.isEnabled()).toBe(true);
  });

  it('installs both layers visible', () => {
    for (const id of ['cached-tiles-fill', 'cached-tiles-line']) {
      expect(mapStub.getLayoutProperty(id, 'visibility')).toBe('visible');
    }
  });

  it('paints the squares without waiting for anything else to happen', async () => {
    await waitFor(() => (mapStub.getCachedTilesData()?.features || []).length > 0);

    // The half that fails independently of the flag: visible layers over an
    // empty source are an overlay switched on and showing nothing.
    expect(mapStub.getCachedTilesData().features).toHaveLength(1);
  });

  it('writes the preference back when the switch is turned off', () => {
    window.pwaDownloadedOverlay.hide();

    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('false');
    expect(window.pwaDownloadedOverlay.isEnabled()).toBe(false);
  });
});
