/*
 * tests/js/test_map_detail_popup_exclusivity.js — the anchored map-detail
 * popup takes part in "one open map overlay at a time" (SNOW-658).
 *
 * The popup — a resort pin's or an existing favourite pin's detail,
 * anchored to the point it describes — is the sixth map overlay and the
 * one that cannot be reached without booting map.js, so it sits here
 * rather than in the four-surface matrix
 * (tests/js/test_map_overlay_exclusivity_surfaces.js).
 *
 * Both directions matter, and before this ticket only one existed: map.js
 * dispatched ``snowdesk:map-detail-opening``, which favourites.js alone
 * listened for, and favourites.js dispatched
 * ``snowdesk:favourite-detail-close`` from its own open. The report sheet,
 * the downloads sheet and the layers menu were in neither conversation, so
 * a pin tap opened a popup straight over any of them.
 *
 * Booting map.js in jsdom follows tests/js/test_map_favourites_overlay_bridge.js's
 * pattern — see its header for the general rationale. favourites.js is NOT
 * loaded: map.js hands it an empty ``[data-favourite-detail]`` container to
 * fill through ``snowdesk:favourite-selected`` and gives up if it comes back
 * empty, so the listener below stands in for that one contract (covered on
 * its own in tests/js/test_favourites.js).
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** The favourite pin the tap below lands on. */
const FAVOURITE_FEATURE = {
  layer: { id: 'favourites-pin' },
  geometry: { type: 'Point', coordinates: [7.5, 46.1] },
  properties: { uuid: 'f-1', name: 'Verbier' },
};

/** Every popup this suite's stub has built, newest last. */
const popups = [];

/**
 * Minimal MapLibre stub: enough for map.js to boot, plus a hit-test that
 * always finds the favourite pin and a Popup that records its own life.
 */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (event, layerOrHandler, maybeHandler) => {
      // Layer-scoped handlers take three arguments; the generic click
      // dispatcher this suite fires takes two.
      if (typeof layerOrHandler === 'function') {
        (handlers[event] ||= []).push(layerOrHandler);
      } else if (typeof maybeHandler === 'function') {
        (handlers[`${event}:${layerOrHandler}`] ||= []).push(maybeHandler);
      }
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (id === 'favourites-pin' ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: () => {},
    removeLayer: () => {},
    removeSource: () => {},
    moveLayer: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: () => {},
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
    // The tap always lands on the favourite pin — the exclusion-zone
    // hit-test and the region/resort query both read this.
    queryRenderedFeatures: () => [FAVOURITE_FEATURE],
    resize: () => {},
    handlers,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      const popup = {
        open: true,
        closeHandlers: [],
        setHTML: () => popup,
        setDOMContent: () => popup,
        setLngLat: () => popup,
        addTo: () => popup,
        getElement: () => document.createElement('div'),
        on: (event, handler) => {
          if (event === 'close') popup.closeHandlers.push(handler);
        },
        remove: () => {
          popup.open = false;
          popup.closeHandlers.forEach((handler) => handler());
        },
      };
      popups.push(popup);
      return popup;
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-favourites-url="/favourites/favourites.geojson"
         data-favourites-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/** Tap the map where the favourite pin is. */
function tapTheFavouritePin(mapStub) {
  for (const handler of mapStub.handlers.click || []) {
    handler({ point: { x: 10, y: 10 }, originalEvent: { target: document.body } });
  }
}

/** The popup currently on the map, if any. */
function openPopup() {
  return popups.filter((popup) => popup.open).at(-1) || null;
}

let mapStub;

beforeAll(async () => {
  localStorage.clear();
  buildFixture();
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) })),
  );

  // Assigned before the bundle boots — map.js constructs the map at parse
  // time, so the stub has to be in place first.
  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom, and the detail-popup surface
  // hangs off it.
  for (const handler of mapStub.handlers.load || []) await handler();

  // Stand in for favourites.js: map.js gives up on an empty container, so
  // without a filler no popup is ever mounted.
  document.addEventListener('snowdesk:favourite-selected', (event) => {
    event.detail.container.appendChild(document.createElement('span'));
  });
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

beforeEach(() => {
  popups.length = 0;
});

describe('the anchored detail popup and the shared registry', () => {
  it('registers itself, so any other overlay can close it', () => {
    expect(window.pwaMapOverlays.names()).toContain('map-detail-popup');
  });

  it('closes every other overlay as it opens', () => {
    const sheet = { open: true };
    window.pwaMapOverlays.register('a-sheet', {
      isOpen: () => sheet.open,
      close: () => { sheet.open = false; },
    });

    tapTheFavouritePin(mapStub);

    expect(openPopup()).not.toBeNull();
    expect(sheet.open).toBe(false);
  });

  it('is closed when another overlay opens', () => {
    tapTheFavouritePin(mapStub);
    expect(openPopup()).not.toBeNull();

    window.pwaMapOverlays.opening('a-sheet');

    expect(openPopup()).toBeNull();
  });
});
