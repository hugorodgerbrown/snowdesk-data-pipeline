/*
 * tests/js/map_offline_boot_helpers.js — shared fixtures for the SNOW-638
 * map-boot tests (test_map_offline_boot.js and
 * test_map_offline_boot_control.js).
 *
 * Deliberately NOT named ``test_*.js`` — vitest.config.mjs's ``include``
 * glob only picks up ``test_*.js``, so this module is never itself run as
 * a test file, only imported by ones that are.
 *
 * Split into its own module (rather than one file importing the other's
 * exports) so each of the two SNOW-638 test files can do its OWN
 * ``vi.resetModules()`` + ``await import('.../map.js')`` in its own
 * ``beforeAll`` without ever pulling in a second, already-evaluated copy of
 * map.js via a shared import chain. See test_map_offline_boot.js's module
 * docstring for why re-importing map.js more than once per file is unsafe.
 */

import { vi } from 'vitest';

/** One region, as /api/regions.geojson would emit for it. */
export const REGIONS_GEOJSON = {
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
 * ``addSource``/``addLayer`` upgraded to ``vi.fn()`` recorders so a test can
 * assert exactly which sources/layers the boot handler installed, rather
 * than inferring it indirectly.
 */
export function stubMapLibre() {
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

/** The `#map` + search fixture map.js's boot reads. */
export function buildFixture() {
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

/** Whether `mapStub` installed the regions source and its choropleth fill layer. */
export function regionsInstalled(mapStub) {
  const sourceInstalled = mapStub.addSource.mock.calls.some((call) => call[0] === 'regions');
  const layerInstalled = mapStub.addLayer.mock.calls.some((call) => call[0].id === 'regions-fill');
  return sourceInstalled && layerInstalled;
}
