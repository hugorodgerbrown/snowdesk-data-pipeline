/*
 * tests/js/test_map_basemap_origin_learning.js — SNOW-870: the page learns
 * a basemap's tile hosts WHENEVER the style can tell it, not at two fixed
 * moments that a TileJSON-backed source misses.
 *
 * `learnBasemapTileOrigins` (static/js/map.js) reads
 * `map.getSource(id).tiles` and hands the resulting origins to the service
 * worker, whose classifier needs them to mark a tile `basemap` and cache it
 * opportunistically. SNOW-843 called it twice per style: on `style.load`,
 * and from a one-shot `idle` registered inside it.
 *
 * Neither shot can land for swisstopo, which declares both of its vector
 * sources by `url` — a TileJSON document:
 *
 *   - at `style.load` that document has not been fetched, so `tiles` is
 *     empty. This is structural, not a race: it is never populated then.
 *   - `idle` needs a completed render pass. In the reported trace it never
 *     fired at all; on a device that does render it can arrive BEFORE the
 *     TileJSON resolves, learning exactly as little.
 *
 * So on the boot path — the path every real session takes — the five
 * `vectortiles0-4.geo.admin.ch` hosts never joined the allowlist, and every
 * swisstopo tile classified `unclassified` for the whole session.
 *
 * The fix is a permanently-bound `sourcedata` listener guarded on
 * `sourceDataType === 'metadata'`, which is the event MapLibre emits when a
 * source's TileJSON resolves. The guard is half the fix and has its own
 * case below: the `content` variant fires per tile, hundreds of times
 * during a pan, and each call runs `getStyle()` — the cost SNOW-614 took
 * out of the attribution handler.
 *
 * This file's stub differs from test_map_multi_source_basemap.js's in the
 * one way that matters: `getSource` returns a source with NO `tiles` until
 * the TileJSON is resolved, so it can model the gap at all. That file keeps
 * the already-resolved case.
 */

import { afterAll, afterEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

/** The five hosts swisstopo round-robins its vector tiles between. */
const HOSTS = [0, 1, 2, 3, 4].map((n) => `https://vectortiles${n}.geo.admin.ch`);
const RELIEF = HOSTS.map((h) => `${h}/tiles/ch.swisstopo.relief.vt/v1.0.0/{z}/{x}/{y}.pbf`);
const BASE = HOSTS.map((h) => `${h}/tiles/ch.swisstopo.base.vt/v1.0.0/{z}/{x}/{y}.pbf`);

/** The style document's own origin — in the catalogue, and NOT a tile host. */
const STYLE_ORIGIN = 'https://vectortiles.geo.admin.ch';
const STYLE_URL = `${STYLE_ORIGIN}/styles/ch.swisstopo.winter/style.json`;

/** The TileJSON documents the two sources are declared by. */
const RELIEF_TILEJSON = `${STYLE_ORIGIN}/tiles/ch.swisstopo.relief.vt/v1.0.0/tiles.json`;
const BASE_TILEJSON = `${STYLE_ORIGIN}/tiles/ch.swisstopo.base.vt/v1.0.0/tiles.json`;

/**
 * MapLibre stub whose sources are declared by `url` and whose runtime
 * `tiles` appear only once `state.tileJSONResolved` is set.
 *
 * That flag is the whole point of the stub: before it, `getSource(id)`
 * answers with a real source object that simply has no `tiles` yet, which
 * is what MapLibre hands back between `style.load` and the TileJSON
 * response.
 *
 * @param {{tileJSONResolved: boolean}} state Mutable resolution flag.
 * @returns {object} The stub map, with its recorded `handlers`.
 */
function stubMapLibre(state) {
  const handlers = {};
  const getStyle = vi.fn(() => ({
    layers: [],
    sources: {
      relief: { type: 'vector', url: RELIEF_TILEJSON },
      base: { type: 'vector', url: BASE_TILEJSON },
    },
    glyphs: `${STYLE_ORIGIN}/fonts/{fontstack}/{range}.pbf`,
  }));
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
      if (id !== 'relief' && id !== 'base') return null;
      // Declared by `url`, so until its TileJSON has been read the source
      // exists but knows no tile URL at all.
      if (!state.tileJSONResolved) return { type: 'vector' };
      return { type: 'vector', tiles: [...(id === 'relief' ? RELIEF : BASE)] };
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
    getStyle,
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

/**
 * A service-worker stub recording every message the page posts.
 *
 * @returns {object[]} The live array of posted messages.
 */
function installServiceWorkerStub() {
  const posted = [];
  const registration = { active: { postMessage: (message) => posted.push(message) } };
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

/**
 * In-memory `meta:app`, starting EMPTY.
 *
 * Empty matters: the durable seed (SNOW-843) would otherwise supply last
 * session's tile origins and hide whether this session learned any.
 *
 * @returns {Map<string, unknown>} The backing rows.
 */
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
  return rows;
}

/** The DOM map.js's boot reads. */
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
    </ul>`;
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  return predicate();
}

/**
 * Boot the map bundle over the unresolved-TileJSON stub.
 *
 * Deliberately does NOT fire the `load` handlers: this suite is about the
 * origin learner alone, and the data-load path registers `sourcedata`
 * listeners of its own that would blur both the event dispatch and the
 * `getStyle()` call count.
 *
 * @returns {Promise<object>} The stub map, the posted messages, and the
 *   helpers each case drives the timing with.
 */
async function boot() {
  buildFixture();
  const state = { tileJSONResolved: false };
  const map = stubMapLibre(state);
  installDbStub();
  const posted = installServiceWorkerStub();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 10 * 1024 * 1024 * 1024, usage: 0 }) },
    configurable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })),
  );

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // The catalogue seed is posted from a promise; let it settle so a later
  // message count is a real delta.
  await waitFor(() => posted.length > 0);

  /** Fire every `style.load` handler, as MapLibre does once per style. */
  const fireStyleLoad = async () => {
    for (const handler of map.handlers['style.load'] || []) await handler();
  };
  /** Fire every `sourcedata` handler with one `sourceDataType`. */
  const fireSourceData = async (sourceDataType) => {
    for (const handler of map.handlers.sourcedata || []) await handler({ sourceDataType });
  };
  /** Fire whatever one-shot `idle` handlers were registered — none, now. */
  const fireIdle = async () => {
    for (const handler of map.handlers['once:idle'] || []) await handler();
  };

  return {
    map,
    posted,
    state,
    fireStyleLoad,
    fireSourceData,
    fireIdle,
    /** Every origin the page has handed the worker, across all messages. */
    registeredOrigins: () =>
      posted
        .filter((message) => message.type === 'register-basemap-origins')
        .flatMap((message) => message.origins),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete window.pwaDb;
  delete globalThis.maplibregl;
});

afterAll(() => {
  document.body.innerHTML = '';
});

describe('learning a TileJSON-backed basemap’s tile origins', () => {
  it('learns nothing at style.load, then every host when the TileJSON resolves', async () => {
    const ctx = await boot();

    // The boot path exactly: the style has parsed, so its glyph and sprite
    // origins are readable, but no source knows a tile URL yet.
    await ctx.fireStyleLoad();
    await waitFor(() => false, 50);
    for (const host of HOSTS) expect(ctx.registeredOrigins()).not.toContain(host);
    // The catalogue seed is still there — this is a gap in what is known,
    // not a broken registration.
    expect(ctx.registeredOrigins()).toContain(STYLE_ORIGIN);

    // MapLibre reads the two TileJSON documents and says so.
    ctx.state.tileJSONResolved = true;
    await ctx.fireSourceData('metadata');
    await waitFor(() => ctx.registeredOrigins().includes(HOSTS[0]));

    for (const host of HOSTS) expect(ctx.registeredOrigins()).toContain(host);
  });

  it('learns them although no idle event ever arrives', async () => {
    // The reported trace. `idle` needs a completed render pass, and on that
    // device there was not one — under SNOW-843 that alone lost every tile
    // host for the session.
    const ctx = await boot();
    await ctx.fireStyleLoad();

    expect(ctx.map.handlers['once:idle'] || []).toHaveLength(0);

    ctx.state.tileJSONResolved = true;
    await ctx.fireSourceData('metadata');
    await waitFor(() => ctx.registeredOrigins().includes(HOSTS[4]));

    for (const host of HOSTS) expect(ctx.registeredOrigins()).toContain(host);
  });

  it('learns them although idle arrives before the TileJSON resolves', async () => {
    // The other half of the race, on a device that does render: `idle`
    // comes and goes while `tiles` is still empty, and SNOW-843 had spent
    // its second and last shot.
    const ctx = await boot();
    await ctx.fireStyleLoad();
    await ctx.fireIdle();
    for (const host of HOSTS) expect(ctx.registeredOrigins()).not.toContain(host);

    ctx.state.tileJSONResolved = true;
    await ctx.fireSourceData('metadata');
    await waitFor(() => ctx.registeredOrigins().includes(HOSTS[0]));

    for (const host of HOSTS) expect(ctx.registeredOrigins()).toContain(host);
  });

  it('ignores the per-tile sourcedata firehose without reading the style', async () => {
    // SNOW-614's cost, which this fix must not reintroduce: `content`
    // fires for every tile of every source — hundreds of times during a
    // pan — and `getStyle()` serialises the whole style object.
    const ctx = await boot();
    await ctx.fireStyleLoad();
    ctx.state.tileJSONResolved = true;
    await ctx.fireSourceData('metadata');
    await waitFor(() => ctx.registeredOrigins().includes(HOSTS[0]));

    const callsBefore = ctx.map.getStyle.mock.calls.length;
    for (let i = 0; i < 200; i += 1) await ctx.fireSourceData('content');

    expect(ctx.map.getStyle.mock.calls.length).toBe(callsBefore);
  });

  it('sends no further message for a metadata event that adds no origin', async () => {
    // The worker replaces its whole allowlist from each message, so a
    // message that changes nothing is work it does for no reason — and
    // `metadata` fires once per source, so a two-source style already
    // sends one redundant event.
    const ctx = await boot();
    await ctx.fireStyleLoad();
    ctx.state.tileJSONResolved = true;
    await ctx.fireSourceData('metadata');
    await waitFor(() => ctx.registeredOrigins().includes(HOSTS[0]));

    const messagesBefore = ctx.posted.length;
    await ctx.fireSourceData('metadata');
    await waitFor(() => false, 50);

    expect(ctx.posted).toHaveLength(messagesBefore);
  });
});
