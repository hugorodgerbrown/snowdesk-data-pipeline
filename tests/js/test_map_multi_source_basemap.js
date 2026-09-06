/*
 * tests/js/test_map_multi_source_basemap.js — SNOW-843: a basemap whose
 * style declares SEVERAL vector sources, each served from SEVERAL hosts,
 * is downloaded and read back under the URLs MapLibre will actually ask
 * for.
 *
 * The bug, from a real offline trace on the swisstopo winter basemap. Its
 * style declares two vector sources (`ch.swisstopo.relief.vt` and
 * `ch.swisstopo.base.vt`), each listing five hosts
 * (`vectortiles0-4.geo.admin.ch`) that MapLibre round-robins between per
 * tile — `urls[(x + y) % urls.length]`, see `CanonicalTileID.url` in
 * static/js/maplibre-gl.min.js. The download resolved ONE template
 * (`tiles[0]` of the first vector source) and pinned every tile under it,
 * so a "downloaded" region held:
 *
 *   - none of the base layer at all — no roads, labels or features;
 *   - one fifth of the relief layer, the tiles whose indices happen to sum
 *     to a multiple of five.
 *
 * Offline the map came up blank over a full pinned bucket, and the service
 * worker's own trace showed the shape of it exactly: `pinned.search
 * result=miss` for four hosts in five, `result=hit` for the fifth.
 *
 * The same trace also showed every one of those tiles classified
 * `unclassified` with a NON-empty allowlist: the origins registered with
 * the worker come from the basemap catalogue's style-document URLs
 * (`vectortiles.geo.admin.ch`), which is not where any tile is served
 * from. So swisstopo tiles were never opportunistically cached either.
 *
 * Booting map.js in jsdom follows test_map_download_bytes.js's pattern —
 * see its header for the general rationale. This file's stub differs in
 * the one way that matters: its style has TWO vector sources with FIVE
 * hosts each, which is what the whole suite is about.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const REGION_ID = 'CH-4115';
const PINNED_PREFIX = 'snowdesk-basemap-pinned-';

/** The five hosts swisstopo round-robins its vector tiles between. */
const HOSTS = [0, 1, 2, 3, 4].map((n) => `https://vectortiles${n}.geo.admin.ch`);
const RELIEF = HOSTS.map((h) => `${h}/tiles/ch.swisstopo.relief.vt/v1.0.0/{z}/{x}/{y}.pbf`);
const BASE = HOSTS.map((h) => `${h}/tiles/ch.swisstopo.base.vt/v1.0.0/{z}/{x}/{y}.pbf`);

/** The style document's own origin — deliberately NOT a tile host. */
const STYLE_URL = 'https://vectortiles.geo.admin.ch/styles/ch.swisstopo.winter/style.json';

/**
 * The TileJSON documents the two sources are declared by. swisstopo names
 * its tiles this way rather than inline, so these are what MapLibre has to
 * read before it knows any tile URL at all.
 */
const RELIEF_TILEJSON =
  'https://vectortiles.geo.admin.ch/tiles/ch.swisstopo.relief.vt/v1.0.0/tiles.json';
const BASE_TILEJSON =
  'https://vectortiles.geo.admin.ch/tiles/ch.swisstopo.base.vt/v1.0.0/tiles.json';

/**
 * The four tiles this suite downloads, chosen from the reported trace so
 * the host rotation varies across them: (x + y) % 5 is 2, 3, 3 and 4.
 */
const TILES = [
  [8520, 5827],
  [8521, 5827],
  [8520, 5828],
  [8521, 5828],
];

/** One region carrying a precomputed download summary, as /api/regions.geojson emits. */
const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: {
        id: REGION_ID,
        name: 'Martigny — Verbier',
        download: {
          count: 4,
          mb: 1,
          over_ceiling: false,
          centre_tile: { z: 14, x: 8520, y: 5827 },
        },
      },
      geometry: {
        type: 'Polygon',
        coordinates: [[[7.0, 46.0], [7.01, 46.0], [7.01, 46.01], [7.0, 46.01], [7.0, 46.0]]],
      },
    },
  ],
};

/** The blob /api/region-basemap-tiles/ would answer with — the four TILES. */
const REGION_BLOB = {
  band: [14, 14],
  count: 4,
  mb: 1,
  over_ceiling: false,
  centre_tile: { z: 14, x: 8520, y: 5827 },
  z: { 14: [8520, 8521, 5827, 5828] },
};

/**
 * The URL MapLibre itself would request for one tile of one source —
 * written out independently of the code under test rather than derived
 * from it, so the assertion is against MapLibre's rule and not against
 * our restatement of it.
 *
 * @param {string[]} urls One source's hosts, in style order.
 * @param {number} x
 * @param {number} y
 * @returns {string}
 */
function expectedTileURL(urls, x, y) {
  return urls[(x + y) % urls.length]
    .replace('{z}', '14')
    .replace('{x}', String(x))
    .replace('{y}', String(y));
}

/** Every URL a correct download of REGION_BLOB fetches, in no order. */
function expectedTileURLs() {
  const urls = [];
  for (const [x, y] of TILES) {
    urls.push(expectedTileURL(RELIEF, x, y));
    urls.push(expectedTileURL(BASE, x, y));
  }
  return urls;
}

/**
 * Minimal MapLibre stub with a two-source, five-host vector style — the
 * shape `activeBasemapTileSources` reads. `once` records its handlers (the
 * SNOW-843 origin learner hangs off a one-shot `idle`), unlike the
 * no-op in test_map_download_bytes.js's stub.
 */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: (ev, cb) => {
      (handlers[`once:${ev}`] ||= []).push(cb);
    },
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: () => null,
    getFilter: () => null,
    getLayoutProperty: () => null,
    getPaintProperty: () => null,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => {
      if (id === 'relief') return { tiles: [...RELIEF] };
      if (id === 'base') return { tiles: [...BASE] };
      return null;
    },
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
    getStyle: () => ({
      layers: [],
      // Order matters to the bug: `relief` first is what the old
      // first-source-only resolver stopped at.
      sources: {
        relief: { type: 'vector', url: RELIEF_TILEJSON },
        base: { type: 'vector', url: BASE_TILEJSON },
      },
      glyphs: 'https://vectortiles.geo.admin.ch/fonts/{fontstack}/{range}.pbf',
    }),
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

/** Cache Storage stub over `name -> Set<url>` — this suite only reads keys. */
function installCachesStub() {
  const buckets = new Map();
  const openBucket = (name) => {
    if (!buckets.has(name)) buckets.set(name, new Set());
    return buckets.get(name);
  };
  const stub = {
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
  };
  Object.defineProperty(window, 'caches', {
    value: stub,
    configurable: true,
    writable: true,
  });
  return stub;
}

/** In-memory `meta:app`. */
function installDbStub() {
  const rows = new Map();
  window.pwaDb = {
    rows,
    get: vi.fn(async (_store, key) =>
      rows.has(key) ? { key, value: rows.get(key) } : undefined,
    ),
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
 * A service-worker stub that records every `register-basemap-origins`
 * message, so the allowlist the page hands the worker can be asserted.
 */
function installServiceWorkerStub() {
  const posted = [];
  const registration = {
    active: {
      postMessage: (message) => posted.push(message),
    },
  };
  Object.defineProperty(navigator, 'serviceWorker', {
    value: {
      ready: Promise.resolve(registration),
      getRegistration: async () => registration,
      addEventListener: () => {},
    },
    configurable: true,
  });
  return posted;
}

/** The DOM map.js's boot and the region control read. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="swisstopo_winter"
         data-season-end="2026-05-31"></div>
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
          data-basemap-url="${STYLE_URL}"
          aria-checked="false"
        >Swisstopo (CH)</button>
      </li>
    </ul>
    <div id="map-download-evict-confirm" class="hidden" data-overlay data-overlay-hide="class">
      <p id="map-download-evict-confirm-title">Free up space?</p>
      <p id="map-download-evict-confirm-body"></p>
      <button id="map-download-evict-confirm-cta" type="button">Delete and download</button>
      <button type="button" data-action="dismiss">&times;</button>
    </div>`;
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

let mapStub;
let cachesStub;
let core;
let postedMessages;
let warmedURLs;

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
  cachesStub = installCachesStub();
  installDbStub();
  postedMessages = installServiceWorkerStub();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * 1024 * 1024, usage: 0 }) },
    configurable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const href = String(url);
      let body = {};
      if (href.includes('regions.geojson')) body = REGIONS_GEOJSON;
      if (href.includes('region-basemap-tiles')) body = REGION_BLOB;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );
  warmedURLs = [];
  window.pwaWarmCache = vi.fn(async (urls, options) => {
    warmedURLs = [...urls];
    const cache = await window.caches.open(PINNED_PREFIX + options.areaId);
    for (const url of urls) await cache.put(url, { headers: { get: () => null } });
    return { ok: urls.length, failed: 0, bytes: 4096, cancelled: false };
  });

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/basemap_download_runner.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  core = window.pwaBasemapDownloadCore;
  // MapLibre never fires these in jsdom; the data load hangs off 'load',
  // and the origin learner off 'style.load' plus the one-shot 'idle' it
  // registers.
  for (const handler of mapStub.handlers.load || []) await handler();
  for (const handler of mapStub.handlers['style.load'] || []) await handler();
  for (const handler of mapStub.handlers['once:idle'] || []) await handler();
  await waitFor(() => postedMessages.length > 0);
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete window.pwaWarmCache;
  delete globalThis.maplibregl;
});

describe('the tile URLs a download fetches', () => {
  it('covers every vector source at the host MapLibre will ask each tile from', async () => {
    const btn = document.getElementById('map-download-control');
    document.dispatchEvent(
      new CustomEvent('snowdesk:region-selected', {
        detail: { region_id: REGION_ID, region_name: 'Martigny — Verbier' },
      }),
    );
    await waitFor(() => btn.dataset.downloadState === 'idle');
    btn.click();
    await waitFor(() => btn.dataset.downloadState === 'done', 5000);
    expect(btn.dataset.downloadState).toBe('done');

    // Both sources, and the rotated host for each tile — eight URLs for
    // four tiles. The old resolver fetched four, all from `vectortiles0`,
    // all relief.
    const tiles = warmedURLs.filter((url) => url.endsWith('.pbf'));
    expect(new Set(tiles)).toEqual(new Set(expectedTileURLs()));
    expect(tiles).toHaveLength(8);

    // Named explicitly, because "one fifth of one layer" was the failure:
    // the base source is present at all, and more than one host is used.
    expect(tiles.filter((url) => url.includes('base.vt'))).toHaveLength(4);
    const hosts = new Set(tiles.map((url) => new URL(url).origin));
    expect(hosts).toEqual(new Set([HOSTS[2], HOSTS[3], HOSTS[4]]));
  });

  it("pins each source's TileJSON, without which no tile URL is knowable", () => {
    // A hard dependency of rendering, and one the passive cache's FIFO trim
    // evicts within a couple of browsing sessions — the glyph decay
    // SNOW-742 fixed, one document further up. Without it the map is blank
    // offline however complete the tile download was.
    expect(warmedURLs).toContain(RELIEF_TILEJSON);
    expect(warmedURLs).toContain(BASE_TILEJSON);
    // And the style document itself, as before.
    expect(warmedURLs).toContain(STYLE_URL);
  });

  it('reads the area back as downloaded from what it actually pinned', async () => {
    // The done-probe (`blobFullyCached`) has to agree with the fetcher
    // about which URLs the blob's tiles live under, or a complete download
    // reads as absent — the roundel above already asserts the agreement,
    // this pins the two halves together directly.
    const areaId = core.areaIdForRegion(REGION_ID);
    const cached = [...cachesStub.buckets.get(PINNED_PREFIX + areaId)];
    const sources = [RELIEF, BASE];

    expect(core.blobFullyCached(sources, REGION_BLOB, cached)).toBe(true);
    // One source's tiles alone are not the area being available offline.
    const reliefOnly = cached.filter((url) => url.includes('relief.vt'));
    expect(core.blobFullyCached(sources, REGION_BLOB, reliefOnly)).toBe(false);
  });
});

/*
 * SNOW-844: the composed probe — tiles AND the documents the map needs to
 * draw them. The suite above proves the DOWNLOAD fetches all four; this
 * proves the VERIFICATION asks about all four, which is the half that was
 * missing and the reason an area downloaded before SNOW-843 still reads
 * `done` today with no TileJSON in its bucket at all.
 */
describe('the render-dependency probe', () => {
  /** The area's own pinned bucket, as a fresh Set. */
  function pinnedURLs() {
    const areaId = core.areaIdForRegion(REGION_ID);
    return new Set(cachesStub.buckets.get(PINNED_PREFIX + areaId));
  }

  /** Every render dependency this style declares, as the download fetched them. */
  const DEPENDENCIES = [STYLE_URL, RELIEF_TILEJSON, BASE_TILEJSON];

  it('reports nothing missing for the area the run just completed', () => {
    expect(core.missingRenderDependencies(DEPENDENCIES, pinnedURLs())).toEqual([]);
  });

  it("names a source's TileJSON as missing when its bucket has lost it", () => {
    // The passive basemap cache is FIFO-trimmed and pinned buckets are
    // not, but an area downloaded before SNOW-843 never pinned this
    // document in the first place. Either way MapLibre offline cannot
    // learn a single tile URL, so the map is blank over a full bucket —
    // while `blobFullyCached` still, correctly, reports every tile there.
    const cached = pinnedURLs();
    cached.delete(BASE_TILEJSON);

    expect(core.missingRenderDependencies(DEPENDENCIES, cached)).toEqual([BASE_TILEJSON]);
    // The tile half of the answer is unchanged — which is precisely why
    // asking it alone reported an unusable area as available offline.
    const tiles = [...cached].filter((url) => url.endsWith('.pbf'));
    expect(core.blobFullyCached([RELIEF, BASE], REGION_BLOB, tiles)).toBe(true);
  });

  it('names the style document, without which nothing resolves at all', () => {
    const cached = pinnedURLs();
    cached.delete(STYLE_URL);
    expect(core.missingRenderDependencies(DEPENDENCIES, cached)).toEqual([STYLE_URL]);
  });

  it('never reports a glyph range missing (SNOW-847, not this ticket)', () => {
    // Glyphs are deliberately outside the dependency list: MapLibre
    // requests only the unicode ranges its labels use, so the honest set
    // is not enumerable, and SNOW-742 PROMOTES whatever was already cached
    // rather than fetching it. A check over a legitimately partial set
    // would report a permanent, unrepairable fault. Asserted rather than
    // assumed so a later ticket cannot fold them in by reflex.
    const glyphs = [...pinnedURLs()].filter((url) => url.endsWith('.pbf') && url.includes('fonts'));
    expect(glyphs).toEqual([]);
    for (const url of core.missingRenderDependencies(DEPENDENCIES, new Set())) {
      expect(url).not.toContain('/fonts/');
    }
  });
});

describe('the origins registered with the service worker', () => {
  it('include every host the live style fetches tiles from', () => {
    const origins = postedMessages
      .filter((message) => message.type === 'register-basemap-origins')
      .flatMap((message) => message.origins);

    for (const host of HOSTS) expect(origins).toContain(host);
    // The style DOCUMENT's own origin stays in the list — it serves the
    // sprite and glyphs — but it was never the one that mattered for tiles.
    expect(origins).toContain('https://vectortiles.geo.admin.ch');
  });
});

/*
 * SNOW-844, end to end on the roundel: an area whose bucket has lost one
 * source's TileJSON reads `incomplete` rather than `done`, and one tap
 * fetches back exactly that document — not the eight tiles beside it.
 *
 * Runs LAST in the file because it mutates the bucket the describes above
 * assert against.
 */
describe('an area that has lost a render dependency', () => {
  it('reads incomplete, and one tap repairs just the missing document', async () => {
    const btn = document.getElementById('map-download-control');
    const areaId = core.areaIdForRegion(REGION_ID);
    cachesStub.buckets.get(PINNED_PREFIX + areaId).delete(BASE_TILEJSON);

    // Re-probe the same region: the tiles are all still there, so nothing
    // about the tile answer has changed.
    document.dispatchEvent(
      new CustomEvent('snowdesk:region-selected', {
        detail: { region_id: REGION_ID, region_name: 'Martigny — Verbier' },
      }),
    );
    await waitFor(() => btn.dataset.downloadState === 'incomplete');
    expect(btn.dataset.downloadState).toBe('incomplete');
    // Actionable, because the remedy is one tap — a warning the user
    // cannot act on is worse than no warning.
    expect(btn.getAttribute('aria-disabled')).toBe('false');

    warmedURLs = [];
    btn.click();
    await waitFor(() => btn.dataset.downloadState === 'done', 5000);

    expect(btn.dataset.downloadState).toBe('done');
    // The repair's whole point: one document, not a re-download.
    expect(warmedURLs).toEqual([BASE_TILEJSON]);
  });
});
