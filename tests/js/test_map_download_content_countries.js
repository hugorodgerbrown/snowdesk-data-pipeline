/*
 * tests/js/test_map_download_content_countries.js — an area's content plan
 * considers every country's regions, not just the loaded ones (SNOW-931).
 *
 * SNOW-924 made an area's boundary the manifest for its content, and
 * `docs/decisions/inside-the-boundary-is-complete.md` states the contract
 * it rests on: inside the boundary, everything. The superset invariant that
 * backs it is pinned in `test_basemap_download_core.js` against 149 real CH
 * boundaries — but that test hands `areaContentPlan` the complete feed
 * directly, and `test_map_download_content.js` boots a fixture whose
 * `regions.geojson` stub answers the same whole FeatureCollection whatever
 * `?country=` asks for. Neither can see a candidate set that is short
 * before the geometry ever runs, which is exactly how SNOW-924 shipped
 * under-fetching the thing it was built to guarantee.
 *
 * So the fixture here differs from that one in exactly two ways, and both
 * are the point:
 *
 *   - the active basemap declares `data-basemap-countries="ch"`, as
 *     `swisstopo_winter` and `swisstopo_light` do in `BASEMAP_COUNTRIES`.
 *     Boot then loads CH and nothing else, where a fixture with the
 *     attribute absent falls back to all four and hides the bug;
 *   - the `regions.geojson` stub is keyed on `?country=`, so France exists
 *     on the server and is simply not on the client yet — the ordinary
 *     state of an Alpine map, not a contrived one.
 *
 * The area straddles the border. France's bulletin has to be in the posted
 * list; a user who downloads Martigny — Verbier and skis west into
 * Chamonix is the person this feature exists for, and a bulletin they
 * needed and do not have is discovered in a car park with no signal.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const CH_REGION_ID = 'CH-4115';
const CH_REGION_SLUG = 'martigny-verbier';
const FR_REGION_ID = 'FR-7401';
const FR_REGION_SLUG = 'chablais';
const TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';
const TODAY = '2026-01-06';

/**
 * The Swiss region, whose polygon is the ground REGION_BLOB's tiles cover.
 * Same golden vector as `test_map_download_content.js` — bbox 7.0,46.0 →
 * 7.2,46.2 at the micro band.
 */
const CH_REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {
        id: CH_REGION_ID,
        name: 'Martigny — Verbier',
        slug: CH_REGION_SLUG,
        country: 'CH',
        download: {
          count: 205,
          mb: 13,
          over_ceiling: false,
          centre_tile: { z: 14, x: 8515, y: 5822 },
        },
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[7.0, 46.0], [7.2, 46.0], [7.2, 46.2], [7.0, 46.2], [7.0, 46.0]]],
      },
    },
  ],
};

/**
 * The French region next door, overlapping the western edge of that same
 * ground. Never fetched at boot under a CH-only basemap, which is the whole
 * scenario.
 */
const FR_REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {
        id: FR_REGION_ID,
        name: 'Chablais',
        slug: FR_REGION_SLUG,
        country: 'FR',
        download: {
          count: 180,
          mb: 11,
          over_ceiling: false,
          centre_tile: { z: 14, x: 8495, y: 5822 },
        },
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[6.9, 46.05], [7.1, 46.05], [7.1, 46.15], [6.9, 46.15], [6.9, 46.05]]],
      },
    },
  ],
};

/** The blob for the Swiss region's ground — the golden vector's z14 row. */
const REGION_BLOB = {
  band: [10, 14],
  count: 205,
  mb: 13,
  over_ceiling: false,
  centre_tile: { z: 14, x: 8515, y: 5822 },
  z: { 14: [8510, 8519, 5815, 5828] },
};

const WEATHER_GEOJSON = { type: 'FeatureCollection', features: [] };
const EMPTY_GEOJSON = { type: 'FeatureCollection', features: [] };

/** Minimal MapLibre stub — as test_map_download_content.js's. */
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
    getLayoutProperty: () => null,
    getPaintProperty: () => null,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => (id === 'basemap' ? { tiles: [TEMPLATE] } : null),
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
    getStyle: () => ({ layers: [], sources: { basemap: { type: 'vector' } } }),
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

/** Cache Storage stub over `name -> Set<url>`. */
function installCachesStub() {
  const buckets = new Map();
  const openBucket = (name) => {
    if (!buckets.has(name)) buckets.set(name, new Set());
    return buckets.get(name);
  };
  Object.defineProperty(window, 'caches', {
    value: {
      buckets,
      keys: vi.fn(async () => [...buckets.keys()]),
      open: vi.fn(async (name) => {
        const store = openBucket(name);
        return {
          keys: async () => [...store].map((url) => ({ url })),
          put: vi.fn(async (url) => {
            store.add(url);
          }),
          match: async (request) => {
            const url = typeof request === 'string' ? request : request.url;
            return store.has(url) ? { headers: { get: () => null } } : undefined;
          },
        };
      }),
      delete: vi.fn(async (name) => buckets.delete(name)),
    },
    configurable: true,
    writable: true,
  });
}

/** In-memory `meta:app`. */
function installDbStub() {
  const rows = new Map();
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
}

/** The overlay cache the download writes its feeds through. */
function installOverlayCacheStub() {
  const stored = new Map();
  Object.defineProperty(window, 'pwaMapOverlayCache', {
    value: {
      stored,
      putOverlay: vi.fn(async (resource, geojson) => {
        stored.set(resource, geojson);
      }),
      getOverlay: vi.fn(async (resource) => stored.get(resource) || null),
    },
    configurable: true,
    writable: true,
  });
}

/**
 * The DOM the boot needs.
 *
 * `data-basemap-countries="ch"` is the load-bearing line: `map.js`'s
 * `boundaryCountryCodes` falls back to all four countries when the
 * attribute is absent, so a fixture without it boot-loads France and the
 * defect cannot reproduce.
 */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-weather-url="/api/weather.geojson"
         data-weather-detail-url="/api/weather/__SHORTID__/detail/"
         data-community-reports-url="/api/community-reports.geojson"
         data-community-reports-eligible="true"
         data-favourites-url="/api/favourites.geojson"
         data-favourites-eligible="true"
         data-routes-url="/api/routes.geojson"
         data-routes-eligible="true"
         data-default-basemap-key="swisstopo_winter"
         data-season-end="2026-05-31"></div>
    <div id="season-scrubber" data-today="${TODAY}" data-today-pct="50"
         data-season-start="2025-11-01" data-season-end="2026-05-31" data-state="ready">
      <div class="season-scrubber-track"><div class="season-scrubber-thumb"></div></div>
      <div class="season-scrubber-loading"></div>
    </div>
    <button id="map-download-control" type="button"></button>
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
          data-basemap-key="swisstopo_winter"
          data-basemap-countries="ch"
          data-basemap-url="https://tiles.example.invalid/swisstopo.json"
          aria-checked="true"
        >swisstopo winter</button>
      </li>
    </ul>
    <div id="map-download-error-toast" class="hidden"></div>`;
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

let mapStub;

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  installCachesStub();
  installDbStub();
  installOverlayCacheStub();
  window.pwaConnectivity = { isOnline: () => true };
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * 1024 * 1024, usage: 0 }) },
    configurable: true,
  });

  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const href = String(url);
      let body = {};
      // Keyed on ?country=, unlike test_map_download_content.js's stub —
      // the server holds France whether or not the client has asked.
      if (href.includes('regions.geojson')) {
        body = href.includes('country=fr') ? FR_REGIONS_GEOJSON : CH_REGIONS_GEOJSON;
      }
      if (href.includes('region-basemap-tiles')) body = REGION_BLOB;
      if (href.includes('weather.geojson')) body = WEATHER_GEOJSON;
      if (href.includes('favourites.geojson')) body = EMPTY_GEOJSON;
      if (href.includes('routes.geojson')) body = EMPTY_GEOJSON;
      if (href.includes('community-reports.geojson')) body = EMPTY_GEOJSON;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  window.pwaWarmCache = vi.fn(async (urls, options) => {
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) await cache.put(url, {});
    if (options.onProgress) {
      options.onProgress(urls.length, urls.length, urls.map((_u, i) => i), 4096);
    }
    return { ok: urls.length, failed: 0, bytes: 4096, cancelled: false };
  });

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/calendar_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaWarmCache;
  delete window.pwaConnectivity;
  delete window.pwaMapOverlayCache;
  delete globalThis.maplibregl;
});

describe('an area straddling a border takes both countries bulletins', () => {
  let posted;

  beforeAll(async () => {
    const btn = document.getElementById('map-download-control');
    document.dispatchEvent(
      new CustomEvent('snowdesk:region-selected', {
        detail: { region_id: CH_REGION_ID, region_name: 'Martigny — Verbier' },
      }),
    );
    await waitFor(() => btn.dataset.downloadState && btn.dataset.downloadState !== 'busy');
    btn.click();
    await waitFor(() => btn.dataset.downloadState !== 'busy');
    posted = window.pwaWarmCache.mock.calls[0][0];
  });

  it('posts the bulletin for the country the map booted with', () => {
    // The control case. This one passed before SNOW-931 too — Switzerland
    // is the country the boot load fetches, so it was never the gap.
    expect(posted).toContain(`/${CH_REGION_ID.toLowerCase()}/${CH_REGION_SLUG}/${TODAY}/`);
  });

  it('posts the bulletin for a country that was never loaded', () => {
    // THE assertion. France overlaps the area's western edge and exists on
    // the server, but a CH-only basemap never put it in
    // `featureByRegionId`, so before SNOW-931 it was not a candidate at
    // all — the rectangle test never got the chance to over-select it, and
    // the run still reported complete and stamped `contentAt`.
    expect(posted).toContain(`/${FR_REGION_ID.toLowerCase()}/${FR_REGION_SLUG}/${TODAY}/`);
  });

  it('reports the run complete, having actually been complete', async () => {
    // The stamp is what makes the gap invisible: weather comes from one
    // global feed that succeeds regardless, so a plan short of a whole
    // country still tallied `ok === total` and went green. Asserting the
    // stamp alongside the French bulletin above pins the pair — the roundel
    // may only claim completeness once the plan deserves it.
    const row = await window.pwaDb.get('meta:app', 'basemap.regions');
    const list = Array.isArray(row && row.value) ? row.value : [];
    const record = list.find((entry) => entry && entry.region_id === CH_REGION_ID);

    expect(record).toBeTruthy();
    expect(typeof record.contentAt).toBe('string');
  });
});
