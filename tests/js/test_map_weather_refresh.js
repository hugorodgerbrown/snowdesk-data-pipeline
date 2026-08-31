/*
 * tests/js/test_map_weather_refresh.js — when the weather overlay recomputes
 * what it draws, and which recompute wins (SNOW-761).
 *
 * `refreshWeatherSourceData` is bound to `moveend` and to
 * `snowdesk:date-changed`, and it is not cheap: it collapses every station in
 * the payload to one per cluster, then projects every survivor for the day on
 * screen. Three properties, none of which needs a browser:
 *
 *   IT DOES NOTHING WHEN NOTHING IS DRAWN. The overlay is opt-in and both its
 *   layers carry `minzoom: 7`, so a pan with the overlay off — or below that
 *   zoom — was doing the whole walk for a picture that does not exist. Every
 *   pan on the map page paid for it, whether or not the visitor had ever
 *   switched the layer on.
 *
 *   THE STORAGE KEY IS THE TRUTH, not map.js's own `overlayState`. Toggling
 *   the row OFF takes the picker's direct visibility path, which writes the
 *   key and never touches this module's copy — so a check against
 *   `overlayState` alone would go on recomputing for a layer the user had
 *   switched off.
 *
 *   THE LAST CALL WINS. The recompute awaits an icon decode before it writes,
 *   so two calls started by a rapid pan can settle in either order and the
 *   slower FIRST one would paint the previous viewport's collapse over the
 *   newer one. Each call takes a token and only writes while it still holds
 *   the current one.
 *
 * Booting map.js in jsdom follows tests/js/test_map_route_share.js's pattern.
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
        name: 'Verbier',
        elevation_m: 1500,
        days: { '2026-08-31': { code: 0, temp_max_c: 4, temp_min_c: -2 } },
      },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [9.8, 46.5] },
      properties: {
        name: 'Davos',
        elevation_m: 1560,
        days: { '2026-08-31': { code: 3, temp_max_c: 1, temp_min_c: -6 } },
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

beforeAll(async () => {
  localStorage.clear();
  // Enabled, so the boot restore installs the layer and the refresh gate
  // below opens. The picker writes this key on every click, which is why the
  // gate reads it rather than map.js's own overlayState.
  localStorage.setItem('snowdesk.map.overlay.weather', 'true');
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
      json: () => Promise.resolve(
        String(url).includes('weather.geojson') ? WEATHER_FC : EMPTY_FC,
      ),
      text: () => Promise.resolve(''),
    })),
  );

  stubCanvas2D();

  // The icon decode, gated. map.js decodes each icon through an <img> that
  // jsdom will never load, so every decode is failed ON THIS SUITE'S CUE
  // instead — `ensureWeatherIconsRegistered` catches a failed decode and
  // skips that icon, which is the settled state the write waits on. Failing
  // rather than succeeding keeps the stub to one branch; what these tests
  // are about is the gap before the write, not what is in the sprite.
  globalThis.Image = class {
    set src(_value) {
      decodeGates.push(() => { if (this.onerror) this.onerror(); });
    }
  };
  // Each of those failures is logged by design (one bad icon must not block
  // the others), and this suite provokes several per test. Silenced so the
  // run's output says something about the run.
  vi.spyOn(console, 'warn').mockImplementation(() => {});

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/elevation_profile_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  // The boot restore is fire-and-forget — the `load` handler does not await
  // it — so the fetch, the install and that install's own icon decode land
  // over several turns after the handler returns. Settle repeatedly until
  // they have, or the first test would see the boot's write as its own.
  for (let round = 0; round < 20; round += 1) await settle();
});

beforeEach(() => {
  zoom = 9;
  localStorage.setItem('snowdesk.map.overlay.weather', 'true');
  setDataCalls.length = 0;
});

describe('recomputing on a finished pan', () => {
  it('writes the newly-collapsed payload while the overlay is on', async () => {
    moveend();
    await settle();

    expect(setDataCalls.length).toBe(1);
    expect(setDataCalls[0].type).toBe('FeatureCollection');
  });

  it('does nothing once the row has been switched off', async () => {
    // The defect: every pan on the map page paid for the collapse whether or
    // not the visitor had ever enabled the layer.
    localStorage.setItem('snowdesk.map.overlay.weather', 'false');

    moveend();
    await settle();

    expect(setDataCalls.length).toBe(0);
  });

  it('does nothing below the zoom both layers are drawn from', async () => {
    // WEATHER_MIN_ZOOM is the layers' own minzoom, so nothing is on screen
    // at this camera either way. moveend fires again on the way back up.
    zoom = 5;

    moveend();
    await settle();

    expect(setDataCalls.length).toBe(0);
  });

  it('recomputes again once the camera is back above it', async () => {
    zoom = 5;
    moveend();
    await settle();

    zoom = 9;
    moveend();
    await settle();

    expect(setDataCalls.length).toBe(1);
  });
});

describe('switching the row back on', () => {
  it('recomputes, so a day scrubbed to while it was off arrives', async () => {
    // The cost of the gate above: everything that happened while the layer
    // was hidden was skipped, so a re-enable would otherwise reveal whatever
    // the source last held. The bulletin boundary takes the same fix in the
    // same handler, for the same reason.
    localStorage.setItem('snowdesk.map.overlay.weather', 'false');
    moveend();
    await settle();
    expect(setDataCalls.length).toBe(0);

    localStorage.setItem('snowdesk.map.overlay.weather', 'true');
    document.dispatchEvent(
      new CustomEvent('snowdesk:overlay-load', { detail: { key: 'weather' } }),
    );
    for (let round = 0; round < 5; round += 1) await settle();

    expect(setDataCalls.length).toBe(1);
  });
});

describe('two recomputes racing', () => {
  it('discards the older one, however the decodes settle', async () => {
    // Rapid panning starts a second refresh before the first has written.
    // Without the ordering token the first to SETTLE wins, which is the
    // previous viewport's collapse painted over the current one.
    moveend();
    moveend();

    await settle();

    expect(setDataCalls.length).toBe(1);
  });

  it('still writes exactly once for a single pan', async () => {
    // The regression guard for the token: one that never matched would
    // silently discard every write and freeze the overlay.
    moveend();
    await settle();

    expect(setDataCalls.length).toBe(1);
  });
});
