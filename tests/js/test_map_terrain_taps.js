/*
 * tests/js/test_map_terrain_taps.js — what choosing a region does in
 * Terrain (SNOW-1422, static/js/map.js).
 *
 * Terrain has no regions to choose. What is pinned here:
 *
 *   - a tap on a region selects nothing, and a resort under it still opens;
 *   - a search pick, `snowdesk:region-open`, a hash and a field-observation
 *     pin in Terrain each go to Conditions on today first
 *     (`pwaMapMode.toConditionsToday`), then select — in that order;
 *   - entering Terrain drops the region selection, which closes the region
 *     sheet, and leaves an open place sheet open;
 *   - in Conditions each of these behaves as it did, and with no
 *     `window.pwaMapMode` (off the map_modes flag) nothing changes.
 *
 * map.js is booted on the tests/js/test_map_rest_zone_tap.js harness, with
 * one region in the regions feed so a selection has something to select.
 * The mode is a stub of map_mode.js's `window.pwaMapMode`, whose own
 * behaviour is pinned in tests/js/test_map_mode.js.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';
import { installRouteRailStub } from './_route_rail_stub.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };
const REGION_ID = 'CH-4224';

/** One region, as /api/regions.geojson emits it. */
const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { id: REGION_ID, name: 'Verbier' },
      geometry: {
        type: 'Polygon',
        coordinates: [[[7.0, 46.0], [7.01, 46.0], [7.01, 46.01], [7.0, 46.01], [7.0, 46.0]]],
      },
    },
  ],
};

/** A field-observation pin inside the region (SNOW-1291). */
const REPORT = {
  layer: { id: 'community-reports-point' },
  geometry: { type: 'Point', coordinates: [7.005, 46.005] },
  properties: {
    region_id: REGION_ID,
    submission: 'sub-1',
    types: '["whumpf"]',
    type_labels: '["Whumpf"]',
  },
};

const RESORT = {
  layer: { id: 'resorts-pin' },
  geometry: { type: 'Point', coordinates: [7.005, 46.005] },
  properties: { id: 'verbier', name: 'Verbier' },
};

/** What the next tap lands on, every layer at once. */
let hitFeatures = [];
/** The layers each hit-test asked about, this test. */
let asked = [];

/** A MapLibre stub whose hit-test answers only for the layers asked about. */
function stubMapLibre() {
  const handlers = {};
  const present = new Set(['resorts-pin', 'regions-fill', 'community-reports-point']);
  const canvas = { style: {}, clientWidth: 100, clientHeight: 100 };
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
    getLayer: (id) => (present.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: () => {},
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
    getCanvas: () => canvas,
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
    getCenter: () => ({ lng: 7.005, lat: 46.005 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 7.005, lat: 46.005 }),
    queryRenderedFeatures: (_point, options) => {
      const layers = (options && options.layers) || [];
      asked.push(...layers);
      return hitFeatures.filter((f) => layers.includes(f.layer.id));
    },
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

let mapStub;
/** The region feature map.js built from the feed. */
let regionHit;
/** Every snowdesk:region-selected map.js announced, this test. */
let regionEvents = [];
/** The mode the stub reports. */
let terrain = false;
/**
 * One log, in order: 'to-conditions' for each toConditionsToday(),
 * 'focus:<submission>' for each observation focus, and each region id
 * selected, so a test can pin that the mode moves BEFORE the selection.
 */
let log = [];
document.addEventListener('snowdesk:region-selected', (event) => {
  regionEvents.push(event.detail);
  if (event.detail.region_id) log.push(event.detail.region_id);
});
document.addEventListener('snowdesk:observation-focus', (event) => {
  log.push(`focus:${event.detail.submission}`);
});

/** Tap the map once, on whatever `hitFeatures` holds. */
function tapTheMap() {
  asked = [];
  for (const handler of mapStub.handlers.click || []) {
    handler({
      point: { x: 10, y: 10 },
      lngLat: { lng: 7.005, lat: 46.005 },
      originalEvent: { target: document.body },
    });
  }
}

/** The region ids map.js selected, in order. */
const selected = () => regionEvents.map((d) => d.region_id);

/**
 * Drop whatever region is selected, with no mode involved. A deselect
 * pops the history entry the selection pushed, and the selection clears
 * on the popstate that follows, so this waits for it.
 */
async function deselect() {
  terrain = false;
  hitFeatures = [];
  tapTheMap();
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** Announce a mode change, as map_mode.js does. */
function enterMode(mode) {
  terrain = mode === 'terrain';
  document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode } }));
}

/** Pick the region from search, as a typed name and Enter do. */
function searchPick() {
  const input = document.getElementById('search-input');
  input.value = 'Verbier';
  input.dispatchEvent(new Event('input'));
  input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
}

beforeAll(async () => {
  localStorage.clear();
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-favourites-url="/favourites/favourites.geojson"
         data-favourites-eligible="true"
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-ski-routes-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal('fetch', vi.fn((url) => {
    const body = String(url).includes('regions.geojson') ? REGIONS_GEOJSON : EMPTY_FC;
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }));
  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/map_sheet.js');
  await import('../../static/js/community_reports_core.js');
  installRouteRailStub();
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  const feature = REGIONS_GEOJSON.features[0];
  regionHit = {
    layer: { id: 'regions-fill' },
    id: feature.id,
    geometry: feature.geometry,
    properties: feature.properties,
  };
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
  delete window.pwaPlaceSheet;
  delete window.pwaMapMode;
});

beforeEach(async () => {
  await deselect();
  regionEvents = [];
  log = [];
  window.pwaPlaceSheet = { open: vi.fn(() => true), close: vi.fn(), isOpen: () => true };
  window.pwaMapMode = {
    isTerrain: () => terrain,
    toConditionsToday: vi.fn(() => {
      log.push('to-conditions');
      terrain = false;
    }),
  };
});

describe('the boot', () => {
  it('reads the region from the feed', () => {
    expect(regionHit.id).not.toBeUndefined();
  });
});

describe('a tap in Terrain', () => {
  it('selects no region, and asks the fill nothing', () => {
    terrain = true;
    hitFeatures = [regionHit];
    tapTheMap();
    expect(asked).not.toContain('regions-fill');
    // The empty-map close: nothing is selected.
    expect(selected()).toEqual([null]);
    expect(window.pwaMapMode.toConditionsToday).not.toHaveBeenCalled();
  });

  it('still opens a resort over a region', () => {
    terrain = true;
    hitFeatures = [RESORT, regionHit];
    tapTheMap();
    expect(window.pwaPlaceSheet.open).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'resort', id: 'verbier' }),
    );
    expect(selected()).toEqual([]);
  });

  it('selects the region in Conditions, as ever', () => {
    hitFeatures = [regionHit];
    tapTheMap();
    expect(asked).toContain('regions-fill');
    expect(selected()).toEqual([REGION_ID]);
  });

  it('gives no pointer and no double-click zoom over a region', () => {
    const canvas = mapStub.getCanvas();
    canvas.style.cursor = '';
    terrain = true;
    for (const fn of mapStub.handlers['mouseenter:regions-fill']) fn();
    expect(canvas.style.cursor).toBe('');
    const dbl = { preventDefault: vi.fn(), features: [regionHit] };
    for (const fn of mapStub.handlers['dblclick:regions-fill']) fn(dbl);
    expect(dbl.preventDefault).not.toHaveBeenCalled();

    terrain = false;
    for (const fn of mapStub.handlers['mouseenter:regions-fill']) fn();
    expect(canvas.style.cursor).toBe('pointer');
    for (const fn of mapStub.handlers['dblclick:regions-fill']) fn(dbl);
    expect(dbl.preventDefault).toHaveBeenCalled();
  });
});

describe('a region chosen another way in Terrain', () => {
  it('goes to Conditions on today, then selects, from search', () => {
    terrain = true;
    searchPick();
    expect(log).toEqual(['to-conditions', REGION_ID]);
  });

  it('goes to Conditions on today, then selects, from a region row', () => {
    terrain = true;
    document.dispatchEvent(new CustomEvent('snowdesk:region-open', {
      detail: { region_id: REGION_ID },
    }));
    expect(log).toEqual(['to-conditions', REGION_ID]);
  });

  it('goes to Conditions on today, then selects, from a hash', () => {
    terrain = true;
    // Pushed, as editing the fragment in the address bar does.
    history.pushState(null, '', `${location.pathname}#${REGION_ID}`);
    window.dispatchEvent(new HashChangeEvent('hashchange'));
    expect(log).toEqual(['to-conditions', REGION_ID]);
  });

  it('goes to Conditions on today, then focuses and selects, from a field observation', () => {
    terrain = true;
    hitFeatures = [REPORT];
    tapTheMap();
    expect(log).toEqual(['to-conditions', 'focus:sub-1', REGION_ID]);
  });

  it('opens a field observation in Conditions without moving the mode', () => {
    hitFeatures = [REPORT];
    tapTheMap();
    expect(log).toEqual(['focus:sub-1', REGION_ID]);
  });

  it('leaves the mode alone in Conditions', () => {
    document.dispatchEvent(new CustomEvent('snowdesk:region-open', {
      detail: { region_id: REGION_ID },
    }));
    expect(log).toEqual([REGION_ID]);
  });

  it('selects as ever with no map_mode.js (off the flag)', () => {
    delete window.pwaMapMode;
    searchPick();
    expect(selected()).toEqual([REGION_ID]);
  });
});

describe('entering Terrain', () => {
  it('drops the region, which closes its sheet, and leaves the place sheet open', () => {
    hitFeatures = [regionHit];
    tapTheMap();
    window.pwaPlaceSheet.close.mockClear();
    regionEvents = [];
    // What map_mode.js writes before it announces the change.
    history.replaceState(history.state, '', `${location.pathname}?mode=terrain${location.hash}`);

    enterMode('terrain');

    expect(regionEvents).toEqual([{ region_id: null, region_name: null }]);
    expect(window.pwaPlaceSheet.close).not.toHaveBeenCalled();
    // The hash goes, replaced rather than popped, so ?mode=terrain stays.
    expect(location.hash).toBe('');
    expect(location.search).toBe('?mode=terrain');
    history.replaceState(null, '', location.pathname);
  });

  it('announces nothing with no region selected', () => {
    enterMode('terrain');
    expect(regionEvents).toEqual([]);
  });

  it('keeps the region on the way back to Conditions', () => {
    hitFeatures = [regionHit];
    tapTheMap();
    regionEvents = [];
    enterMode('dated');
    expect(regionEvents).toEqual([]);
  });
});
