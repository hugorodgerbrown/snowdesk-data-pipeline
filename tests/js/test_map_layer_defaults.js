/*
 * tests/js/test_map_layer_defaults.js — the map's opening view, seeded from
 * the page rather than from literals (SNOW-872).
 *
 * Which providers' bulletins are painted, which EAWS boundary tier is drawn
 * and how strongly the choropleth is painted used to be hardcoded in three
 * JavaScript places that had to agree by hand — `map.js`'s boot IIFE, the
 * re-seed in its `styledata` handler after a basemap swap, and
 * `map_season_ribbon.js`. They are configured server-side now, rendered onto
 * `#map`, and read back through one owner: `mapDefaults()` in
 * `map_state.js`. `tests/public/test_map_defaults.py` covers the server half.
 *
 * Four things are pinned here, and the second is the one that matters most.
 *
 * 1. The dataset seeds the state, for all three defaults at once.
 * 2. **A STORED PREFERENCE STILL WINS.** A default is what a device with
 *    nothing stored gets; it is never an override. An operator changing the
 *    opening view must not silently switch a returning visitor's layers back
 *    on — that is somebody else's map being rearranged.
 * 3. An ABSENT attribute falls back to the literal that shipped before this.
 *    Around forty fixtures across this directory hand-write a `#map` root and
 *    carry none of these, and `trip_map.js` drives two map roots of its own,
 *    so a required attribute would be a breaking change dressed as a default.
 * 4. The `styledata` re-seed honours the same values. That block is a
 *    verbatim second copy of the boot seed, and a literal left in it would
 *    hold the configured view until the visitor changed basemap and then
 *    quietly revert — the failure no fixed-boot test would ever see.
 *
 * Every suite below boots its own bundle with its own dataset, following
 * `bootWithFailingCountry` in test_map_country_group_toggle.js: the boot seed
 * reads `localStorage` and the module state it builds is not resettable from
 * outside, so a fresh evaluation is the only honest way to ask what a first
 * visit looks like.
 */

import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

const COUNTRY_KEY = (code) => `snowdesk.map.overlay.country.${code}`;
const L1_KEY = 'snowdesk.map.overlay.l1';
const L4_KEY = 'snowdesk.map.overlay.l4';
const BULLETINS_KEY = 'snowdesk.map.overlay.bulletins';

/** Minimal MapLibre stub tracking per-layer layout and paint. */
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
    moveLayer: () => {},
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
    // A real setStyle DROPS every source and layer the app added — which is
    // the entire reason the `styledata` handler re-seeds and re-installs. A
    // no-op stub here would leave `regions-fill` in place, `installRegions-
    // Layers` would take its "fully installed already" early return, and the
    // re-seed's effect would never reach a layer to be asserted on. That is
    // exactly how a literal in that block survives a green suite.
    setStyle: () => {
      layers.clear();
      layouts.clear();
    },
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
 * The DOM map.js's boot reads: the three provider rows, the three boundary
 * tiers, and the five fill-strength segments — each rendered in the state
 * _map_embed.html would render it in for the SHIPPED defaults, so a fixture
 * that never got re-seeded reads as the old opening view rather than as the
 * new one by accident.
 *
 * @param {{overlays?: string, boundary?: string, opacityStep?: number}} defaults
 *   Each is omitted from the markup entirely when undefined, which is what a
 *   page (or a fixture) that predates SNOW-872 looks like.
 */
function buildFixture(defaults = {}) {
  const attrs = [];
  if (defaults.overlays !== undefined) {
    attrs.push(`data-default-overlays="${defaults.overlays}"`);
  }
  if (defaults.boundary !== undefined) {
    attrs.push(`data-default-boundary="${defaults.boundary}"`);
  }
  if (defaults.opacityStep !== undefined) {
    attrs.push(`data-default-opacity-step="${defaults.opacityStep}"`);
  }
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         ${attrs.join('\n         ')}
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
            <button class="basemap-menu-item"
                    data-basemap-key="openfreemap_liberty"
                    data-basemap-url="https://tiles.example.invalid/liberty.json"
                    aria-checked="true">OpenFreeMap</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="country.ch" data-country-codes="ch"
                    aria-checked="true">SLF (CH)</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="country.fr" data-country-codes="fr"
                    aria-checked="false">MétéoFrance (FR)</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="country.albina" data-country-codes="at it"
                    aria-checked="false">ALBINA (AT, IT)</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="l1" aria-checked="false">Major</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="l2" aria-checked="false">Minor</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="l4" aria-checked="true">Micro</button>
          </li>
        </ul>
      </div>
      <div id="map-fill-flyout" class="map-fill-flyout" role="group" hidden>
        <button role="radio" aria-checked="false" class="map-fill-step" data-bulletins-step="0"></button>
        <button role="radio" aria-checked="false" class="map-fill-step" data-bulletins-step="0.25"></button>
        <button role="radio" aria-checked="true" class="map-fill-step" data-bulletins-step="0.5"></button>
        <button role="radio" aria-checked="false" class="map-fill-step" data-bulletins-step="0.75"></button>
        <button role="radio" aria-checked="false" class="map-fill-step" data-bulletins-step="1"></button>
      </div>
    </div>`;
}

let mapStub;

/**
 * Evaluate a fresh bundle against a given page and a given device.
 *
 * @param {{overlays?: string, boundary?: string, opacityStep?: number,
 *          storage?: Object<string, string>}} options
 *   `storage` is what this device has already chosen — written before the
 *   boot, because the seed reads it.
 * @returns {Promise<void>}
 */
async function boot({ storage = {}, ...defaults } = {}) {
  localStorage.clear();
  for (const [key, value] of Object.entries(storage)) localStorage.setItem(key, value);
  buildFixture(defaults);
  mapStub = stubMapLibre();
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) })),
  );
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom, and installRegionsLayers hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();
}

/**
 * Change basemap, as far as map.js can tell.
 *
 * Two steps, because a swap is two things: MapLibre replaces the style —
 * taking every source and layer the app added with it — and then fires
 * `styledata`, which is where map.js re-seeds `overlayState` and re-installs.
 * Firing the event over an intact style would assert nothing.
 *
 * @returns {Promise<void>}
 */
async function swapBasemap() {
  mapStub.setStyle();
  for (const handler of mapStub.handlers.styledata || []) await handler();
}

/** Whether a layers-menu row opens checked, after the boot has mirrored state. */
function rowChecked(key) {
  return document.querySelector(`#basemap-menu [data-overlay-key="${key}"]`)
    .getAttribute('aria-checked') === 'true';
}

/** The fill-strength segment the control reads, as a user would see it. */
function checkedStep() {
  const seg = document.querySelector('[data-bulletins-step][aria-checked="true"]');
  return seg ? Number(seg.dataset.bulletinsStep) : null;
}

/** `regions-fill`'s resting opacity — the last arm of its `case` expression. */
function fillOpacity() {
  const opacity = mapStub.getPaintProperty('regions-fill', 'fill-opacity');
  return Array.isArray(opacity) ? opacity[opacity.length - 1] : opacity;
}

/** Whether the micro-region boundary is drawn — `l4`'s own layer. */
function microBoundaryVisible() {
  return mapStub.getLayoutProperty('regions-line', 'visibility') === 'visible';
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
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

describe('a first visit follows the configured defaults', () => {
  it('seeds providers, boundary tier and fill strength from the dataset', async () => {
    await boot({
      overlays: 'country.fr country.albina',
      boundary: 'l1',
      opacityStep: 0.25,
    });

    expect(rowChecked('country.ch')).toBe(false);
    expect(rowChecked('country.fr')).toBe(true);
    // One row, two country codes: overlayKeyForCountry resolves both AT and
    // IT back to it, so a row checked here means the map has both.
    expect(rowChecked('country.albina')).toBe(true);
    expect(rowChecked('l1')).toBe(true);
    expect(rowChecked('l4')).toBe(false);
    expect(microBoundaryVisible()).toBe(false);
    expect(checkedStep()).toBe(0.25);
    expect(fillOpacity()).toBe(0.25);
  });

  it('accepts an empty configuration as one — nothing on', async () => {
    // An operator asking for no provider and no boundary is a configuration,
    // not an omission, and must not be read as "unset" and quietly refilled.
    await boot({ overlays: '', boundary: '', opacityStep: 0.5 });

    expect(rowChecked('country.ch')).toBe(false);
    expect(rowChecked('country.fr')).toBe(false);
    expect(rowChecked('country.albina')).toBe(false);
    expect(rowChecked('l1')).toBe(false);
    expect(rowChecked('l2')).toBe(false);
    expect(rowChecked('l4')).toBe(false);
  });
});

describe('a stored preference beats a changed default', () => {
  it('leaves every layer where the visitor left it', async () => {
    await boot({
      overlays: 'country.fr',
      boundary: 'l1',
      opacityStep: 0.25,
      storage: {
        [COUNTRY_KEY('ch')]: 'true',
        [COUNTRY_KEY('fr')]: 'false',
        [L1_KEY]: 'false',
        [L4_KEY]: 'true',
        [BULLETINS_KEY]: '1',
      },
    });

    expect(rowChecked('country.ch')).toBe(true);
    expect(rowChecked('country.fr')).toBe(false);
    expect(rowChecked('l1')).toBe(false);
    expect(rowChecked('l4')).toBe(true);
    expect(checkedStep()).toBe(1);
  });

  it('applies the default only to the layers nothing is stored for', async () => {
    // The mixed case, which is the common one: a visitor who has touched one
    // switch has not thereby opted out of the rest of the opening view.
    await boot({
      overlays: 'country.ch country.albina',
      boundary: 'l4',
      storage: { [COUNTRY_KEY('albina')]: 'false', [COUNTRY_KEY('at')]: 'false' },
    });

    expect(rowChecked('country.ch')).toBe(true);
    // AT is stored off; IT has nothing stored and follows the default, and
    // the row reads unchecked because it only claims coverage it has in full.
    expect(rowChecked('country.albina')).toBe(false);
    expect(rowChecked('l4')).toBe(true);
  });
});

describe('a page carrying none of the attributes', () => {
  it('falls back to the view that shipped before SNOW-872', async () => {
    // The ~40 fixtures in this directory, and trip_map.js's own map roots.
    await boot();

    expect(rowChecked('country.ch')).toBe(true);
    expect(rowChecked('country.fr')).toBe(false);
    expect(rowChecked('country.albina')).toBe(false);
    expect(rowChecked('l1')).toBe(false);
    expect(rowChecked('l2')).toBe(false);
    expect(rowChecked('l4')).toBe(true);
    expect(microBoundaryVisible()).toBe(true);
    expect(checkedStep()).toBe(0.5);
  });
});

describe('the styledata re-seed after a basemap swap', () => {
  it('re-seeds from the same defaults the boot used', async () => {
    // The whole point of the second block: `overlayState` is rebuilt from
    // scratch when the style is replaced, and a literal left there would
    // hold the configured view until the visitor changed basemap and then
    // revert to l4-on at half strength with nothing to explain it.
    await boot({ overlays: 'country.ch', boundary: 'l1', opacityStep: 0.25 });

    expect(microBoundaryVisible()).toBe(false);
    expect(fillOpacity()).toBe(0.25);

    await swapBasemap();

    expect(microBoundaryVisible()).toBe(false);
    expect(fillOpacity()).toBe(0.25);
  });

  it('still lets a stored preference win afterwards', async () => {
    await boot({
      boundary: '',
      opacityStep: 0.25,
      storage: { [L4_KEY]: 'true', [BULLETINS_KEY]: '0.75' },
    });

    await swapBasemap();

    expect(microBoundaryVisible()).toBe(true);
    expect(fillOpacity()).toBe(0.75);
  });
});

describe('the legacy l4 hand-over under a configured step', () => {
  // `seedFromLegacy` distinguishes "absent" from "explicitly false" so a
  // device that switched the pre-split `l4` row off comes back with the
  // choropleth off rather than acquiring one it never asked for. That path
  // takes the default as an argument, so it has to be the CONFIGURED one.

  it('honours an explicit legacy off', async () => {
    await boot({ opacityStep: 0.25, storage: { [L4_KEY]: 'false' } });

    expect(checkedStep()).toBe(0);
  });

  it('falls back to the configured step, not to 0.5', async () => {
    await boot({ opacityStep: 0.25, storage: { [L4_KEY]: 'true' } });

    expect(checkedStep()).toBe(0.25);
  });
});
