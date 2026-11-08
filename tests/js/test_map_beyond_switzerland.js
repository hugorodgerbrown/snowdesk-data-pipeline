/*
 * tests/js/test_map_beyond_switzerland.js — the winter map past the Swiss
 * border on /map/ (SNOW-1460, static/js/map_beyond_switzerland.js).
 *
 * A stub MapLibre records `addProtocol` and holds a style the splice can
 * read and add to; a stub Worker records what the page posts and lets each
 * case answer it. The handlers are called directly, the way MapLibre calls
 * them (the test_map_terrain_filter_protocol.js pattern).
 *
 * What is pinned:
 *   - both protocols are registered once, and only on the flagged path;
 *   - the splice adds the OSM and relief sources and layers once, re-runs
 *     after a second style.load (a basemap swap), and adds nothing twice
 *     however many styledata events follow;
 *   - a handler starts its worker with an init, posts {id, url} and
 *     resolves with the bytes of the matching reply;
 *   - an error reply, or the worker failing, rejects;
 *   - an abort posts a cancel and rejects with an AbortError;
 *   - the 3D view is handed the Alpine gate;
 *   - with the map bundle booted, the attribution panel names
 *     OpenStreetMap once the splice has added its source (map.js's id cache
 *     is cleared by a sourcedata for an id it does not hold).
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/swisstopo_extent.js';
import '../../static/js/beyond_switzerland_core.js';
import '../../static/js/osm_clip_core.js';
import '../../static/js/alpine_relief_core.js';
import { loadMapBundle } from './_load_map_bundle.js';

const core = window.pwaBeyondSwitzerlandCore;
const BASE_URL = 'https://vectortiles.geo.admin.ch/tiles/ch.swisstopo.base.vt/v1.0.0/tiles.json';
const RELIEF_URL = 'https://vectortiles.geo.admin.ch/tiles/ch.swisstopo.relief.vt/v1.0.0/tiles.json';
const SWISSTOPO_CREDIT = '© swisstopo';

/** A cut of the winter style: enough for a plan. */
function winterStyle() {
  return {
    version: 8,
    name: 'basemap-winter_v1.19.0',
    sprite: 'https://vectortiles.geo.admin.ch/styles/ch.swisstopo.basemap-winter.vt/v1.26.0/sprite/sprite',
    sources: {
      relief_v1: { type: 'vector', url: RELIEF_URL },
      base_v1: { type: 'vector', url: BASE_URL },
    },
    layers: [
      { id: 'background', type: 'background' },
      { id: 'hillshade_grey', type: 'fill', source: 'relief_v1', 'source-layer': 'hillshade' },
      { id: 'landcover', type: 'fill', source: 'base_v1', 'source-layer': 'landcover' },
      { id: 'road_fill', type: 'line', source: 'base_v1', 'source-layer': 'transportation' },
    ],
  };
}

/** Every Worker the page started, newest last. */
const workers = [];

/** A Worker stub: records posts, and lets a case send it replies. */
class StubWorker {
  /** @param {string} url */
  constructor(url) {
    this.url = url;
    this.posted = [];
    this.listeners = {};
    this.terminated = false;
    workers.push(this);
  }

  postMessage(message) {
    this.posted.push(message);
  }

  addEventListener(type, listener) {
    (this.listeners[type] ||= []).push(listener);
  }

  terminate() {
    this.terminated = true;
  }

  /** Answer as the worker would. */
  reply(data) {
    for (const listener of this.listeners.message || []) listener({ data });
  }

  /** Fail as a worker whose script throws would. */
  fail() {
    for (const listener of this.listeners.error || []) listener(new Event('error'));
  }

  /** The tile requests posted, without the init. */
  requests() {
    return this.posted.filter((message) => message.type !== 'init');
  }
}

/** The #map element map_beyond_switzerland.js reads its URLs from. */
function mapElement(flagged = true) {
  const attrs = flagged
    ? `data-map-beyond-switzerland="true"
       data-osm-clip-worker-url="/static/js/osm_clip_worker.js"
       data-alpine-relief-worker-url="/static/js/alpine_relief_worker.js"
       data-vector-tile-kit-url="/static/js/vector_tile_kit.min.js"
       data-osm-clip-core-url="/static/js/osm_clip_core.js"
       data-alpine-relief-core-url="/static/js/alpine_relief_core.js"
       data-osm-tilejson-url="https://tiles.openfreemap.org/planet"
       data-dem-tile-url="https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"`
    : '';
  return `<div id="map" ${attrs}></div>`;
}

/**
 * A map holding a style, with sources and layers the splice adds to.
 *
 * @returns {object} The stub, with `handlers`, `fire` and `swapStyle`.
 */
function stubMap() {
  const handlers = {};
  let style = winterStyle();
  const sources = new Map();
  const layers = [];
  const map = {
    handlers,
    addSource: vi.fn((id, def) => { sources.set(id, def); }),
    addLayer: vi.fn((layer, beforeId) => {
      const at = beforeId ? layers.findIndex((l) => l.id === beforeId) : -1;
      if (at >= 0) layers.splice(at, 0, layer);
      else layers.push(layer);
    }),
    getSource: (id) => sources.get(id) || null,
    getLayer: (id) => layers.find((l) => l.id === id) || null,
    isStyleLoaded: () => true,
    getStyle: () => ({ ...style, sources: Object.fromEntries(sources), layers: [...layers] }),
    on: (ev, cb) => { (handlers[ev] ||= []).push(cb); },
    fire: (ev, e) => { for (const cb of handlers[ev] || []) cb(e); },
    /** What a setStyle does: the old style's sources and layers go. */
    swapStyle() {
      style = winterStyle();
      sources.clear();
      layers.length = 0;
      for (const [id, def] of Object.entries(style.sources)) sources.set(id, def);
      layers.push(...style.layers);
    },
    layerIds: () => layers.map((l) => l.id),
  };
  map.swapStyle();
  return map;
}

/**
 * Load the runtime over a stub map.
 *
 * @param {{flagged?: boolean}} [options]
 * @returns {Promise<{map: object, maplibre: object, setCoverage: Function}>}
 */
async function boot({ flagged = true } = {}) {
  document.body.innerHTML = mapElement(flagged);
  const map = stubMap();
  const maplibre = { addProtocol: vi.fn() };
  const setCoverage = vi.fn();
  window.snowdeskMap = map;
  window.maplibregl = maplibre;
  window.pwaMapView = { setCoverage };
  vi.stubGlobal('Worker', StubWorker);
  vi.resetModules();
  await import('../../static/js/map_beyond_switzerland.js');
  return { map, maplibre, setCoverage };
}

/** The handler registered for a protocol. */
function handlerFor(maplibre, protocol) {
  const call = maplibre.addProtocol.mock.calls.find(([name]) => name === protocol);
  return call ? call[1] : null;
}

afterEach(() => {
  vi.unstubAllGlobals();
  workers.length = 0;
  delete window.snowdeskMap;
  delete window.maplibregl;
  delete window.pwaMapView;
  delete window.pwaBeyondSwitzerland;
});

describe('the protocols', () => {
  it('registers osmclip:// and alpinerelief:// once on the flagged path', async () => {
    const { maplibre } = await boot();
    const names = maplibre.addProtocol.mock.calls.map(([name]) => name);
    expect(names).toEqual(['osmclip', 'alpinerelief']);
  });

  it('registers nothing, and splices nothing, off the flag', async () => {
    const { map, maplibre, setCoverage } = await boot({ flagged: false });
    expect(maplibre.addProtocol).not.toHaveBeenCalled();
    expect(map.addSource.mock.calls.filter(([id]) => id.startsWith('beyond-'))).toEqual([]);
    expect(setCoverage).not.toHaveBeenCalled();
    expect(window.pwaBeyondSwitzerland).toBeUndefined();
  });
});

describe('the splice', () => {
  it('adds the OSM and relief sources and layers at once, when the style has loaded', async () => {
    const { map } = await boot();
    expect(map.getSource(core.OSM_SOURCE_ID).tiles).toEqual(['osmclip://{z}/{x}/{y}']);
    expect(map.getSource(core.RELIEF_SOURCE_ID)).toBeTruthy();
    const ids = map.layerIds();
    expect(ids.indexOf(core.RELIEF_LAYER_ID)).toBe(ids.indexOf('hillshade_grey') + 1);
    expect(ids).toContain(`${core.LAYER_PREFIX}road_fill`);
    expect(ids.indexOf(`${core.LAYER_PREFIX}road_fill`)).toBe(ids.indexOf('road_fill') + 1);
  });

  it('re-runs after a second style.load, adding nothing twice', async () => {
    const { map } = await boot();
    const added = map.addLayer.mock.calls.length;
    expect(added).toBeGreaterThan(0);
    // styledata fires many times on one style: nothing more is added.
    map.fire('styledata');
    map.fire('style.load');
    expect(map.addLayer.mock.calls.length).toBe(added);

    // A basemap swap drops the splice with the old style; it comes back once.
    map.swapStyle();
    map.fire('style.load');
    map.fire('styledata');
    map.fire('styledata');
    expect(map.addLayer.mock.calls.length).toBe(added * 2);
    const ids = map.layerIds();
    expect(new Set(ids).size).toBe(ids.length);
    expect(map.addSource.mock.calls.filter(([id]) => id === core.OSM_SOURCE_ID)).toHaveLength(2);
  });

  it('leaves a style that is not the winter one alone', async () => {
    const { map } = await boot();
    map.swapStyle();
    map.getStyle = () => ({ version: 8, name: 'OpenFreeMap Liberty', sources: {}, layers: [] });
    map.fire('style.load');
    expect(map.getSource(core.OSM_SOURCE_ID)).toBeNull();
  });

  it('hands the 3D view the Alpine gate', async () => {
    const { setCoverage } = await boot();
    expect(setCoverage).toHaveBeenCalledWith(core.ALPINE_GATE);
  });
});

describe('a protocol handler', () => {
  const TILE = 'osmclip://10/533/358';

  it('starts its worker with an init, posts the tile and resolves with the bytes', async () => {
    const { maplibre } = await boot();
    expect(workers).toHaveLength(0);
    const pending = handlerFor(maplibre, 'osmclip')({ url: TILE }, new AbortController());
    const [worker] = workers;
    expect(worker.url).toBe('/static/js/osm_clip_worker.js');
    expect(worker.posted[0]).toMatchObject({
      type: 'init',
      kitUrl: '/static/js/vector_tile_kit.min.js',
      coreUrl: '/static/js/osm_clip_core.js',
      tilejsonUrl: 'https://tiles.openfreemap.org/planet',
      extent: window.pwaSwisstopoExtent.EXTENT,
    });
    const [request] = worker.requests();
    expect(request).toEqual({ id: expect.any(Number), url: TILE });
    const bytes = new ArrayBuffer(8);
    worker.reply({ id: request.id, data: bytes });
    await expect(pending).resolves.toEqual({ data: bytes });
  });

  it('starts one worker per protocol, and reuses it', async () => {
    const { maplibre } = await boot();
    const osm = handlerFor(maplibre, 'osmclip');
    const relief = handlerFor(maplibre, 'alpinerelief');
    osm({ url: TILE }, new AbortController());
    osm({ url: 'osmclip://10/534/358' }, new AbortController());
    relief({ url: 'alpinerelief://10/533/358' }, new AbortController());
    expect(workers.map((w) => w.url)).toEqual([
      '/static/js/osm_clip_worker.js',
      '/static/js/alpine_relief_worker.js',
    ]);
    expect(workers[1].posted[0]).toMatchObject({
      type: 'init',
      coreUrl: '/static/js/alpine_relief_core.js',
      demTileUrl: 'https://tiles.mapterhorn.com/{z}/{x}/{y}.webp',
      relief: window.pwaSwisstopoExtent.DETAILED_RELIEF,
    });
    expect(workers[0].requests()).toHaveLength(2);
  });

  it('rejects on an error reply', async () => {
    const { maplibre } = await boot();
    const pending = handlerFor(maplibre, 'alpinerelief')({ url: 'alpinerelief://10/533/358' }, new AbortController());
    const [request] = workers[0].requests();
    workers[0].reply({ id: request.id, error: 'alpine_relief: elevation tile 503' });
    await expect(pending).rejects.toThrow('elevation tile 503');
  });

  it('rejects every request in flight when the worker fails, and starts a new one next', async () => {
    const { maplibre } = await boot();
    const osm = handlerFor(maplibre, 'osmclip');
    const first = osm({ url: TILE }, new AbortController());
    workers[0].fail();
    await expect(first).rejects.toThrow('failed');
    expect(workers[0].terminated).toBe(true);
    osm({ url: TILE }, new AbortController());
    expect(workers).toHaveLength(2);
  });

  it('posts a cancel on abort and rejects with an AbortError', async () => {
    const { maplibre } = await boot();
    const controller = new AbortController();
    const pending = handlerFor(maplibre, 'osmclip')({ url: TILE }, controller);
    const [request] = workers[0].requests();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(workers[0].posted.at(-1)).toEqual({ type: 'cancel', id: request.id });
    // A late reply for the aborted tile is dropped.
    workers[0].reply({ id: request.id, data: new ArrayBuffer(1) });
  });
});

/*
 * The attribution panel, with the real map bundle booted: map.js caches the
 * style's source ids at style.load, and the OSM source arrives after it.
 */
describe('the attribution, with the map bundle', () => {
  let bundleMap;

  /** A MapLibre stub the bundle boots over, with sources the splice adds to. */
  function bundleStub() {
    const handlers = {};
    const sources = new Map([
      ['base_v1', { type: 'vector', url: BASE_URL, attribution: SWISSTOPO_CREDIT }],
      ['relief_v1', { type: 'vector', url: RELIEF_URL, attribution: SWISSTOPO_CREDIT }],
    ]);
    const layers = [...winterStyle().layers];
    const map = {
      handlers,
      on: (ev, a, b) => { (handlers[ev] ||= []).push(typeof a === 'function' ? a : b); },
      once: () => {},
      off: () => {},
      addControl: () => {},
      removeControl: () => {},
      getLayer: (id) => layers.find((l) => l.id === id) || null,
      getFilter: () => null,
      getLayoutProperty: () => null,
      getPaintProperty: () => null,
      getFeatureState: () => ({}),
      isSourceLoaded: () => true,
      getSource: (id) => sources.get(id) || null,
      addSource: (id, def) => { sources.set(id, def); },
      addLayer: (layer) => { layers.push(layer); },
      removeLayer: () => {},
      removeSource: () => {},
      setLayoutProperty: () => {},
      setPaintProperty: () => {},
      setFilter: () => {},
      setFeatureState: () => {},
      removeFeatureState: () => {},
      setStyle: () => {},
      isStyleLoaded: () => true,
      getStyle: () => ({ ...winterStyle(), sources: Object.fromEntries(sources), layers: [...layers] }),
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
      getCenter: () => ({ lng: 10.3, lat: 47.1 }),
      getBounds: () => ({ getWest: () => 9, getSouth: () => 46.5, getEast: () => 11.5, getNorth: () => 47.6 }),
      project: () => ({ x: 0, y: 0 }),
      unproject: () => ({ lng: 10.3, lat: 47.1 }),
      queryRenderedFeatures: () => [],
      resize: () => {},
    };
    window.maplibregl = {
      Map: function () { return map; },
      Popup: function () {
        return { setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }), remove: () => {} };
      },
      GeolocateControl: function () { return { on: () => {} }; },
      AttributionControl: function () { return {}; },
      MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
      addProtocol: vi.fn(),
    };
    return map;
  }

  it('names OpenStreetMap once the splice has added its source', async () => {
    document.body.innerHTML = `
      ${mapElement(true).replace('<div id="map"', `<div id="map"
        data-regions-url="/api/regions.geojson"
        data-ratings-url="/api/ratings.json"
        data-resorts-url="/api/resorts.json"
        data-default-basemap-key="swisstopo_winter"
        data-season-end="2026-05-31"`)}
      <section id="map-attribution-section"><p id="map-attribution-text"></p></section>`;
    bundleMap = bundleStub();
    window.pwaDb = { get: async () => undefined, put: async () => undefined, delete: async () => undefined };
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })));
    vi.stubGlobal('Worker', StubWorker);
    vi.resetModules();
    await import('../../static/js/basemap_download_core.js');
    await import('../../static/js/search_core.js');
    await import('../../static/js/choropleth_core.js');
    loadMapBundle();
    const text = () => document.getElementById('map-attribution-text').innerHTML;

    // The boot style: swisstopo alone, and its ids cached.
    for (const handler of bundleMap.handlers['style.load'] || []) handler();
    expect(text()).toBe(SWISSTOPO_CREDIT);

    // The splice adds the OSM source; its first sourcedata names it.
    await import('../../static/js/map_beyond_switzerland.js');
    expect(bundleMap.getSource(core.OSM_SOURCE_ID)).toBeTruthy();
    for (const handler of bundleMap.handlers.sourcedata || []) {
      handler({ sourceId: core.OSM_SOURCE_ID, sourceDataType: 'metadata' });
    }
    expect(text()).toContain('OpenStreetMap');
    expect(text()).toContain(SWISSTOPO_CREDIT);
    delete window.pwaDb;
  });
});
