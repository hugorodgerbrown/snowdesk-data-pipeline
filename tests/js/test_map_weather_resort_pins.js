/*
 * tests/js/test_map_weather_resort_pins.js — which resort dots a weather
 * symbol steps aside for, either side of the resorts' z8 start
 * (docs/decisions/resorts-appear-from-z8.md).
 *
 * A station on a resort pin is marked `at_resort` so its symbol sits beside
 * the dot. Below z8 only a pinned resort's dot is drawn, so only that one
 * moves a symbol; from z8 every resort does. Pinning a resort while below
 * z8 re-projects at once rather than at the next pan.
 *
 * Harness lifted from tests/js/test_map_weather_refresh.js, with a resort
 * on each station and Verbier pinned.
 */

import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_weather_core.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** The weather feed: two stations, one day each. */
const WEATHER_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.0, 46.0] },
      properties: {
        name: 'Verbier station',
        elevation_m: 1500,
        days: { '2026-08-31': { code: 0, icon: 'clear-day.svg', temp_max_c: 4, temp_min_c: -2 } },
      },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [9.8, 46.5] },
      properties: {
        name: 'Davos station',
        elevation_m: 1560,
        days: { '2026-08-31': { code: 3, icon: 'cloudy.svg', temp_max_c: 1, temp_min_c: -6 } },
      },
    },
  ],
};

/** Every payload written to the weather source, in the order it landed. */
const setDataCalls = [];

/** The camera's current zoom, which the tests move. */
let zoom = 9;

/** Resolvers for the pending icon-decode promises, newest last. */
let decodeGates = [];

/**
 * Minimal MapLibre stub. See test_map_route_share.js for the model.
 *
 * `hasImage` answers false so every refresh really does await
 * `ensureWeatherIconsRegistered` — which is the async gap the ordering
 * token exists to cover. `addImage` is what each gated decode resolves into.
 *
 * @returns {object} The map stub.
 */
function stubMapLibre() {
  const handlers = {};
  const sources = {};
  const installedLayers = new Set();
  const map = {
    on: (event, layerOrHandler, maybeHandler) => {
      if (typeof layerOrHandler === 'function') {
        (handlers[event] ||= []).push(layerOrHandler);
      } else if (typeof maybeHandler === 'function') {
        (handlers[`${event}:${layerOrHandler}`] ||= []).push(maybeHandler);
      }
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (installedLayers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources[id] || null,
    addSource: (id, spec) => {
      sources[id] = Object.assign(
        { setData: (data) => { if (id === 'weather') setDataCalls.push(data); } },
        spec,
      );
    },
    addLayer: (spec) => { installedLayers.add(spec.id); },
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
    hasImage: () => false,
    addImage: () => {},
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => zoom,
    // SNOW-737's viewport persistence rides the same `moveend` these tests
    // fire, and reads all four camera properties.
    getBearing: () => 0,
    getPitch: () => 0,
    getCenter: () => ({ lng: 7, lat: 46 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 7, lat: 46 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      const popup = {
        setHTML: () => popup,
        setDOMContent: () => popup,
        setLngLat: () => popup,
        addTo: () => popup,
        getElement: () => document.createElement('div'),
        on: () => {},
        remove: () => {},
      };
      return popup;
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/**
 * A 2D canvas context good enough for map.js's colour parsing.
 *
 * jsdom ships none, and `map.js` resolves a design token to three channels
 * by filling a 1x1 canvas (cssColourChannels) on the way to building the
 * downloaded-areas hatch — which every boot that installs the region layers
 * reaches. Lifted from tests/js/test_map_route_endpoints.js.
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
        const hex = /^#([0-9a-f]{6})$/i.exec(String(value).trim());
        if (hex) {
          const n = parseInt(hex[1], 16);
          channels = [(n >> 16) & 255, (n >> 8) & 255, n & 255, 255];
          return;
        }
        const rgb = /rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/.exec(String(value));
        channels = rgb
          ? [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), 255]
          : [0, 0, 0, 255];
      },
      get fillStyle() {
        return `rgb(${channels[0]}, ${channels[1]}, ${channels[2]})`;
      },
      fillRect: () => {},
      drawImage: () => {},
      getImageData: () => ({ data: Uint8ClampedArray.from(channels) }),
    };
  };
  return () => { HTMLCanvasElement.prototype.getContext = original; };
}

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-weather-url="/api/weather.geojson"
         data-favourites-url="/favourites/favourites.geojson"
         data-favourites-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

let mapStub;

/** Fire the map's `moveend`, which is what a finished pan or zoom raises. */
function moveend() {
  for (const handler of mapStub.handlers.moveend || []) handler();
}

/**
 * Let every pending icon decode finish, then drain the microtask queue.
 *
 * Each decode is stubbed as a promise this test resolves by hand, so the
 * window between "refresh started" and "refresh wrote" is under the test's
 * control rather than the event loop's.
 *
 * @returns {Promise<void>}
 */
async function settle() {
  for (const release of decodeGates) release();
  decodeGates = [];
  for (let tick = 0; tick < 12; tick += 1) await Promise.resolve();
}


/** A resort standing on each station. */
const RESORTS_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.0, 46.0] },
      properties: { id: 'verbier', name: 'Verbier', tier: 'CORE' },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [9.8, 46.5] },
      properties: { id: 'davos', name: 'Davos', tier: 'CORE' },
    },
  ],
};

/** Verbier is pinned. */
const FAVOURITES_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [7.0, 46.0] },
      properties: { uuid: 'f-1', name: 'Verbier', resort_slug: 'verbier' },
    },
  ],
};

/** Station name → whether its symbol steps aside, from the latest write. */
function atResort() {
  const latest = setDataCalls[setDataCalls.length - 1];
  return Object.fromEntries(
    latest.features.map((f) => [f.properties.name, f.properties.at_resort]),
  );
}

function body(url) {
  const href = String(url);
  if (href.includes('weather.geojson')) return WEATHER_FC;
  if (href.includes('favourites')) return FAVOURITES_FC;
  if (href.includes('resorts.geojson')) return RESORTS_FC;
  return EMPTY_FC;
}

beforeAll(async () => {
  localStorage.clear();
  localStorage.setItem('snowdesk.map.overlay.weather', 'true');
  localStorage.setItem('snowdesk.map.overlay.resorts', 'true');
  buildFixture();
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(body(url)),
      text: () => Promise.resolve(''),
    })),
  );
  stubCanvas2D();
  globalThis.Image = class {
    set src(_value) {
      decodeGates.push(() => { if (this.onerror) this.onerror(); });
    }
  };
  vi.spyOn(console, 'warn').mockImplementation(() => {});

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/elevation_profile_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  document.dispatchEvent(
    new CustomEvent('snowdesk:overlay-load', { detail: { key: 'resorts' } }),
  );
  for (let round = 0; round < 20; round += 1) await settle();
});

beforeEach(() => {
  setDataCalls.length = 0;
});

describe('weather symbols step aside only for drawn resort dots', () => {
  it('steps aside for every resort from z8', async () => {
    zoom = 9;
    moveend();
    await settle();

    expect(atResort()).toEqual({ 'Verbier station': true, 'Davos station': true });
  });

  it('steps aside only for the pinned resort below z8', async () => {
    zoom = 7.5;
    moveend();
    await settle();

    expect(atResort()).toEqual({ 'Verbier station': true, 'Davos station': false });
  });

  it('re-projects at once when a resort is pinned below z8', async () => {
    zoom = 7.5;
    document.dispatchEvent(new CustomEvent('snowdesk:favourite-pending', {
      detail: { lat: 46.5, lon: 9.8, name: 'Davos', resortSlug: 'davos' },
    }));
    await settle();

    expect(atResort()).toEqual({ 'Verbier station': true, 'Davos station': true });
  });
});
