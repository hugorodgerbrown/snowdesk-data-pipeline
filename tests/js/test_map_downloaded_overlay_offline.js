/*
 * tests/js/test_map_downloaded_overlay_offline.js — SNOW-857: the
 * downloaded-coverage overlay switches itself on while the app is offline,
 * for a reader who has never touched the switch, and never overrides one
 * who has.
 *
 * Why it exists. SNOW-856 pinned a shared z0-9 base layer, and MapLibre
 * stretches a cached ancestor over any tile it does not hold, so the map
 * no longer goes blank at the edge of a download — it draws coarsely
 * everywhere. The blank edge WAS the cue for "your detailed coverage stops
 * here", and losing it is the one respect in which that ticket left the
 * product worse. This overlay can say the same thing, but it was off by
 * default and buried at the foot of the Manage downloads sheet, so nobody
 * had it on at the moment it started mattering.
 *
 * The whole risk is in the STORAGE SHAPE, which is why most of this file
 * is about it. The preference was read through `readBoolStorage`, which
 * collapses "absent" and "'false'" to the same `false` — and an auto-on
 * rule cannot work against that: it would either override a deliberate
 * off, or never fire for anyone. So the value is read raw and has three
 * states, and the two failure modes worth guarding are
 *
 *   - a user's explicit choice being overridden by the connection, and
 *   - the app WRITING a preference on the user's behalf, which silently
 *     converts "never touched" into "explicitly off" and stops the rule
 *     ever firing again.
 *
 * The second is the nastier one: it is invisible, it is permanent, and it
 * would only show up as "this feature does nothing for me" months later.
 * Every case below therefore asserts on localStorage as well as on paint.
 *
 * jsdom boot follows test_map_downloaded_overlay_boot.js's harness; see its
 * header. The difference here is that `window.pwaConnectivity` is stubbed
 * before the bundle is evaluated (pwa_offline.js is not in the map bundle)
 * and the stored preference is varied per suite.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const STORAGE_KEY = 'snowdesk.map.overlay.downloads';
const TEMPLATE_LIBERTY = 'https://tiles-liberty.example.invalid/{z}/{x}/{y}.pbf';
const CACHED_TILES_ZOOM = 14;

const REGIONS_GEOJSON = { type: 'FeatureCollection', features: [] };

/** Minimal MapLibre stub — layer visibility is the whole subject here. */
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
      layers.set(def.id, { ...(def.paint || {}), filter: def.filter });
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
    setFilter: (id, filter) => {
      const layer = layers.get(id);
      if (layer) layer.filter = filter;
    },
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

/** Cache Storage stub — one pinned bucket holding one z14 tile. */
function installCachesStub() {
  Object.defineProperty(window, 'caches', {
    value: {
      keys: vi.fn(async () => ['snowdesk-basemap-pinned-region-CH-2101']),
      open: vi.fn(async () => ({
        keys: async () => [
          { url: `https://tiles-liberty.example.invalid/${CACHED_TILES_ZOOM}/100/200.pbf` },
        ],
        put: async () => {},
        match: async () => undefined,
      })),
      delete: vi.fn(async () => {}),
    },
    configurable: true,
    writable: true,
  });
}

/** `window.pwaDb` holding one region downloaded under the active basemap. */
function installDbStub() {
  const rows = new Map([
    ['basemap.regions', [{
      region_id: 'CH-2101',
      name: 'Aletsch',
      template: TEMPLATE_LIBERTY,
      basemapKey: 'openfreemap_liberty',
      bytes: 1000,
      savedAt: '2026-08-01T10:00:00.000Z',
    }]],
    ['basemap.customAreas', []],
  ]);
  window.pwaDb = {
    get: vi.fn(async (_store, key) => (rows.has(key) ? { key, value: rows.get(key) } : undefined)),
    put: vi.fn(async (_store, row) => { rows.set(row.key, row.value); }),
    delete: vi.fn(async () => {}),
  };
}

/**
 * The DOM map.js's boot reads, plus the legend card's coverage section —
 * which is the surface that has to appear alongside an overlay nobody
 * switched on.
 */
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
    <section id="map-coverage-section" hidden></section>
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
 * three channels by filling a 1x1 one. Same narrow double as
 * test_map_downloaded_overlay_boot.js's — see its comment. Colour is not
 * this file's subject, but the hatch build sits on the boot path, so
 * without it nothing installs at all.
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
async function waitFor(predicate, attempts = 50) {
  for (let i = 0; i < attempts; i += 1) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

let mapStub;
let online;
let restoreCanvas;

/**
 * Boot the map bundle with a given stored preference and connection state.
 *
 * @param {string|null} stored `'true'` / `'false'` / null for untouched.
 * @param {boolean} startOnline The connection at boot.
 * @returns {Promise<void>}
 */
async function boot(stored, startOnline) {
  buildFixture();
  online = startOnline;
  if (stored === null) window.localStorage.removeItem(STORAGE_KEY);
  else window.localStorage.setItem(STORAGE_KEY, stored);
  installDbStub();
  installCachesStub();
  // Assigned rather than frozen: pwa_offline.js is not in the map bundle,
  // and what matters here is only that `isOnline()` is the EFFECTIVE state
  // — a user-forced offline mode is exactly the reader this rule serves,
  // and `navigator.onLine` would answer true for them.
  window.pwaConnectivity = { isOnline: () => online };
  mapStub = stubMapLibre();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * 1024 * 1024, usage: 0 }) },
    configurable: true,
  });
  vi.stubGlobal('fetch', vi.fn((url) => {
    const body = String(url).includes('regions.geojson') ? REGIONS_GEOJSON : {};
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }));
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
}

/** Flip the connection and fire the broadcast pwa_offline.js sends. */
function setOnline(next) {
  online = next;
  document.dispatchEvent(
    new CustomEvent('snowdesk:connectivity-changed', { detail: { online: next } }),
  );
}

const overlayVisible = () => mapStub.getLayoutProperty('cached-tiles-fill', 'visibility') === 'visible';
const legendVisible = () => !document.getElementById('map-coverage-section').hidden;

beforeEach(() => {
  restoreCanvas = stubCanvas2D();
  window.localStorage.removeItem(STORAGE_KEY);
});

afterEach(() => {
  restoreCanvas();
  vi.unstubAllGlobals();
  window.localStorage.removeItem(STORAGE_KEY);
  delete globalThis.maplibregl;
  delete window.pwaDb;
  delete window.pwaConnectivity;
});

describe('no stored preference — the overlay follows the connection', () => {
  it('boots ON when the app is already offline', async () => {
    await boot(null, false);

    // The case the ticket exists for: a reader who opens the map on a
    // mountain, having never opened the downloads sheet in their life.
    expect(overlayVisible()).toBe(true);
    expect(window.pwaDownloadedOverlay.isEnabled()).toBe(true);
  });

  it('reveals the legend key alongside it', async () => {
    await boot(null, false);

    // An overlay that appears unprompted with no key is a hatch the reader
    // has no way to interpret — worse than not appearing.
    expect(legendVisible()).toBe(true);
  });

  it('boots OFF when the app is online', async () => {
    await boot(null, true);

    expect(overlayVisible()).toBe(false);
    expect(legendVisible()).toBe(false);
  });

  it('switches on when the connection is lost mid-session', async () => {
    await boot(null, true);
    expect(overlayVisible()).toBe(false);

    setOnline(false);

    await waitFor(() => overlayVisible());
    expect(overlayVisible()).toBe(true);
    expect(legendVisible()).toBe(true);
  });

  it('switches off again when the network comes back', async () => {
    await boot(null, false);
    expect(overlayVisible()).toBe(true);

    setOnline(true);

    await waitFor(() => !overlayVisible());
    expect(overlayVisible()).toBe(false);
    expect(legendVisible()).toBe(false);
  });

  it('NEVER writes a preference while following the connection', async () => {
    // The invisible, permanent failure this guards: a write here converts
    // "never touched" into "explicitly off" behind the user's back, and the
    // rule then never fires again for them.
    await boot(null, true);
    setOnline(false);
    await waitFor(() => overlayVisible());
    setOnline(true);
    await waitFor(() => !overlayVisible());

    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});

describe('an explicit preference outranks the connection', () => {
  it('stays OFF while offline when the user switched it off', async () => {
    await boot('false', false);

    expect(overlayVisible()).toBe(false);
    expect(legendVisible()).toBe(false);
  });

  it('stays OFF when the connection drops later', async () => {
    await boot('false', true);

    setOnline(false);
    // Nothing to wait for — the assertion is that nothing happens — so this
    // yields once to let any stray handler run before reading.
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(overlayVisible()).toBe(false);
  });

  it('stays ON while online when the user switched it on', async () => {
    await boot('true', true);

    expect(overlayVisible()).toBe(true);
    expect(legendVisible()).toBe(true);
  });

  it('stays ON when the network comes back', async () => {
    await boot('true', false);

    setOnline(true);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(overlayVisible()).toBe(true);
  });

  it('is created the moment the user touches the switch, and then holds', async () => {
    await boot(null, true);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();

    await window.pwaDownloadedOverlay.show();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe('true');

    // Having stated a preference, the reader keeps it through a connection
    // change in either direction.
    setOnline(false);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(overlayVisible()).toBe(true);
    setOnline(true);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(overlayVisible()).toBe(true);
  });
});

describe('no pwa_offline.js on the page', () => {
  it('falls back to navigator.onLine rather than throwing', async () => {
    buildFixture();
    installDbStub();
    installCachesStub();
    mapStub = stubMapLibre();
    Object.defineProperty(navigator, 'storage', {
      value: { estimate: async () => ({ quota: 1e10, usage: 0 }) },
      configurable: true,
    });
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
      ok: true, json: () => Promise.resolve(REGIONS_GEOJSON),
    })));
    vi.resetModules();
    await import('../../static/js/basemap_download_core.js');
    await import('../../static/js/search_core.js');
    await import('../../static/js/choropleth_core.js');
    loadMapBundle();
    for (const handler of mapStub.handlers.load || []) await handler();

    // jsdom reports navigator.onLine true, so this is the online answer —
    // reached without `window.pwaConnectivity` existing at all, which is
    // every consumer's documented fallback.
    expect(overlayVisible()).toBe(false);
  });
});
