/*
 * tests/js/test_map_boundaries_follow_basemap.js — the EAWS boundary outlines
 * are scoped by the ACTIVE BASEMAP, the bulletin data by the provider rows
 * (SNOW-891).
 *
 * `applyCountryFilters` composed one country filter from `countryState` onto
 * all seven region layers, so unticking every Bulletins provider fed the
 * deliberate always-false expression to the boundary lines and labels as well
 * as to the choropleth — Major / Minor / Micro stayed ticked and the map drew
 * nothing at all. That is the bug pinned first below.
 *
 * The two questions are now answered separately: a provider row says whose
 * bulletins to paint, and the basemap says which ground is drawn — a national
 * style renders blank past its own border, so an outline beyond it would
 * delineate ground no tile covers.
 *
 * Asserted through the live bundle rather than against a lifted copy of the
 * expression, because the split only holds if the DOM read
 * (`data-basemap-countries` on the checked picker row) and the filter
 * composition agree — see tests/js/test_map_country_group_toggle.js's header
 * for the general rationale of booting map.js in jsdom.
 */

import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

/** Every code the map carries, in map.js's own COUNTRY_KEYS order. */
const ALL_CODES = ['CH', 'FR', 'AT', 'IT'];

const BOUNDARY_LAYERS = [
  'regions-line', 'regions-label',
  'sub-regions-line', 'sub-regions-label',
  'major-regions-line', 'major-regions-label',
];

/** One region per country, so a filter can be read as a set of codes. */
const featureCollection = () => ({
  type: 'FeatureCollection',
  features: ['CH', 'FR', 'AT', 'IT'].map((country, i) => ({
    type: 'Feature',
    properties: { id: `${country}-${i}`, prefix: `${country}-${i}`, name: country, country },
    geometry: { type: 'Polygon', coordinates: [[[7, 46], [7, 47], [8, 47], [7, 46]]] },
  })),
});

/**
 * What the server would answer for one URL: the tier endpoints are
 * per-country and carry only that country's features, which is what makes a
 * country loaded twice visible as a duplicate rather than hidden in a payload
 * that always held all four.
 *
 * @param {string} url
 * @returns {Object} A FeatureCollection.
 */
const responseFor = (url) => {
  const code = (String(url).match(/country=([a-z]+)/) || [])[1];
  const all = featureCollection();
  if (!code) return all;
  const country = code.toUpperCase();
  return {
    type: 'FeatureCollection',
    features: all.features.filter((f) => f.properties.country === country),
  };
};

/** Minimal MapLibre stub — this suite reads filters, not paint. */
function stubMapLibre() {
  const handlers = {};
  const layers = new Set();
  const filters = new Map();
  const sourceData = {};
  const map = {
    on: (ev, a, b) => { (handlers[ev] ||= []).push(typeof a === 'function' ? a : b); },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: (id) => filters.get(id) ?? null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    // Sources are tracked (rather than the usual `() => null`) because the
    // dedupe test below reads what actually landed in the `regions` source:
    // a country loaded twice shows up there as duplicate features.
    getSource: (id) => (id in sourceData
      ? { setData: (data) => { sourceData[id] = data; } }
      : null),
    addSource: (id, def) => { sourceData[id] = (def && def.data) || null; },
    addLayer: (def) => {
      layers.add(def.id);
      if (def.filter !== undefined) filters.set(def.id, def.filter);
    },
    removeLayer: (id) => { layers.delete(id); filters.delete(id); },
    removeSource: () => {},
    moveLayer: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: () => {},
    setFilter: (id, filter) => { filters.set(id, filter); },
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
    sourceData,
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
 * The layers menu as the template renders it: two provider rows and two
 * basemap radios, the active one carrying its `data-basemap-countries`.
 *
 * @param {{providers: string, countries: (string|null)}} options
 *   `providers` is `#map`'s `data-default-overlays` (empty means no provider
 *   on); `countries` is the ACTIVE basemap row's declared coverage, or null
 *   to omit the attribute entirely.
 */
function buildFixture({ providers, countries, activeBasemap = 'openfreemap_liberty' }) {
  const basemapRow = (key, coverage) => `
    <li role="none">
      <button class="basemap-menu-item" role="menuitemradio"
              data-basemap-key="${key}"
              data-basemap-url="https://tiles.example/${key}/style.json"
              ${coverage === null ? '' : `data-basemap-countries="${coverage}"`}
              aria-checked="${key === activeBasemap ? 'true' : 'false'}">${key}</button>
    </li>`;

  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-major-regions-url="/api/major-regions.geojson"
         data-sub-regions-url="/api/sub-regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="${activeBasemap}"
         data-default-overlays="${providers}"
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
                    data-overlay-key="country.ch" data-country-codes="ch">SLF (CH)</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="country.albina"
                    data-country-codes="at it">ALBINA (AT, IT)</button>
          </li>
          ${basemapRow('openfreemap_liberty', activeBasemap === 'openfreemap_liberty' ? countries : null)}
          ${basemapRow('swisstopo_winter', activeBasemap === 'swisstopo_winter' ? countries : null)}
        </ul>
      </div>
    </div>`;
}

/**
 * Boot the whole bundle against `options`' fixture, with both boundary tiers
 * enabled so their layers are installed and can be read below.
 *
 * A fresh boot per test: `loadedCountries`, `countryState` and the caches are
 * module state, so a second scenario in the same module would answer from the
 * first one's fetches.
 *
 * @returns {Promise<Object>} the MapLibre stub.
 */
async function boot(options) {
  localStorage.clear();
  localStorage.setItem('snowdesk.map.overlay.l1', 'true');
  localStorage.setItem('snowdesk.map.overlay.l2', 'true');
  buildFixture(options);
  const mapStub = stubMapLibre();
  vi.stubGlobal('fetch', vi.fn((url) => Promise.resolve({
    ok: true,
    json: () => Promise.resolve(responseFor(url)),
  })));

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  // The boundary tiers load off the critical path, un-awaited by design.
  await new Promise((resolve) => setTimeout(resolve, 100));
  return mapStub;
}

/**
 * The country codes a layer's filter admits, uppercased, or `[]` for the
 * always-false expression every layer takes when nothing is selected.
 *
 * Unwraps the `['all', base, countryFilter]` composition, which is what a
 * layer carrying its own install-time filter would be given.
 *
 * @param {Object} mapStub
 * @param {string} layerId
 * @returns {string[]}
 */
function filterCodes(mapStub, layerId) {
  let filter = mapStub.getFilter(layerId);
  if (Array.isArray(filter) && filter[0] === 'all') filter = filter[filter.length - 1];
  if (!Array.isArray(filter)) return null;
  if (filter[0] === 'match') return filter[2];
  // ['in', ['get', 'country'], ['literal', []]] — the always-false form.
  return [];
}

beforeAll(() => {
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 1e10, usage: 0 }) },
    configurable: true,
  });
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }), match: async () => null },
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

afterAll(() => {
  delete globalThis.maplibregl;
});

describe('the boundary outlines and the bulletin fill are filtered separately', () => {
  it('keeps the outlines when every bulletin provider is off', async () => {
    // The reported bug: no provider ticked, so the choropleth empties — and
    // every boundary layer emptied with it, on a map whose Major / Minor /
    // Micro rows were all still ticked.
    const mapStub = await boot({ providers: '', countries: 'ch fr at it' });

    expect(filterCodes(mapStub, 'regions-fill')).toEqual([]);
    for (const layerId of BOUNDARY_LAYERS) {
      expect(filterCodes(mapStub, layerId)).toEqual(ALL_CODES);
    }
  });

  it("scopes the outlines to a national basemap's own country", async () => {
    // Swisstopo draws Switzerland and nothing else, so an outline past the
    // border would delineate ground no tile covers. ALBINA stays on, and its
    // choropleth is unaffected — the two questions are independent.
    const mapStub = await boot({
      providers: 'country.albina',
      countries: 'ch',
      activeBasemap: 'swisstopo_winter',
    });

    for (const layerId of BOUNDARY_LAYERS) {
      expect(filterCodes(mapStub, layerId)).toEqual(['CH']);
    }
    expect(filterCodes(mapStub, 'regions-fill')).toEqual(['AT', 'IT']);
  });

  it('does not move the boundary filter when a provider is toggled', async () => {
    const mapStub = await boot({ providers: 'country.ch', countries: 'ch fr at it' });
    expect(filterCodes(mapStub, 'regions-fill')).toEqual(['CH']);

    document.dispatchEvent(new CustomEvent('snowdesk:country-toggle', {
      detail: { code: 'ch', next: false },
    }));
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(filterCodes(mapStub, 'regions-fill')).toEqual([]);
    expect(filterCodes(mapStub, 'regions-line')).toEqual(ALL_CODES);
  });

  it('falls back to every country when the active row declares none', async () => {
    // The safe direction is drawing outlines nobody asked for; the unsafe one
    // is the blank map this ticket is about. A row with no attribute — an
    // older cached page, a fixture — must take the safe one.
    const mapStub = await boot({ providers: 'country.ch', countries: null });

    for (const layerId of BOUNDARY_LAYERS) {
      expect(filterCodes(mapStub, layerId)).toEqual(ALL_CODES);
    }
  });

  it('fetches a country\'s season ratings once however often its row is toggled', async () => {
    // Boot outlines every country the global basemap draws, so all four are
    // in `loadedCountries` before any provider row is switched on, and
    // `ensureCountryLoaded` short-circuits on the toggle that reveals one —
    // taking its choropleth paint with it. `loadCountryRatings` supplies that
    // paint, and memoises the payload, so the second switch-on costs nothing:
    // a row switched off and on repeatedly used to re-fetch the season every
    // time.
    await boot({ providers: '', countries: 'ch fr at it' });

    const atRatings = () => globalThis.fetch.mock.calls
      .map((call) => String(call[0]))
      .filter((url) => url.includes('/api/ratings.json') && url.includes('country=at'))
      .length;

    const toggle = async (next) => {
      document.dispatchEvent(new CustomEvent('snowdesk:country-toggle', {
        detail: { code: 'at', next },
      }));
      await new Promise((resolve) => setTimeout(resolve, 50));
    };

    await toggle(true);
    const afterFirst = atRatings();
    expect(afterFirst).toBeGreaterThan(0);

    await toggle(false);
    await toggle(true);

    expect(atRatings()).toBe(afterFirst);
  });

  it("loads the basemap's countries, not just the enabled providers'", async () => {
    // The filter alone is not enough: a country whose geometry was never
    // fetched draws no outline however the filter reads. SLF alone on the
    // global basemap must still fetch all four countries' micro regions.
    await boot({ providers: 'country.ch', countries: 'ch fr at it' });

    const fetched = new Set(
      globalThis.fetch.mock.calls
        .map((call) => String(call[0]))
        .filter((url) => url.includes('/api/regions.geojson'))
        .map((url) => new URL(url, 'https://example.test').searchParams.get('country')),
    );
    expect(fetched).toEqual(new Set(['ch', 'fr', 'at', 'it']));
  });
});

describe('a country is loaded at most once at a time', () => {
  it('does not start a second load when a swap arrives mid-flight', async () => {
    // `loadedCountries` only flips true once a load SETTLES, so it cannot
    // answer "is this already happening". Two overlapping calls would both
    // pass its guard and both concat their L4 answer into `geojsonCache` —
    // every polygon in that country drawn twice. Two quick basemap swaps is
    // the ordinary way to get there now that a swap loads countries.
    const mapStub = await boot({
      providers: '',
      countries: 'ch',
      activeBasemap: 'swisstopo_winter',
    });

    // Move to the global basemap the way the picker does: mark its row
    // checked (synchronously, before the style swap) and announce it.
    const menu = document.getElementById('basemap-menu');
    const global = menu.querySelector('[data-basemap-key="openfreemap_liberty"]');
    global.dataset.basemapCountries = 'ch fr at it';
    menu.querySelector('[data-basemap-key="swisstopo_winter"]')
      .setAttribute('aria-checked', 'false');
    global.setAttribute('aria-checked', 'true');

    globalThis.fetch.mockClear();
    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));
    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));
    await new Promise((resolve) => setTimeout(resolve, 100));

    const microFetches = globalThis.fetch.mock.calls
      .map((call) => String(call[0]))
      .filter((url) => url.includes('/api/regions.geojson') && url.includes('country=at'));
    expect(microFetches).toHaveLength(1);
    // And nothing arrived twice in the source the choropleth reads.
    const ids = mapStub.sourceData['regions'].features.map((f) => f.properties.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});
