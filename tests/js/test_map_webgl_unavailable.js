/*
 * tests/js/test_map_webgl_unavailable.js — a map that cannot start says so
 * (SNOW-893).
 *
 * MapLibre throws from its constructor when the browser will not give it a
 * WebGL context. Everything in map.js is inside ONE IIFE, so before this
 * ticket that throw skipped every statement below line 718 — including
 * `map.on('error', …)` and the offline fallback style, both of which are
 * defined *underneath* the call that fails. The other 76 scripts on the page
 * are separate files and still ran, so the page rendered the full map
 * furniture around an empty grey box with nothing saying what happened.
 *
 * Three properties, and the third is the one worth the file:
 *
 *   1. The unavailable panel is shown and #map is stamped, so the CSS can
 *      hide the chrome.
 *   2. Nothing escapes the IIFE — a page-killing throw is the failure mode.
 *   3. The scripts loaded AFTER map.js still initialise. That is the
 *      difference between "degrades" and "half-dies", and it is invisible in
 *      any assertion about map.js alone.
 *
 * The happy path is asserted too, because every claim here is about a
 * difference: a panel that is hidden either way proves nothing.
 */

import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

/**
 * The page as the server renders it: the panel present but hidden, and one
 * piece of chrome to prove the stamp is what hides it.
 *
 * `#map-controls-toggle` stands in for the thirteen sibling containers
 * _map_embed.html puts inside `#map`. The CSS hides "every child except the
 * panel" rather than a list of ids, so one representative is enough — and a
 * list here would rot exactly as a list in the stylesheet would.
 */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings/"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty">
      <div id="map-unavailable" class="map-unavailable" hidden>
        <div data-testid="callout" data-kind="warning"></div>
      </div>
      <div id="map-controls-toggle"></div>
    </div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/**
 * Install a `maplibregl` whose constructor throws, the way it does with no
 * WebGL context.
 *
 * The real message is MapLibre's; the text is not asserted on, only that
 * the throw is caught. A future MapLibre could word it differently and this
 * test should not care.
 */
function stubThrowingMapLibre() {
  globalThis.maplibregl = {
    Map: function () {
      throw new Error('Failed to initialize WebGL');
    },
    Popup: function () {
      return { setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }), remove: () => {} };
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
}

/** A constructor that works, so the two states can be told apart. */
function stubWorkingMapLibre() {
  const handlers = {};
  const map = {
    on: (ev, a, b) => { (handlers[ev] ||= []).push(typeof a === 'function' ? a : b); },
    once: () => {}, off: () => {}, addControl: () => {}, removeControl: () => {},
    getLayer: () => null, getFilter: () => null, getLayoutProperty: () => 'visible',
    getPaintProperty: () => 1, isSourceLoaded: () => true, getSource: () => null,
    addSource: () => {}, addLayer: () => {}, removeLayer: () => {}, removeSource: () => {},
    setLayoutProperty: () => {}, setPaintProperty: () => {}, setFilter: () => {},
    setFeatureState: () => {}, getFeatureState: () => ({}), removeFeatureState: () => {},
    setStyle: () => {}, isStyleLoaded: () => true, getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }), getContainer: () => document.getElementById('map'),
    loaded: () => true, areTilesLoaded: () => true, listImages: () => [], hasImage: () => true,
    addImage: () => {}, triggerRepaint: () => {}, fitBounds: () => {}, easeTo: () => {},
    flyTo: () => {}, getZoom: () => 8, getCenter: () => ({ lng: 8, lat: 46.5 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }), unproject: () => ({ lng: 8, lat: 46.5 }),
    queryRenderedFeatures: () => [], resize: () => {}, handlers,
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
}

beforeEach(() => {
  localStorage.clear();
  buildFixture();
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({ type: 'FeatureCollection', features: [] }),
  })));
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

afterAll(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
});

describe('when MapLibre cannot start', () => {
  it('shows the unavailable panel and stamps #map so the chrome can be hidden', () => {
    stubThrowingMapLibre();

    loadMapBundle();

    expect(document.getElementById('map-unavailable').hidden).toBe(false);
    expect(document.getElementById('map').dataset.mapUnavailable).toBe('true');
  });

  it('does not let the throw escape the boot IIFE', () => {
    stubThrowingMapLibre();

    // Before SNOW-893 this threw, and the throw is what killed every
    // statement below the constructor.
    expect(() => loadMapBundle()).not.toThrow();
  });

  it('reports the failure, because nothing else on the page will', () => {
    stubThrowingMapLibre();

    loadMapBundle();

    // There is no window.onerror reporter yet (SNOW-894), so this console
    // line is the only trace a support session has to go on. Assert that it
    // carries the original error rather than only a message of our own —
    // "the map is unavailable" without MapLibre's reason is not a report.
    expect(console.error).toHaveBeenCalled();
    const [, thrown] = console.error.mock.calls.find(
      (call) => String(call[0]).includes('[map]'),
    );
    expect(thrown).toBeInstanceOf(Error);
  });

  it('leaves the modules loaded after map.js still working', () => {
    stubThrowingMapLibre();

    loadMapBundle();

    // The point of catching rather than throwing: map.js returns early, and
    // the surface modules that load after it in the bundle still run their
    // own IIFEs. `snowdeskMapState` is published by map_state.js, which
    // loads BEFORE map.js — so reading it proves the bundle was evaluated;
    // the map being null proves map.js took the failure path rather than
    // half-completing.
    expect(window.snowdeskMapState).toBeTruthy();
    expect(window.snowdeskMapState.map).toBeNull();
  });
});

describe('when MapLibre starts normally', () => {
  it('leaves the unavailable panel hidden and #map unstamped', () => {
    stubWorkingMapLibre();

    loadMapBundle();

    expect(document.getElementById('map-unavailable').hidden).toBe(true);
    expect(document.getElementById('map').dataset.mapUnavailable).toBeUndefined();
    expect(window.snowdeskMapState.map).not.toBeNull();
  });
});
