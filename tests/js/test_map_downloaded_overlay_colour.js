/*
 * tests/js/test_map_downloaded_overlay_colour.js — Vitest DOM test for
 * SNOW-645 (review item 3): the "downloaded areas" map overlay
 * (`cached-tiles-fill` / `cached-tiles-line`) paints in the ACTIVE
 * basemap's identity colour, and keeps tracking it across a basemap
 * switch rather than freezing at whatever was active on first paint.
 *
 * Hugo's call, overruling the plan's own non-goal for this overlay: it is
 * already computed against the active basemap's tile template (it only
 * ever shows tiles cached for the basemap showing now — see
 * refreshDownloadedOverlay's own "PER-BASEMAP" comment in map.js), so a
 * plain green here while the roundel and progress grid turn (say) blue
 * would read as a colour seam.
 *
 * The bug this guards against: `DOWNLOADED_OUTLINE_COLOUR` used to be a
 * module-level `const` resolved ONCE at parse time. `downloadedOutlineColour`
 * is now a function, called fresh both where the two layers are first
 * installed (`installRegionsLayers`) AND on every `refreshDownloadedOverlay`
 * call via `map.setPaintProperty` — the second half is what this test
 * exercises, since a real basemap switch does not always force a full
 * layer re-add (map.js's own `styledata` handler skips reinstalling when
 * the regions layer survived the style swap).
 *
 * Booting map.js in jsdom follows test_map_download_bytes.js's pattern —
 * see its header for the general rationale. This file's stub additionally
 * TRACKS added layers and setPaintProperty calls (most other harnesses'
 * stubs are pure no-ops for these, since nothing before this ticket needed
 * to assert on paint state), which is the whole point here.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const TEMPLATE_A = 'https://tiles-a.example.invalid/{z}/{x}/{y}.pbf';
const TEMPLATE_B = 'https://tiles-b.example.invalid/{z}/{x}/{y}.pbf';

const LIBERTY_COLOUR = 'rgb(1, 2, 3)';
const SWISSTOPO_COLOUR = 'rgb(9, 8, 7)';

/** Empty regions collection — this suite only cares about the overlay's colour. */
const REGIONS_GEOJSON = { type: 'FeatureCollection', features: [] };

/**
 * Minimal MapLibre stub, mirroring test_map_download_bytes.js's, but with
 * `addLayer` / `getLayer` / `setPaintProperty` backed by a real `layers`
 * Map (id -> paint object) instead of no-ops, so a test can read back
 * what the overlay is actually painted with — and `setActiveTemplate` to
 * switch which template `activeBasemapTileTemplate` resolves, exactly as
 * test_map_download_bytes.js uses it to exercise a basemap switch.
 */
function stubMapLibre() {
  const handlers = {};
  let activeTemplate = TEMPLATE_A;
  const layers = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: (ev, cb) => {
      (handlers[ev] ||= []).push(cb);
    },
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => null,
    getPaintProperty: (id, prop) => {
      const paint = layers.get(id);
      return paint ? paint[prop] : undefined;
    },
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => (id === 'basemap' ? { tiles: [activeTemplate] } : null),
    addSource: () => {},
    addLayer: (def) => {
      layers.set(def.id, { ...(def.paint || {}) });
    },
    removeLayer: (id) => {
      layers.delete(id);
    },
    removeSource: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: (id, prop, value) => {
      const paint = layers.get(id);
      if (paint) paint[prop] = value;
    },
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
    layers,
    setActiveTemplate: (template) => {
      activeTemplate = template;
    },
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

/** Cache Storage stub — empty throughout; this suite never downloads anything. */
function installCachesStub() {
  const stub = {
    keys: vi.fn(async () => []),
    open: vi.fn(async () => ({
      keys: async () => [],
      put: async () => {},
      match: async () => undefined,
    })),
    delete: vi.fn(async () => {}),
  };
  Object.defineProperty(window, 'caches', {
    value: stub,
    configurable: true,
    writable: true,
  });
  return stub;
}

/** The DOM map.js's boot reads, plus a two-option basemap picker to switch between. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
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
          data-basemap-key="openfreemap_liberty"
          data-basemap-url="https://tiles.example.invalid/liberty.json"
          aria-checked="false"
        >Standard</button>
      </li>
      <li role="none">
        <button
          type="button"
          class="basemap-menu-item"
          data-basemap-key="swisstopo_winter"
          data-basemap-url="https://tiles.example.invalid/swisstopo.json"
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
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

/**
 * Flip the picker's checked radio to `key` and dispatch the same event
 * map.js's own `styledata` handler fires once a basemap switch has
 * finished re-installing everything (map.js:4012's
 * `snowdesk:basemap-changed`) — the signal `refreshDownloadedOverlay`
 * listens for. Mirrors the picker's own aria-checked bookkeeping
 * (map_basemap_picker.js) without driving a real MapLibre setStyle
 * round trip, which this stub's `setStyle` is a no-op for anyway.
 */
function switchBasemap(key, template) {
  for (const btn of document.querySelectorAll('#basemap-menu [data-basemap-key]')) {
    btn.setAttribute('aria-checked', btn.dataset.basemapKey === key ? 'true' : 'false');
  }
  mapStub.setActiveTemplate(template);
  document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));
}

let mapStub;

beforeAll(async () => {
  buildFixture();
  // Seeded before loadMapBundle() runs — overlayState.downloaded is read
  // from this key synchronously at the IIFE's top level (map.js:261), and
  // refreshDownloadedOverlay is a no-op whenever it is false.
  localStorage.setItem('snowdesk.map.overlay.downloaded', 'true');
  document.documentElement.style.setProperty(
    '--color-basemap-openfreemap-liberty',
    LIBERTY_COLOUR,
  );
  document.documentElement.style.setProperty(
    '--color-basemap-swisstopo-winter',
    SWISSTOPO_COLOUR,
  );
  mapStub = stubMapLibre();
  installCachesStub();
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
      return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
    }),
  );

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom; installRegionsLayers (and so the
  // two cached-tiles-* layers) hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.removeItem('snowdesk.map.overlay.downloaded');
  document.documentElement.removeAttribute('style');
  delete globalThis.maplibregl;
});

describe('downloaded-areas overlay colour (SNOW-645 review)', () => {
  it('paints the active basemap identity colour on first install, not a frozen default', async () => {
    await waitFor(() => mapStub.layers.has('cached-tiles-fill'));

    expect(mapStub.layers.get('cached-tiles-fill')['fill-color']).toBe(LIBERTY_COLOUR);
    expect(mapStub.layers.get('cached-tiles-line')['line-color']).toBe(LIBERTY_COLOUR);
  });

  it('tracks a basemap switch — the colour is not frozen at parse time', async () => {
    switchBasemap('swisstopo_winter', TEMPLATE_B);

    await waitFor(
      () => mapStub.layers.get('cached-tiles-fill')['fill-color'] === SWISSTOPO_COLOUR,
    );

    expect(mapStub.layers.get('cached-tiles-fill')['fill-color']).toBe(SWISSTOPO_COLOUR);
    expect(mapStub.layers.get('cached-tiles-line')['line-color']).toBe(SWISSTOPO_COLOUR);
  });

  it('switches back just as readily — this is live resolution, not a one-way flip', async () => {
    switchBasemap('openfreemap_liberty', TEMPLATE_A);

    await waitFor(
      () => mapStub.layers.get('cached-tiles-fill')['fill-color'] === LIBERTY_COLOUR,
    );

    expect(mapStub.layers.get('cached-tiles-fill')['fill-color']).toBe(LIBERTY_COLOUR);
    expect(mapStub.layers.get('cached-tiles-line')['line-color']).toBe(LIBERTY_COLOUR);
  });
});
