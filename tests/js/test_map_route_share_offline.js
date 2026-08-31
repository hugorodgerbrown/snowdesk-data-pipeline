/*
 * tests/js/test_map_route_share_offline.js — a `?route_share=` arrival whose
 * routes layer never loads (SNOW-764).
 *
 * The deep link waits for the routes source to appear before it frames the
 * shared track, because the boot restore is async and the two orders both
 * happen. That wait is a `sourcedata` listener documented to unbind "on the
 * first routes load it sees, found or not" — and it does, as long as there
 * IS one. Offline with nothing in the overlay cache there is not: the fetch
 * fails, no layer is installed, no matching `sourcedata` is ever emitted, and
 * the listener stayed bound for the rest of the session, running on every
 * source event the map made.
 *
 * The load's own failure branch releases it now, which is what this pins: a
 * failed routes load leaves exactly the map's own permanent `sourcedata`
 * listener (the attribution updater) bound and nothing else.
 *
 * Its own file rather than a case in tests/js/test_map_route_share.js: that
 * suite boots with a SUCCESSFUL routes fetch, and map.js reads both the
 * deep-link parameter and the routes URL once, at boot.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import '../../static/js/share.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

const SHARE_TOKEN = 'tok123abc';

/**
 * Minimal MapLibre stub. See test_map_route_share.js for the model.
 *
 * `off` really unbinds, because the count of bound `sourcedata` handlers is
 * the whole assertion here.
 *
 * @returns {object} The map stub.
 */
function stubMapLibre() {
  const handlers = {};
  const sources = {};
  const installedLayers = new Set();
  const map = {
    on: (event, layerOrHandler, maybeHandler) => {
      if (typeof layerOrHandler === 'function') {
        (handlers[event] ||= []).push(layerOrHandler);
      } else if (typeof maybeHandler === 'function') {
        (handlers[`${event}:${layerOrHandler}`] ||= []).push(maybeHandler);
      }
    },
    once: () => {},
    off: (event, handler) => {
      const list = handlers[event] || [];
      const at = list.indexOf(handler);
      if (at !== -1) list.splice(at, 1);
    },
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (installedLayers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources[id] || null,
    addSource: (id, spec) => { sources[id] = Object.assign({ setData: () => {} }, spec); },
    addLayer: (spec) => { installedLayers.add(spec.id); },
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
    getBearing: () => 0,
    getPitch: () => 0,
    getCenter: () => ({ lng: 7, lat: 46 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 7, lat: 46 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      const popup = {
        setHTML: () => popup,
        setDOMContent: () => popup,
        setLngLat: () => popup,
        addTo: () => popup,
        getElement: () => document.createElement('div'),
        on: () => {},
        remove: () => {},
      };
      return popup;
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/** The DOM map.js's boot reads, plus the routes offline toast. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-routes-upload-eligible="true"
         data-routes-signin-url="/account/sign-in/"
         data-route-claim-url-template="/routes/partials/share/__TOKEN__/claim/"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="map-offline-toast-routes" class="hidden"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

let mapStub;

beforeAll(async () => {
  localStorage.clear();
  buildFixture();
  window.history.replaceState(null, '', `/?route_share=${SHARE_TOKEN}`);
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  // The routes feed alone fails. window.pwaMapOverlayCache is deliberately
  // never defined, which is the "offline with nothing cached" state: the
  // read-back is skipped and the load takes its failure branch.
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => (String(url).includes('routes.geojson')
      ? Promise.reject(new Error('offline'))
      : Promise.resolve({
        ok: true,
        json: () => Promise.resolve(EMPTY_FC),
        text: () => Promise.resolve(''),
      }))),
  );

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/elevation_profile_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  // The deep link enables the overlay, which starts the load; its failure
  // lands several turns after the `load` handler returns.
  for (let round = 0; round < 20; round += 1) await Promise.resolve();
});

describe('a shared-route deep link whose layer never loads', () => {
  it('says the routes layer is unavailable', () => {
    // The scenario guard: without this the test below could pass because
    // nothing was ever attempted.
    expect(
      document.getElementById('map-offline-toast-routes').classList.contains('hidden'),
    ).toBe(false);
  });

  it('installs no routes source', () => {
    expect(mapStub.getSource('routes')).toBeNull();
  });

  it('leaves no deep-link listener bound to sourcedata', () => {
    // One remains: map.js's own attribution updater, which is bound for the
    // life of the map. A second is the leak — the deep link waiting for an
    // install that has already failed, and which nothing will retry.
    expect(mapStub.handlers.sourcedata.length).toBe(1);
  });
});
