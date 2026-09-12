/*
 * tests/js/test_basemap_base_layer_plan.js — SNOW-929: the base layer is
 * planned WITH the documents that draw it, and its bucket is judged stale
 * on its tiles alone.
 *
 * The bug this is the regression coverage for. `warmBaseLayerWideBand`
 * fetches a basemap's wide band the first time that basemap is shown — the
 * one download every user gets whether or not they ever ask for an area —
 * and it pinned tiles and nothing else. Measured on a cold origin:
 * `openfreemap_liberty` 56 tiles / 20.9 MB, `swisstopo_winter` 430 / 14.8,
 * `ign_plan` 681 / 8.1, `basemap_at` 93 / 11.7, every bucket holding `.pbf`
 * entries only. The four documents MapLibre needs before it can draw a
 * single one of those tiles — the style JSON, each vector source's
 * TileJSON, the sprite JSON+PNG, the glyph ranges — sat in the unpinned
 * `snowdesk-basemap-v1` passive cache, which is FIFO-trimmed and
 * evictable; and on a FIRST visit the style and sprite were not cached at
 * all, because MapLibre asks for them before the worker is in control. So
 * the device held a band it could not render, and every surface called it
 * downloaded.
 *
 * The area download path has answered this since SNOW-843/847. This suite
 * asserts the base layer now asks the same question, through the same
 * composer, and that SNOW-863's re-banding migration still fires — the two
 * pull in opposite directions, which is why they are asserted together:
 *
 *   - the plan carries the documents, so the band is renderable;
 *   - the staleness check ignores them, so a provider renaming a sprite
 *     path cannot bin a 21 MB band. Folding the document list into the
 *     expected set would have done exactly that, since the documents are
 *     derived from the LIVE style and move whenever the provider moves
 *     them.
 *
 * Harness follows `_load_map_bundle.js` the way
 * test_map_download_record_completion.js does — see that file's header,
 * and `_load_map_bundle.js`'s own, for why the map bundle is evaluated in
 * one shared function scope rather than imported. The fixture is closest
 * to test_basemap_custom_areas.js's, which already drives
 * `baseLayerPlan()`; what is added here is a style that actually declares
 * the four document classes, so the plan has real documents to carry.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { loadMapBundle } from './_load_map_bundle.js';

// The RENDERED style's tile template, and the four document classes it
// declares. Shaped after OpenFreeMap Liberty, which is the default basemap
// and the one whose 20.9 MB band is the thing worth not re-downloading.
const BASE_TEMPLATE = 'https://tiles.example.invalid/{z}/{x}/{y}.pbf';
const TILEJSON_URL = 'https://tiles.example.invalid/planet';
const SPRITE_BASE = 'https://tiles.example.invalid/sprites/ofm_f384/ofm';
const GLYPH_TEMPLATE = 'https://tiles.example.invalid/fonts/{fontstack}/{range}.pbf';
const FONTSTACK = 'Noto Sans Regular';

// The picker row's `data-basemap-url` — where
// `activeBasemapRenderDependencyURLs` reads the style url from, because
// MapLibre does not expose the url a loaded style came from.
const STYLE_URL = 'https://tiles.example.invalid/liberty.json';

const BASE_BUCKET = 'snowdesk-basemap-pinned-base-openfreemap_liberty';

const MB = 1024 * 1024;

/** The style `map.getStyle()` hands back — one vector source, all four document classes. */
const STYLE = {
  sources: { basemap: { type: 'vector', url: TILEJSON_URL } },
  sprite: SPRITE_BASE,
  glyphs: GLYPH_TEMPLATE,
  layers: [{ id: 'place-label', layout: { 'text-font': [FONTSTACK] } }],
};

/** Minimal MapLibre stub — see test_map_download_eviction.js for the full rationale. */
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
    // `bounds: null` is the global default basemap's real case — the
    // camera is then the only bound, which is what makes the band 56
    // tiles at z0-7 rather than the planet.
    getSource: (id) => (id === 'basemap' ? { tiles: [BASE_TEMPLATE], bounds: null } : null),
    getMaxBounds: () => ({
      toArray: () => [
        [0.9482, 41.9952],
        [19.6674, 49.9983],
      ],
    }),
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
    getStyle: () => STYLE,
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

/** Minimal Cache Storage stub — a bucket a test can seed and read back. */
function installCachesStub() {
  const buckets = new Map();
  const stub = {
    buckets,
    keys: vi.fn(async () => [...buckets.keys()]),
    open: vi.fn(async (name) => {
      if (!buckets.has(name)) buckets.set(name, new Set());
      return {
        keys: async () => [...buckets.get(name)].map((url) => ({ url })),
        put: async () => {},
        match: async () => undefined,
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

/** In-memory `meta:app`, seeded with whatever a test wants already on disk. */
function installDbStub(initial) {
  const rows = new Map(Object.entries(initial || {}));
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

/** The map div map.js's boot reads, plus the picker row carrying the style url. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <ul id="basemap-menu">
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="openfreemap_liberty"
          data-basemap-url="${STYLE_URL}"
          aria-checked="true"
        >OpenFreeMap</button>
      </li>
    </ul>`;
}

let cachesStub;
let core;

/** The whole document list the live style implies, in the plan's own order. */
function expectedDeps() {
  return [
    STYLE_URL,
    `${SPRITE_BASE}.json`,
    `${SPRITE_BASE}.png`,
    `${SPRITE_BASE}@2x.json`,
    `${SPRITE_BASE}@2x.png`,
    TILEJSON_URL,
    ...core.glyphURLs(STYLE),
  ];
}

beforeEach(async () => {
  buildFixture();
  stubMapLibre();
  cachesStub = installCachesStub();
  installDbStub({});
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * MB, usage: 0 }) },
    configurable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })),
  );

  vi.resetModules();
  await import('../../static/js/i18n_strings.js');
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  core = self.pwaBasemapDownloadCore;
});

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete globalThis.maplibregl;
  Reflect.deleteProperty(window, 'caches');
});

describe('what the base-layer plan fetches (SNOW-929)', () => {
  it('carries the band, which is what it always carried', async () => {
    // Asserted first and by count, so every assertion below reads as an
    // ADDITION rather than as a possible substitution: the documents must
    // not arrive at the expense of a single tile.
    const plan = await window.pwaBasemapDownloads.baseLayerPlan();
    const tiles = plan.urls.filter((url) => core.isTileEntryURL(url));

    expect(tiles).toHaveLength(56);
    expect(tiles).toContain('https://tiles.example.invalid/0/0/0.pbf');
  });

  it('carries the style document, which a first visit never had cached', async () => {
    // The worst case of the four: MapLibre requests the style before the
    // service worker is in control, so on a first visit it was in no cache
    // at all — and with no style there is no map, however many tiles are
    // pinned.
    const plan = await window.pwaBasemapDownloads.baseLayerPlan();

    expect(plan.urls).toContain(STYLE_URL);
  });

  it("carries each vector source's TileJSON", async () => {
    // SNOW-843's bug class, one layer down: with the TileJSON uncached,
    // MapLibre offline cannot learn a single tile url, so a perfect band is
    // unreachable.
    const plan = await window.pwaBasemapDownloads.baseLayerPlan();

    expect(plan.urls).toContain(TILEJSON_URL);
  });

  it('carries the sprite pair at 1x and 2x', async () => {
    const plan = await window.pwaBasemapDownloads.baseLayerPlan();

    expect(plan.urls).toContain(`${SPRITE_BASE}.json`);
    expect(plan.urls).toContain(`${SPRITE_BASE}.png`);
    expect(plan.urls).toContain(`${SPRITE_BASE}@2x.json`);
    expect(plan.urls).toContain(`${SPRITE_BASE}@2x.png`);
  });

  it("carries the glyph ranges, so the band's labels survive a trim", async () => {
    // SNOW-742's decay: browsing does cache the ranges, into a cache that
    // is FIFO-trimmed while pinned buckets never are. A couple of sessions
    // and the band renders as geometry with no labels, its tiles intact.
    const plan = await window.pwaBasemapDownloads.baseLayerPlan();
    const glyphs = core.glyphURLs(STYLE);

    expect(glyphs).toHaveLength(core.GLYPH_RANGES.length);
    for (const url of glyphs) expect(plan.urls).toContain(url);
  });

  it('exposes the document list whole, for the record to store', async () => {
    // `recordBaseLayer` writes `plan.deps`, and a reader later asks "what
    // does this bucket need?" — so this is the full list, not the subset a
    // given top-up happened to find missing.
    const plan = await window.pwaBasemapDownloads.baseLayerPlan();

    expect(plan.deps).toEqual(expectedDeps());
  });
});

describe('what the base-layer plan skips (SNOW-929)', () => {
  it('omits an already-pinned document as well as an already-pinned tile', async () => {
    // The missing-only half is what makes the base layer a one-off cost
    // rather than a tax on every basemap switch, and it has to hold for
    // both halves of the list — re-fetching a 60 KB style on every switch
    // would be a new cost on a path that had none.
    const cachedTile = 'https://tiles.example.invalid/0/0/0.pbf';
    cachesStub.buckets.set(BASE_BUCKET, new Set([cachedTile, STYLE_URL, TILEJSON_URL]));

    const plan = await window.pwaBasemapDownloads.baseLayerPlan();

    expect(plan.urls).not.toContain(cachedTile);
    expect(plan.urls).not.toContain(STYLE_URL);
    expect(plan.urls).not.toContain(TILEJSON_URL);
    expect(plan.urls.filter((url) => core.isTileEntryURL(url))).toHaveLength(55);
    // Still DECLARED, though: the record states what the bucket needs, and
    // a document found already on disk is one the bucket needs and has.
    expect(plan.deps).toEqual(expectedDeps());
  });

  it('plans nothing at all once the band and its documents are pinned', async () => {
    // The state every later switch back to this basemap is in, and the
    // one `warmBaseLayerWideBand` reads as "fetch nothing".
    const all = await window.pwaBasemapDownloads.baseLayerPlan();
    cachesStub.buckets.set(BASE_BUCKET, new Set(all.urls));

    const plan = await window.pwaBasemapDownloads.baseLayerPlan();

    expect(plan.urls).toEqual([]);
  });
});

describe('re-banding still fires, and only on tiles (SNOW-929)', () => {
  it("drops a bucket holding a previous band's tiles", async () => {
    // SNOW-863's migration, unchanged: SNOW-856 shipped z0-9 against a
    // default band of z0-7, the old set is a SUPERSET so the missing-url
    // plan finds nothing, and without this the extra tiles sit there for
    // the life of the install.
    cachesStub.buckets.set(
      BASE_BUCKET,
      new Set(['https://tiles.example.invalid/9/266/181.pbf']),
    );

    const plan = await window.pwaBasemapDownloads.baseLayerPlan();

    expect(cachesStub.buckets.has(BASE_BUCKET)).toBe(false);
    expect(plan.urls.filter((url) => core.isTileEntryURL(url))).toHaveLength(56);
  });

  it('leaves a bucket holding this band plus its documents alone', async () => {
    // The SNOW-929 case, and the one that would have broken had the
    // documents been folded into the expected set: every document is
    // outside the tile set by construction, so the check would have read
    // the bucket as another band's and evicted the band that had just been
    // fetched — on every single warm.
    const all = await window.pwaBasemapDownloads.baseLayerPlan();
    cachesStub.buckets.set(BASE_BUCKET, new Set(all.urls));

    const plan = await window.pwaBasemapDownloads.baseLayerPlan();

    expect(cachesStub.buckets.has(BASE_BUCKET)).toBe(true);
    expect(plan.urls).toEqual([]);
  });

  it('leaves a bucket alone over a document the plan does not name', async () => {
    // A provider renaming a sprite path or adding a fontstack moves the
    // document list, because it is derived from the live style. Under the
    // old all-entries check that was an unexpected entry and the user paid
    // for the whole band again; here it costs nothing.
    const all = await window.pwaBasemapDownloads.baseLayerPlan();
    cachesStub.buckets.set(
      BASE_BUCKET,
      new Set([
        ...all.urls,
        'https://tiles.example.invalid/sprites/ofm_f999/renamed@2x.png',
        'https://tiles.example.invalid/fonts/Some%20New%20Stack/0-255.pbf',
      ]),
    );

    const plan = await window.pwaBasemapDownloads.baseLayerPlan();

    expect(cachesStub.buckets.has(BASE_BUCKET)).toBe(true);
    expect(plan.urls).toEqual([]);
  });
});
