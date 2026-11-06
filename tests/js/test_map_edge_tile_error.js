/*
 * tests/js/test_map_edge_tile_error.js — SNOW-1457: a swisstopo edge tile
 * that fails asks MapLibre for one more frame.
 *
 * The swisstopoedge:// protocol (static/js/swisstopo_edge.js) rejects a
 * tile whose WMTS request failed, so MapLibre marks it errored. MapLibre
 * 4.7.1 asks for no frame when a tile errors, and fires `load` only from a
 * frame. When the last tile still loading is one that failed (WMTS down, or
 * offline with the style cached), `load` waited for the reader to move the
 * map, and every layer the page installs on it waited too. The Playwright
 * journey tests/e2e/test_map_3d_view.py caught it, since every WMTS request
 * is aborted there.
 *
 * What is pinned: map.js's `error` handler calls `triggerRepaint` for an
 * error from the edge source, and only for that source; and the error
 * still never swaps in the offline fallback style.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

/**
 * A MapLibre stub that records its handlers and its repaints.
 *
 * @returns {object} The stub map.
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
    removeControl: () => {},
    getLayer: () => null,
    getFilter: () => null,
    getLayoutProperty: () => null,
    getPaintProperty: () => null,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: () => {},
    removeLayer: () => {},
    removeSource: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: () => {},
    setFilter: () => {},
    setFeatureState: () => {},
    removeFeatureState: () => {},
    setStyle: vi.fn(),
    isStyleLoaded: () => false,
    getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    loaded: () => false,
    areTilesLoaded: () => false,
    listImages: () => [],
    hasImage: () => true,
    addImage: () => {},
    triggerRepaint: vi.fn(),
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 8, lat: 46.5 }),
    getBounds: () => ({ getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48 }),
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
      return { setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }), remove: () => {} };
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

/**
 * Boot the map bundle on the stub.
 *
 * @returns {Promise<object>} The stub map, with `fireError(event)`.
 */
async function boot() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="swisstopo_winter"
         data-season-end="2026-05-31"></div>
    <ul id="basemap-menu">
      <li role="none">
        <button type="button" class="basemap-menu-item" data-basemap-key="swisstopo_winter"
                data-basemap-url="https://example.test/winter.json" aria-checked="false">Swisstopo (CH)</button>
      </li>
    </ul>`;
  const map = stubMapLibre();
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({}) })));
  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  map.fireError = (event) => {
    for (const handler of map.handlers.error || []) handler(event);
  };
  return map;
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
});

describe('a failed swisstopo edge tile', () => {
  it('asks for one more frame, so load can fire', async () => {
    const map = await boot();
    map.fireError({ sourceId: 'swisstopo-edge', tile: {}, error: new Error('swisstopo edge: tile request failed') });
    expect(map.triggerRepaint).toHaveBeenCalledTimes(1);
  });

  it('never swaps in the offline fallback style', async () => {
    const map = await boot();
    map.setStyle.mockClear();
    map.fireError({ sourceId: 'swisstopo-edge', tile: {}, error: new Error('failed') });
    expect(map.setStyle).not.toHaveBeenCalled();
  });

  it('leaves another source’s tile error alone', async () => {
    const map = await boot();
    map.fireError({ sourceId: 'regions', tile: {}, error: new Error('failed') });
    expect(map.triggerRepaint).not.toHaveBeenCalled();
  });
});
