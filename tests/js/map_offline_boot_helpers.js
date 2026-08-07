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

/**
 * A well-formed empty FeatureCollection — safe default input for any
 * `.geojson` consumer that does `data.features.forEach(...)` or similar.
 */
export const EMPTY_FEATURE_COLLECTION = { type: 'FeatureCollection', features: [] };

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

/**
 * Build a `fetch` implementation that answers EVERY endpoint map.js's boot
 * path might touch — not just the ones a given test cares about — with a
 * correctly-shaped payload by default.
 *
 * Why this matters: with real regions features (unlike every OTHER map.js
 * test's empty-FC fixture — see the module docstring), the boot handler
 * runs all the way through ``installRegionsLayers`` and then fires a lot of
 * work it deliberately does NOT await: ``ensureCountryLoaded`` for every
 * enabled country (major/sub-region + ratings top-up fetches),
 * ``restoreOverlay`` for l1/l2/resorts, and the l3 bulletin-groupings load
 * that rides along with L4 (on by default). A bare ``{}`` is the wrong
 * shape for a ``.geojson`` endpoint — code doing
 * ``payload.features.forEach(...)`` throws inside a floating (never
 * awaited) promise. Those unhandled rejections settle at unpredictable
 * times and got misattributed by the Vitest/Node runner to whatever frame
 * happened to be executing when they did — including, on CI, a
 * ``ReferenceError`` reported against a completely different import
 * (SNOW-638's second CI failure). Answering every ``.geojson`` URL with a
 * real (if empty) FeatureCollection, and everything else with ``{}``,
 * removes the floating-rejection source at its root rather than papering
 * over the symptom.
 *
 * @param overrides - ordered ``[predicate, responder]`` pairs tried before
 *   the default; ``predicate(hrefString)`` decides whether ``responder``
 *   (which may return a rejected Promise, to simulate a genuine fetch
 *   failure) handles this URL.
 */
export function buildFetchImpl(overrides = []) {
  return (url) => {
    const href = String(url);
    for (const [predicate, responder] of overrides) {
      if (predicate(href)) return responder(href);
    }
    if (href.includes('.geojson')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(EMPTY_FEATURE_COLLECTION),
      });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  };
}

/**
 * Resolve after `waitMs` — lets fire-and-forget boot promises (see
 * ``buildFetchImpl``'s docstring) settle before a test's ``beforeAll``
 * returns, rather than leaking them past it. Mirrors the ``tick(ms)``
 * pattern in ``test_map_groupings_guard.js``.
 */
export function tick(waitMs = 50) {
  return new Promise((resolve) => setTimeout(resolve, waitMs));
}

/**
 * Capture Node-level ``unhandledRejection`` events for the life of a test
 * file, so a stray rejection surfaces as a visible, attributable assertion
 * failure in the file it actually happened in — rather than (per SNOW-638's
 * CI failure) a ``ReferenceError`` misattributed to whatever import was
 * in-flight elsewhere in the same worker when it settled.
 *
 * Call ``stop()`` in ``afterAll``: registering a listener suppresses
 * Node's default handling for the life of that listener, so a leaked one
 * would swallow genuine crashes in files that run afterwards.
 */
export function captureUnhandledRejections() {
  const rejections = [];
  const onUnhandledRejection = (reason) => {
    rejections.push(reason);
  };
  process.on('unhandledRejection', onUnhandledRejection);
  return {
    rejections,
    stop: () => process.off('unhandledRejection', onUnhandledRejection),
  };
}
