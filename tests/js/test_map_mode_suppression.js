/*
 * tests/js/test_map_mode_suppression.js — Terrain mode's suppression of the
 * dated overlays, wired end to end through map.js (SNOW-1394).
 *
 * tests/js/test_map_mode_core.js covers the rules in isolation and
 * tests/js/test_map_mode.js the toggle. This file boots the real bundle on
 * `?mode=terrain`, with the flag's `data-map-modes-enabled`, and pins what
 * only map.js can get wrong:
 *
 *   - BOOT. Opened on ?mode=terrain the dated layers are installed hidden —
 *     the region fill, boundary and label, and the field reports — so the
 *     first paint never draws them;
 *   - SUPPRESSION, NEVER PREFERENCE. Releasing and holding again moves what
 *     is drawn and never a stored choice: the Bulletins step, Micro regions
 *     and "On the ground" read the same in localStorage throughout;
 *   - THE STATIC OVERLAYS ARE UNTOUCHED, and a panel switch that paints a
 *     held overlay (the reports panel's "Show on the map") is held again.
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

/** The DOM map.js's boot reads, with the flag on and the reports eligible. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-community-reports-url="/api/community-reports.geojson"
         data-community-reports-eligible="true"
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
    </div>`;
}

/** A layer's visibility, as map.js last wrote it. */
const shown = (map, id) => map.getLayoutProperty(id, 'visibility');

/** Every stored choice the mode must never write. */
const storedChoices = () => ({
  bulletins: localStorage.getItem(BULLETINS_KEY),
  l4: localStorage.getItem(L4_KEY),
  reports: localStorage.getItem(REPORTS_KEY),
});

/** Let the overlay-load handler's fetch settle. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

let mapStub;
let before;

beforeAll(async () => {
  localStorage.clear();
  localStorage.setItem(BULLETINS_KEY, '0.75');
  localStorage.setItem(L4_KEY, 'true');
  localStorage.setItem(REPORTS_KEY, 'true');
  before = storedChoices();
  history.replaceState(null, '', '/map/?d=2026-02-10&mode=terrain');
  buildFixture();
  mapStub = stubMapLibre();
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

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  await settle();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  history.replaceState(null, '', '/');
  delete globalThis.maplibregl;
});

describe('a map opened on ?mode=terrain', () => {
  it('installs the region fill, boundary and label hidden', () => {
    // The fill is the region tap's hit-test target: visibility none, not
    // opacity 0, is what makes a region untappable in Terrain.
    expect(shown(mapStub, 'regions-fill')).toBe('none');
    expect(shown(mapStub, 'regions-line')).toBe('none');
    expect(shown(mapStub, 'regions-label')).toBe('none');
  });

  it('says the bulletins are not visible, and wrote no stored choice', () => {
    expect(window.pwaBulletinsLayer.isVisible()).toBe(false);
    expect(storedChoices()).toEqual(before);
  });

  it('holds the field reports it installed', () => {
    expect(mapStub.getLayer('community-reports-point')).not.toBeNull();
    expect(shown(mapStub, 'community-reports-point')).toBe('none');
  });
});

describe('suppression, never preference', () => {
  it('brings the regions back as the reader had them on release', async () => {
    window.pwaBulletinsLayer.setSuppressed(MAP_MODE, false);
    window.pwaCommunityReportsOverlay.setSuppressed(MAP_MODE, false);
    await settle();

    expect(shown(mapStub, 'regions-fill')).toBe('visible');
    expect(shown(mapStub, 'regions-line')).toBe('visible');
    expect(shown(mapStub, 'community-reports-point')).toBe('visible');
    expect(window.pwaBulletinsLayer.isVisible()).toBe(true);
    expect(storedChoices()).toEqual(before);
  });

  it('takes them away again, and still writes nothing', () => {
    window.pwaBulletinsLayer.setSuppressed(MAP_MODE, true);
    window.pwaCommunityReportsOverlay.setSuppressed(MAP_MODE, true);

    expect(shown(mapStub, 'regions-fill')).toBe('none');
    expect(shown(mapStub, 'regions-line')).toBe('none');
    expect(storedChoices()).toEqual(before);
  });

  it('holds a field-report layer a panel switch paints while in Terrain', async () => {
    // The reports panel's "Show on the map" records the reader's choice; the
    // mode then holds what it painted.
    window.pwaCommunityReportsOverlay.show();
    await settle();

    expect(localStorage.getItem(REPORTS_KEY)).toBe('true');
    for (const id of ['community-reports-point', 'community-reports-clusters']) {
      expect(shown(mapStub, id)).toBe('none');
    }
  });

  it('brings back a region tier the reader had OFF as off', async () => {
    localStorage.setItem(L4_KEY, 'false');
    document.dispatchEvent(new CustomEvent('snowdesk:overlays-changed', {
      detail: { key: 'l4', visible: false },
    }));
    window.pwaBulletinsLayer.setSuppressed(MAP_MODE, false);
    await settle();

    expect(shown(mapStub, 'regions-line')).toBe('none');
    // The fill stays up for the Bulletins step alone.
    expect(shown(mapStub, 'regions-fill')).toBe('visible');
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('0.75');
  });
});
