/*
 * tests/js/test_map_offline_boot.js — Vitest DOM test for SNOW-638: the
 * micro-region (L4) choropleth failed to paint offline even though its own
 * sync dot showed green.
 *
 * Root cause: static/js/map.js's ``map.on('load', ...)`` boot handler
 * fetched regions.geojson, ratings.json and resorts.json in a single
 * ``Promise.all`` with only the ratings leg guarded by a ``.catch()``. The
 * resorts leg (``/api/resorts.json``) is a plain-JSON, non-cacheable
 * sibling deliberately absent from sw.js's ``STATIC_PATHS`` (only the
 * ``.geojson`` variant is listed there) — offline, that fetch rejects on
 * every single load. With no ``.catch()`` on that leg the whole
 * ``Promise.all`` rejected, so ``installRegionsLayers``, the choropleth
 * paint, and ``applyCountryFilters`` never ran, even though the regions
 * feed the sync dot itself probes (``/api/regions.geojson``) was fine.
 *
 * The fix decouples the three fetches: the resorts (and ratings) legs
 * degrade to an empty payload on failure; the regions leg is guarded
 * separately and, on failure, makes the boot handler return early rather
 * than throw. Case 1 below is the regression — it fails against the
 * pre-fix map.js (regions/layers never installed) and passes after the
 * fix. Case 2 is a negative control — the same assertions hold when every
 * fetch resolves, so a test that always passed regardless of the fix would
 * be caught.
 *
 * Booting map.js in jsdom follows the pattern documented in
 * test_map_download_bytes.js: one script of top-level IIFEs,
 * ``map.on('load')`` never fires on its own in jsdom, so the test drives it
 * manually via the stubbed map's recorded ``load`` handlers.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      properties: { id: 'CH-4115', name: 'Martigny — Verbier' },
      geometry: {
        type: 'Polygon',
        coordinates: [[[7.0, 46.0], [7.01, 46.0], [7.01, 46.01], [7.0, 46.01], [7.0, 46.0]]],
      },
    },
  ],
};

/**
 * Minimal MapLibre stub — adapted from test_map_groupings_guard.js's, with
 * ``addSource``/``addLayer`` upgraded to ``vi.fn()`` recorders (per the
 * plan) so a test can assert exactly which sources/layers the boot handler
 * installed, rather than inferring it indirectly.
 */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    getLayer: () => null,
    getFilter: () => null,
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: vi.fn(),
    addLayer: vi.fn(),
    removeLayer: () => {},
    removeSource: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: () => {},
    setFilter: () => {},
    setFeatureState: () => {},
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
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

function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="standard"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/**
 * Boot map.js fresh against the given fetch mock and fire its ``load``
 * handler(s), the way ``test_map_download_bytes.js`` does. Returns the
 * stubbed map so a test can inspect ``addSource``/``addLayer`` calls.
 */
async function bootMap(fetchImpl) {
  buildFixture();
  const mapStub = stubMapLibre();
  vi.stubGlobal('fetch', vi.fn(fetchImpl));

  vi.resetModules();
  // SNOW-618: map.js's search box delegates to this; home.html loads it
  // immediately before map.js, and map.js does not guard for its absence.
  await import('../../static/js/search_core.js');
  // SNOW-623: map.js's choropleth paint delegates to this.
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/map.js');
  for (const handler of mapStub.handlers.load || []) await handler();
  return mapStub;
}

/** Whether `mapStub` installed the regions source and its choropleth fill layer. */
function regionsInstalled(mapStub) {
  const sourceInstalled = mapStub.addSource.mock.calls.some((call) => call[0] === 'regions');
  const layerInstalled = mapStub.addLayer.mock.calls.some((call) => call[0].id === 'regions-fill');
  return sourceInstalled && layerInstalled;
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
});

describe('map.js boot handler — offline resorts feed (SNOW-638)', () => {
  it('still installs the regions choropleth when /api/resorts.json rejects (the offline repro)', async () => {
    const mapStub = await bootMap((url) => {
      const href = String(url);
      if (href.includes('regions.geojson')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(REGIONS_GEOJSON) });
      }
      if (href.includes('resorts.json')) {
        // Exactly what an offline browser does for a fetch sw.js never
        // intercepts: the promise itself rejects, not a non-ok response.
        return Promise.reject(new TypeError('Failed to fetch'));
      }
      // ratings.json and anything else — resolve emptyish, matching the
      // ratings leg's own existing guard.
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });

    expect(regionsInstalled(mapStub)).toBe(true);
  });

  it('installs the regions choropleth when every boot fetch resolves (negative control)', async () => {
    const mapStub = await bootMap((url) => {
      const href = String(url);
      if (href.includes('regions.geojson')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(REGIONS_GEOJSON) });
      }
      if (href.includes('resorts.json')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
    });

    expect(regionsInstalled(mapStub)).toBe(true);
  });
});
