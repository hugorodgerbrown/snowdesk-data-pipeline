/*
 * tests/js/test_map_mode_suppression.js — each map mode's suppression of
 * the other mode's own layers, wired end to end through map.js (SNOW-1394,
 * SNOW-1421).
 *
 * tests/js/test_map_mode_core.js covers the rules in isolation and
 * tests/js/test_map_mode.js the toggle. This file boots the real bundle,
 * with the flag's `data-map-modes-enabled`, and pins what only map.js,
 * ski_tours.js and rest_zones.js can get wrong:
 *
 *   - A TERRAIN BOOT. Opened on ?mode=terrain the bulletin colour is
 *     installed hidden, so the first paint never draws it, while the region
 *     boundaries, which are fixed, are drawn and stay tappable, with their
 *     bare names from z10; the field reports, shared since SNOW-1421, draw;
 *   - A CONDITIONS BOOT (SNOW-1421). On a bare URL, with the stored SAC
 *     tours, summits and rest zones on, ski_tours.js and rest_zones.js
 *     install their layers hidden before any hook is called;
 *   - SUPPRESSION, NEVER PREFERENCE. Releasing and holding again, in either
 *     mode, moves what is drawn and never a stored choice;
 *   - THE BACKSTOP. A control that paints a held overlay ("Find a route"
 *     switching the tours on from Conditions) is held again, and the rest
 *     zones still show while a route is drawn, in either mode.
 *
 * Booted as tests/js/test_map_bulletins_layer.js boots it — see its header.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_mode_core.js';
import { loadMapBundle } from './_load_map_bundle.js';

const BULLETINS_KEY = 'snowdesk.map.overlay.bulletins';
const L4_KEY = 'snowdesk.map.overlay.l4';
const REPORTS_KEY = 'snowdesk.map.overlay.community_reports';
// The tours keep the swisstopo raster's old key (map_state.js).
const TOURS_KEY = 'snowdesk.map.overlay.ski_routes';
const SUMMITS_KEY = 'snowdesk.map.overlay.ski_summits';
const REST_KEY = 'snowdesk.map.overlay.rest_zones';
const MAP_MODE = 'map-mode';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** A MapLibre stub recording each layer's layout and paint. */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const layouts = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layouts.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: (id, prop) => (layers.get(id) || {})[prop],
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => {
      layers.set(def.id, { ...(def.paint || {}) });
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => {
      layers.delete(id);
      layouts.delete(id);
    },
    removeSource: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
    setPaintProperty: (id, prop, value) => {
      const paint = layers.get(id) || {};
      paint[prop] = value;
      layers.set(id, paint);
    },
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
    hasImage: () => true,
    addImage: () => {},
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

/**
 * The DOM map.js's boot reads, with the flag on, the reports eligible and,
 * for a Conditions boot, the SAC layers' and rest zones' own surfaces.
 */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-community-reports-url="/api/community-reports.geojson"
         data-community-reports-eligible="true"
         data-ski-routes-eligible="true"
         data-map-modes-enabled="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
      <div id="basemap-pill" data-state="collapsed">
        <button id="basemap-toggle" aria-expanded="false"></button>
        <ul id="basemap-menu" hidden>
          <li role="none">
            <button
              type="button"
              class="basemap-menu-item"
              data-basemap-key="openfreemap_liberty"
              data-basemap-url="https://tiles.example.invalid/liberty.json"
              aria-checked="true"
            >OpenFreeMap</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="l4" aria-checked="true">Micro regions</button>
          </li>
        </ul>
      </div>
    </div>
    <div id="ski-tours-config" data-geojson-url="/api/ski-tours.geojson"></div>
    <div id="ski-tours-hint" hidden><span data-ski-tours-hint-text></span></div>
    <template id="ski-tour-claim-template"><div><p data-credit></p></div></template>
    <div id="rest-zones-config" data-geojson-url="/api/rest-zones.geojson"></div>
    <div id="rest-zone-sheet"></div>
    <template id="rest-zone-body-template"><div></div></template>`;
}

/** A layer's visibility, as map.js last wrote it. */
const shown = (map, id) => map.getLayoutProperty(id, 'visibility');

/** Every stored choice the mode must never write. */
const storedChoices = () => ({
  bulletins: localStorage.getItem(BULLETINS_KEY),
  l4: localStorage.getItem(L4_KEY),
  reports: localStorage.getItem(REPORTS_KEY),
  tours: localStorage.getItem(TOURS_KEY),
  summits: localStorage.getItem(SUMMITS_KEY),
  rest: localStorage.getItem(REST_KEY),
});

/** Let the overlay-load handler's fetch settle. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

let mapStub;
let before;

/**
 * Boot the bundle on `url` from `stored`, and fire the map's load.
 *
 * @param {string} url The page's path and query.
 * @param {Object<string, string>} stored localStorage to start from.
 * @returns {Promise<void>}
 */
async function boot(url, stored) {
  localStorage.clear();
  for (const [key, value] of Object.entries(stored)) localStorage.setItem(key, value);
  before = storedChoices();
  history.replaceState(null, '', url);
  buildFixture();
  mapStub = stubMapLibre();
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  await settle();
}

/**
 * Load the two modules that draw their own Terrain layers, after the
 * bundle as the page loads them (static/js/bundles/map_surfaces.txt).
 *
 * @returns {Promise<void>}
 */
async function loadTerrainModules() {
  window.MapSheet = {
    attach: () => ({ open: () => {}, close: () => {}, isOpen: () => false }),
    toast: () => {},
  };
  await import('../../static/js/ski_tours_core.js');
  await import('../../static/js/ski_tours.js');
  await import('../../static/js/rest_zones_core.js');
  await import('../../static/js/rest_zones.js');
  await settle();
}

beforeAll(() => {
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 1e10, usage: 0 }) },
    configurable: true,
  });
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) })),
  );
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  history.replaceState(null, '', '/');
  delete globalThis.maplibregl;
  delete window.MapSheet;
});

describe('a map opened on ?mode=terrain', () => {
  beforeAll(async () => {
    await boot('/map/?d=2026-02-10&mode=terrain', {
      [BULLETINS_KEY]: '0.75',
      [L4_KEY]: 'true',
      [REPORTS_KEY]: 'true',
    });
  });

  it('draws the region boundary and label, and keeps the region tappable', () => {
    // The fill is the region tap's hit-test target: installed, at opacity
    // 0, so a tap opens the lean region sheet without painting the day.
    expect(shown(mapStub, 'regions-fill')).toBe('visible');
    expect(mapStub.getPaintProperty('regions-fill', 'fill-opacity')).toBe(0);
    expect(shown(mapStub, 'regions-line')).toBe('visible');
    expect(shown(mapStub, 'regions-label')).toBe('visible');
  });

  it('names the regions from z10 with the bare name, not the level', () => {
    expect(shown(mapStub, 'regions-level-label')).toBe('visible');
    expect(mapStub.getLayoutProperty('regions-level-label', 'text-field')).toEqual(['get', 'name']);
    expect(shown(mapStub, 'regions-edge')).toBe('none');
  });

  it('says the bulletins are not visible, and wrote no stored choice', () => {
    expect(window.pwaBulletinsLayer.isVisible()).toBe(false);
    expect(storedChoices()).toEqual(before);
  });

  it('draws the field reports, which both modes share', () => {
    expect(mapStub.getLayer('community-reports-point')).not.toBeNull();
    expect(shown(mapStub, 'community-reports-point')).toBe('visible');
  });

  it('brings the colour back as the reader had it on release', async () => {
    window.pwaBulletinsLayer.setSuppressed(MAP_MODE, false);
    await settle();

    expect(shown(mapStub, 'regions-fill')).toBe('visible');
    expect(mapStub.getPaintProperty('regions-fill', 'fill-opacity')).not.toBe(0);
    expect(shown(mapStub, 'regions-line')).toBe('visible');
    expect(mapStub.getLayoutProperty('regions-level-label', 'text-field')).toEqual(['get', 'text']);
    expect(shown(mapStub, 'community-reports-point')).toBe('visible');
    expect(window.pwaBulletinsLayer.isVisible()).toBe(true);
    expect(storedChoices()).toEqual(before);
  });

  it('takes the colour away again, leaves the boundaries and the reports, and writes nothing', () => {
    window.pwaBulletinsLayer.setSuppressed(MAP_MODE, true);

    expect(mapStub.getPaintProperty('regions-fill', 'fill-opacity')).toBe(0);
    expect(shown(mapStub, 'regions-line')).toBe('visible');
    expect(shown(mapStub, 'community-reports-point')).toBe('visible');
    expect(storedChoices()).toEqual(before);
  });

  it('takes the names and the region tap with Micro regions off in Terrain', () => {
    // The picker hides the tier's own layers; map.js hears the flip and
    // re-derives the two it shares with the bulletins.
    document.dispatchEvent(new CustomEvent('snowdesk:overlays-changed', {
      detail: { key: 'l4', visible: false },
    }));

    expect(shown(mapStub, 'regions-level-label')).toBe('none');
    expect(shown(mapStub, 'regions-fill')).toBe('none');
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('0.75');
  });
});

describe('a map opened in Conditions (SNOW-1421)', () => {
  const SAC = ['ski-tours-line', 'ski-tours-line-down', 'ski-tours-summit', 'ski-tours-summit-label'];
  const REST = ['rest-zones-fill', 'rest-zones-line'];

  beforeAll(async () => {
    await boot('/map/?d=2026-02-10', {
      [TOURS_KEY]: 'true',
      [SUMMITS_KEY]: 'true',
      [REST_KEY]: 'true',
    });
    await loadTerrainModules();
  });

  it('installs the SAC layers and the rest zones hidden before any hook is called', () => {
    for (const id of [...SAC, ...REST]) {
      expect(mapStub.getLayer(id), id).not.toBeNull();
      expect(shown(mapStub, id), id).toBe('none');
    }
    expect(window.pwaMapModeState.isSuppressed('ski_tours')).toBe(true);
    expect(window.pwaMapModeState.isSuppressed('rest_zones')).toBe(true);
    expect(storedChoices()).toEqual(before);
  });

  it('draws them as the reader had them once released, and writes nothing', () => {
    window.pwaSkiTours.setSuppressed(MAP_MODE, false);
    window.pwaRestZones.setSuppressed(MAP_MODE, false);

    for (const id of [...SAC, ...REST]) expect(shown(mapStub, id), id).toBe('visible');
    expect(storedChoices()).toEqual(before);
  });

  it('holds them again, and writes nothing', () => {
    window.pwaSkiTours.setSuppressed(MAP_MODE, true);
    window.pwaRestZones.setSuppressed(MAP_MODE, true);

    for (const id of [...SAC, ...REST]) expect(shown(mapStub, id), id).toBe('none');
    expect(storedChoices()).toEqual(before);
  });

  it('ignores a reason that is not the mode\'s', () => {
    window.pwaSkiTours.setSuppressed('resort-edit', false);
    window.pwaRestZones.setSuppressed('resort-edit', false);

    expect(window.pwaMapModeState.isSuppressed('ski_tours')).toBe(true);
    for (const id of [...SAC, ...REST]) expect(shown(mapStub, id), id).toBe('none');
  });

  it('holds the tours "Find a route" switches on, and records the choice', () => {
    localStorage.setItem(TOURS_KEY, 'false');
    localStorage.setItem(SUMMITS_KEY, 'false');
    window.pwaSkiRoutes.show();

    expect(localStorage.getItem(TOURS_KEY)).toBe('true');
    for (const id of SAC) expect(shown(mapStub, id), id).toBe('none');
  });

  it('still shows the rest zones while a route is drawn, and holds them after', () => {
    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: true } }));
    // Another layer's change must not take them away mid-drawing.
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
    for (const id of REST) expect(shown(mapStub, id), id).toBe('visible');

    document.dispatchEvent(new CustomEvent('snowdesk:route-draw', { detail: { active: false } }));
    for (const id of REST) expect(shown(mapStub, id), id).toBe('none');
  });
});
