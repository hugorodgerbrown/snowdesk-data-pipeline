/*
 * tests/js/test_map_region_download_signin.js — the region download
 * roundel's sign-in gate (SNOW-749).
 *
 * STARTING a download needs an account. The requirement that makes this worth a test is not the refusal
 * but its shape: the control stays VISIBLE and stays TAPPABLE, and the tap
 * goes somewhere useful. A hidden control reads as a missing feature and a
 * dead one reads as a bug, so both are asserted rather than assumed.
 *
 * The other half is what the gate must NOT touch. Reading a region already
 * on this device is never gated — working with no signal is the entire
 * point of having downloaded it — so a signed-out visitor whose bucket
 * still holds the tiles must still get the green 'done' circle. That
 * assertion is the reason this file boots the whole probe path rather than
 * calling ``setState`` directly.
 *
 * Harness follows test_map_download_eviction.js — see its header for the
 * jsdom-boot rationale (map.js is one script of top-level IIFEs; MapLibre
 * never fires 'load', so the stub records handlers and the harness invokes
 * the one it wants).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const MB = 1024 * 1024;

const REGION_ID = 'CH-4115';
const SIGNIN_URL = '/accounts/sign-in/';
const TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const CENTRE_TILE = { z: 14, x: 8577, y: 5811 };
const BUCKET = 'snowdesk-basemap-pinned-region-' + REGION_ID;

/** One region carrying a precomputed download summary, as regions.geojson emits. */
const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {
        id: REGION_ID,
        name: 'Martigny — Verbier',
        download: { count: 1, mb: 4, over_ceiling: false, centre_tile: CENTRE_TILE },
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[7.0, 46.0], [7.01, 46.0], [7.01, 46.01], [7.0, 46.01], [7.0, 46.0]]],
      },
    },
  ],
};

/** Minimal MapLibre stub — enough for map.js's boot to complete. */
function stubMapLibre(styleSources) {
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
    getLayoutProperty: () => null,
    getPaintProperty: () => null,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => (styleSources && styleSources.basemap ? { tiles: [TEMPLATE] } : null),
    addSource: () => {},
    addLayer: () => {},
    removeLayer: () => {},
    removeSource: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: () => {},
    setFilter: () => {},
    setFeatureState: () => {},
    removeFeatureState: () => {},
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: styleSources || {} }),
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
      getWest: () => 5,
      getSouth: () => 45,
      getEast: () => 10,
      getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 8, lat: 46.5 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
  };
  globalThis.maplibregl = {
    Map: function () {
      return map;
    },
    Popup: function () {
      return {
        setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }),
        remove: () => {},
      };
    },
    GeolocateControl: function () {
      return { on: () => {} };
    },
    AttributionControl: function () {
      return {};
    },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/** Cache Storage stub over a name -> Set-of-urls map. */
function installCachesStub(buckets) {
  const store = new Map(Object.entries(buckets || {}).map(([k, v]) => [k, new Set(v)]));
  const stub = {
    store,
    keys: vi.fn(async () => [...store.keys()]),
    open: vi.fn(async (name) => ({
      keys: async () => [...(store.get(name) || [])].map((url) => ({ url })),
      put: async () => {},
      match: async () => undefined,
    })),
    delete: vi.fn(async (name) => store.delete(name)),
  };
  Object.defineProperty(window, 'caches', { value: stub, configurable: true, writable: true });
  return stub;
}

/** In-memory `meta:app`. */
function installDbStub(initial) {
  const rows = new Map(Object.entries(initial || {}));
  window.pwaDb = {
    rows,
    get: vi.fn(async (_store, key) => (rows.has(key) ? { key, value: rows.get(key) } : undefined)),
    put: vi.fn(async (_store, row) => {
      rows.set(row.key, row.value);
      return row.key;
    }),
    delete: vi.fn(async (_store, key) => {
      rows.delete(key);
    }),
  };
  return rows;
}

/**
 * The DOM both download controls read.
 *
 * ``#map-custom-download-control`` is the config carrier for the whole
 * downloads surface (see its own template comment), so the gate's two
 * booleans and the sign-in URL live on it — NOT on the region roundel this
 * file is about. That indirection is exactly what a fixture has to
 * reproduce, since getting it wrong leaves the gate reading `undefined`
 * and silently off.
 */
function buildFixture({ eligible, surface = true }) {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="standard"
         data-season-end="2026-05-31"></div>
    <button id="map-download-control" type="button"></button>
    ${
      surface
        ? `<button id="map-custom-download-control" type="button"
            data-downloads-eligible="${eligible}"
            data-signin-url="${SIGNIN_URL}"></button>`
        : ''
    }
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/** Resolve after `ms` of real time — the control's work is promise-driven. */
function tick(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await tick(10);
  }
  return predicate();
}

/**
 * Boot the bundle against a fresh fixture.
 *
 * Re-imported per test rather than once for the file: the gate's constants
 * are read at parse time (the flag and the session are fixed for the life
 * of a document, unlike connectivity), so a test that changes them has to
 * run the IIFE again.
 */
async function boot({ eligible, cached, surface = true }) {
  buildFixture({ eligible: eligible, surface: surface });
  const mapStub = stubMapLibre(
    // A VECTOR source whose runtime `tiles` array resolves — that is the
    // exact shape `activeBasemapTileTemplate` reads, and without it the
    // probe answers "can't tell yet" and never reaches 'done'.
    { basemap: { type: 'vector' } },
  );
  const tileURL = TEMPLATE.replace('{z}', '14').replace('{x}', '8577').replace('{y}', '5811');
  installCachesStub(cached ? { [BUCKET]: [tileURL] } : {});
  installDbStub(
    cached
      ? {
          'basemap.regions': [
            {
              region_id: REGION_ID,
              name: 'Martigny — Verbier',
              band: [10, 14],
              z: { 14: [8577, 8577, 5811, 5811] },
              template: TEMPLATE,
              basemapKey: 'standard',
              bytes: 4 * MB,
              savedAt: '2026-08-01T10:00:00.000Z',
            },
          ],
        }
      : {},
  );
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * MB, usage: 0 }) },
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
  for (const handler of mapStub.handlers.load || []) await handler();

  document.dispatchEvent(
    new CustomEvent('snowdesk:region-selected', { detail: { region_id: REGION_ID } }),
  );
  return document.getElementById('map-download-control');
}

let realLocation;
let assign;

beforeEach(() => {
  realLocation = window.location;
  assign = vi.fn();
  // jsdom refuses a real navigation, and will not let a spy take over
  // `assign` alone, so the whole `location` object is replaced. Every
  // readable field is copied across rather than invented: the map bundle
  // reads `hash` and `search` at parse time, and a partial stand-in fails
  // there with an error that names neither this file nor the gate.
  Object.defineProperty(window, 'location', {
    value: {
      assign: assign,
      replace: vi.fn(),
      reload: vi.fn(),
      href: realLocation.href,
      origin: realLocation.origin,
      protocol: realLocation.protocol,
      host: realLocation.host,
      hostname: realLocation.hostname,
      port: realLocation.port,
      pathname: realLocation.pathname,
      search: realLocation.search,
      hash: realLocation.hash,
      toString: () => realLocation.href,
    },
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  Object.defineProperty(window, 'location', {
    value: realLocation,
    configurable: true,
    writable: true,
  });
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete globalThis.maplibregl;
});

describe('signed out with the gate on', () => {
  it('paints the signin state instead of idle', async () => {
    const btn = await boot({ eligible: false, cached: false });

    await waitFor(() => btn.dataset.downloadState === 'signin');
    expect(btn.dataset.downloadState).toBe('signin');
  });

  it('stays actionable rather than announcing itself disabled', async () => {
    // The show-controls rule: a gate the visitor can pass is an
    // affordance. Announcing aria-disabled would be the same mistake as
    // hiding the button, told to a screen reader instead of an eye.
    const btn = await boot({ eligible: false, cached: false });

    await waitFor(() => btn.dataset.downloadState === 'signin');
    expect(btn.getAttribute('aria-disabled')).toBe('false');
    expect(btn.hidden).toBe(false);
  });

  it('labels itself as needing a sign-in, and names no size', async () => {
    // The megabyte figure answers "is this worth downloading", which is
    // not the question in front of someone who cannot download at all.
    const btn = await boot({ eligible: false, cached: false });

    await waitFor(() => btn.dataset.downloadState === 'signin');
    expect(btn.getAttribute('aria-label')).toContain('Sign in');
    expect(btn.getAttribute('aria-label')).not.toContain('MB');
  });

  it('sends the tap to sign-in rather than starting a run', async () => {
    const btn = await boot({ eligible: false, cached: false });

    await waitFor(() => btn.dataset.downloadState === 'signin');
    btn.click();
    await tick(10);

    expect(assign).toHaveBeenCalledWith(SIGNIN_URL);
    // And no run started. Asserted on the painted state rather than on
    // whether the blob endpoint was fetched: the done-probe fetches that
    // same endpoint for a region with no stored record, so a fetch spy
    // cannot tell a probe from a run. A run's first visible act is
    // 'busy'.
    await tick(50);
    expect(btn.dataset.downloadState).toBe('signin');
  });

  it('still reports a region that IS on this device as downloaded', async () => {
    // The load-bearing one. Reading an already-downloaded region is never
    // gated: a signed-out visitor out of signal must still see that their
    // tiles are there.
    const btn = await boot({ eligible: false, cached: true });

    await waitFor(() => btn.dataset.downloadState === 'done');
    expect(btn.dataset.downloadState).toBe('done');
  });
});

describe('signed in', () => {
  it('leaves the control actionable', async () => {
    const btn = await boot({ eligible: true, cached: false });

    await waitFor(() => btn.dataset.downloadState === 'idle');
    expect(btn.dataset.downloadState).toBe('idle');
    expect(btn.getAttribute('aria-disabled')).toBe('false');
  });
});

describe('a page with no downloads surface', () => {
  it('applies no gate rather than locking for want of an answer', async () => {
    // This control reads its eligibility off #map-custom-download-control,
    // which lives in a DIFFERENT partial. Absent it, the page carries no
    // downloads surface at all: no roundel, no sheet, nothing to sync and
    // no session to read. The control must fall back to its pre-SNOW-749
    // behaviour rather than paint an unanswerable gate — a permanently
    // signed-out-looking control on a page that never had an account
    // question to ask.
    //
    // NOT a flag-off case: SNOW-749's `download_sync` flag was dropped
    // before merge on query cost. This is the real remaining one.
    const btn = await boot({ eligible: false, cached: false, surface: false });

    await waitFor(() => btn.dataset.downloadState === 'idle');
    expect(btn.dataset.downloadState).toBe('idle');
  });
});
