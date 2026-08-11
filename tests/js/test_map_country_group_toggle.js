/*
 * tests/js/test_map_country_group_toggle.js — the merged ALBINA row, wired end
 * to end through the live picker and map.js (SNOW-658).
 *
 * The layers menu lists bulletin PROVIDERS now, and ALBINA publishes the
 * EUREGIO bulletin for both Austria and Italy — so one row drives two country
 * codes. Nothing below the menu learned about providers: `countryState`, the
 * per-code localStorage keys and `applyCountryFilters`'s filter are all still
 * per-code. The merge is therefore a ROUTING change, declared once in
 * `COUNTRY_GROUPS` (static/js/map_state.js) and carried out by
 * map_basemap_picker.js dispatching `snowdesk:country-toggle` once PER CODE.
 *
 * That is what this file pins down: a real click on the row (the integration
 * style of tests/js/test_map_bulletins_layer.js — the picker is a bundle
 * member, so its handler is live here) yields two dispatches and two persisted
 * keys, and the row's checked state answers to both codes rather than either.
 *
 * A row that half-applies is the failure worth catching: one country switched
 * on behind a checked row reads to the user as coverage that is not on the
 * map.
 *
 * Booting map.js in jsdom follows test_map_download_bytes.js's pattern — see
 * its header for the general rationale.
 */

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const AT_KEY = 'snowdesk.map.overlay.country.at';
const IT_KEY = 'snowdesk.map.overlay.country.it';
const CH_KEY = 'snowdesk.map.overlay.country.ch';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** Minimal MapLibre stub — this suite reads state and events, not layers. */
function stubMapLibre() {
  const handlers = {};
  const layouts = new Map();
  const filters = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layouts.has(id) ? { id } : null),
    getFilter: (id) => filters.get(id) ?? null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => {
      layouts.set(def.id, { ...(def.layout || {}) });
      if (def.filter !== undefined) filters.set(def.id, def.filter);
    },
    removeLayer: (id) => {
      layouts.delete(id);
      filters.delete(id);
    },
    removeSource: () => {},
    moveLayer: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
    setPaintProperty: () => {},
    setFilter: (id, filter) => {
      filters.set(id, filter);
    },
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
 * The DOM map.js's boot reads, plus the two provider rows this ticket's
 * routing change turns on: one single-country (SLF) and one merged (ALBINA),
 * each carrying the `data-country-codes` the template renders.
 */
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
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
      <div id="basemap-pill" data-state="collapsed">
        <button id="basemap-toggle" aria-expanded="false"></button>
        <ul id="basemap-menu" hidden>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="country.ch" data-country-codes="ch"
                    aria-checked="true">SLF (CH)</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="country.albina" data-country-codes="at it"
                    aria-checked="false">ALBINA (AT, IT)</button>
          </li>
        </ul>
      </div>
    </div>`;
}

/** The row for an overlay key. */
function row(key) {
  return document.querySelector(`#basemap-menu [data-overlay-key="${key}"]`);
}

/** Put a row into a known checked state without asserting on how it got there. */
function setRow(key, next) {
  const el = row(key);
  if ((el.getAttribute('aria-checked') === 'true') !== next) el.click();
  return el;
}

/** Record every snowdesk:country-toggle dispatch for the enclosing test. */
let toggles;
let removeListener;

let mapStub;

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
 * Re-boot the whole bundle with a fetch stub that fails ONE country's
 * micro-region feed.
 *
 * A fresh boot is the only way to reach the failure branch at all:
 * `loadedCountries` is module state, and the tests above have already loaded
 * both ALBINA codes successfully, so `ensureCountryLoaded` would short-circuit
 * before it ever fetched. `localStorage` is cleared for the same reason — the
 * boot seed reads it.
 *
 * @param {string} failCode - the country code whose regions fetch 404s.
 * @returns {Promise<void>}
 */
async function bootWithFailingCountry(failCode) {
  localStorage.clear();
  buildFixture();
  mapStub = stubMapLibre();
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      const href = String(url);
      // Only the L4 feed rejects on !ok without its own catch, so this is the
      // one that actually reaches ensureCountryLoaded's catch block.
      if (href.includes('regions.geojson') && href.includes(`country=${failCode}`)) {
        return Promise.resolve({ ok: false, status: 503, json: () => Promise.resolve({}) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) });
    }),
  );
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
}


beforeAll(async () => {
  localStorage.clear();
  buildFixture();
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

  mapStub = stubMapLibre();
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
});

beforeEach(() => {
  toggles = [];
  const handler = (event) => toggles.push(event.detail);
  document.addEventListener('snowdesk:country-toggle', handler);
  removeListener = () => document.removeEventListener('snowdesk:country-toggle', handler);
});

afterEach(() => {
  removeListener();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

describe('the merged ALBINA row', () => {
  it('dispatches one country-toggle per code, not one per row', () => {
    setRow('country.albina', false);
    toggles.length = 0;

    row('country.albina').click();

    expect(toggles).toEqual([
      { code: 'at', next: true },
      { code: 'it', next: true },
    ]);
  });

  it('persists a key for each country it switches', () => {
    setRow('country.albina', false);
    localStorage.removeItem(AT_KEY);
    localStorage.removeItem(IT_KEY);

    row('country.albina').click();

    expect(localStorage.getItem(AT_KEY)).toBe('true');
    expect(localStorage.getItem(IT_KEY)).toBe('true');
  });

  it('switches both back off together', () => {
    const albina = setRow('country.albina', true);
    toggles.length = 0;

    albina.click();

    expect(toggles).toEqual([
      { code: 'at', next: false },
      { code: 'it', next: false },
    ]);
    expect(localStorage.getItem(AT_KEY)).toBe('false');
    expect(localStorage.getItem(IT_KEY)).toBe('false');
  });

  it('leaves an unrelated provider row alone', () => {
    setRow('country.albina', false);
    const before = localStorage.getItem(CH_KEY);
    toggles.length = 0;

    row('country.albina').click();

    expect(localStorage.getItem(CH_KEY)).toBe(before);
    expect(toggles.map((detail) => detail.code)).not.toContain('ch');
  });
});

describe('a single-country provider row still behaves as it did', () => {
  it('dispatches exactly one country-toggle', () => {
    setRow('country.ch', true);
    toggles.length = 0;

    row('country.ch').click();

    expect(toggles).toEqual([{ code: 'ch', next: false }]);
    expect(localStorage.getItem(CH_KEY)).toBe('false');
  });
});

// The group-atomic revert (SNOW-658 review). One tap on a provider row starts
// one load PER CODE, and they can disagree: the reported failure was AT
// loading while IT failed, which reverted IT alone and left the row reading
// off with Austria still drawn — persisted, so it survived a reload, and
// unclearable by re-toggling because `loadedCountries.has('at')` short-
// circuits the retry. A half-applied row is worse than a failed one: it shows
// coverage the control denies.
//
// These tests boot their own bundle (see bootWithFailingCountry) and so must
// stay last in the file.
describe('a member country whose fetch fails', () => {
  it('reverts the WHOLE group, and takes the loaded country off the map', async () => {
    await bootWithFailingCountry('it');

    row('country.albina').click();
    await waitFor(() => localStorage.getItem(IT_KEY) === 'false');

    // Austria loaded successfully and was persisted `true` on the click; the
    // revert has to undo that too, not only Italy's own key.
    expect(localStorage.getItem(AT_KEY)).toBe('false');
    expect(localStorage.getItem(IT_KEY)).toBe('false');
    expect(row('country.albina').getAttribute('aria-checked')).toBe('false');

    // State alone is not the assertion worth making — the map has to agree.
    // applyCountryFilters composes the enabled codes into every region layer's
    // filter, so Austria's absence there is what "not drawn" actually means.
    const filter = JSON.stringify(mapStub.getFilter('regions-fill'));
    expect(filter).not.toContain('"AT"');
    expect(filter).not.toContain('"IT"');
    expect(filter).toContain('"CH"');
  });

  it('reverts the same way whichever member fails', async () => {
    // The mirror case: the failing code is the one dispatched FIRST this time,
    // so the revert runs before its sibling's load has settled.
    await bootWithFailingCountry('at');

    row('country.albina').click();
    await waitFor(() => localStorage.getItem(AT_KEY) === 'false');

    expect(localStorage.getItem(AT_KEY)).toBe('false');
    expect(localStorage.getItem(IT_KEY)).toBe('false');
    expect(row('country.albina').getAttribute('aria-checked')).toBe('false');
  });

  it('leaves a single-country row reverting exactly as it always did', async () => {
    // A group of one takes the identical path, so the pre-merge behaviour has
    // to be unchanged: only France goes, and Switzerland is untouched.
    await bootWithFailingCountry('fr');
    // France has no row in this fixture; drive the same handler the picker
    // does, which is what a row click amounts to.
    document.dispatchEvent(
      new CustomEvent('snowdesk:country-toggle', { detail: { code: 'fr', next: true } }),
    );
    await waitFor(() => localStorage.getItem('snowdesk.map.overlay.country.fr') === 'false');

    expect(localStorage.getItem('snowdesk.map.overlay.country.fr')).toBe('false');
    expect(row('country.ch').getAttribute('aria-checked')).toBe('true');
    expect(JSON.stringify(mapStub.getFilter('regions-fill'))).toContain('"CH"');
  });
});
