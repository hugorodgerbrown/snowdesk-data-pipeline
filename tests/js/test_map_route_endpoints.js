/*
 * tests/js/test_map_route_endpoints.js — installing the route start/finish
 * markers (static/js/map.js's installRoutesLayer).
 *
 * tests/js/test_route_markers_core.js covers the geometry and the pixels.
 * What is left is the wiring, and specifically the three parts of it that
 * fail SILENTLY — the layer still exists, so nothing throws, and the
 * markers are simply wrong or missing on the map:
 *
 *   - the ZOOM GATE. minzoom 10 is the whole of "only when zoomed in";
 *     lose it and every country-scale view is littered with flags on
 *     tracks a few pixels long.
 *   - the PIXEL RATIO. The icons are sized in device pixels, so
 *     registering a 40px image at ratio 1 draws a marker at twice its
 *     intended size on every screen — a plausible-looking marker, just
 *     wrong, and no layer assertion would catch it.
 *   - the ROLE SPLIT. One layer draws both markers, choosing the icon and
 *     the anchor from each feature's `role`. Get either expression's arms
 *     the wrong way round and the flag lands at the start.
 *
 * Booting map.js in jsdom follows tests/js/test_map_routes_overlay_bridge.js's
 * pattern — see its header for the rationale.
 */

import { beforeAll, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** Two routes: one open, one closed back on its own start. */
const ROUTES_FC = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[7.0, 46.0, 1500], [7.0, 46.01, 1800], [7.0, 46.02, 2100]],
      },
      properties: { uuid: 'open-route', name: 'Traverse' },
    },
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[8.0, 47.0], [8.0, 47.02], [8.0, 47.0]],
      },
      properties: { uuid: 'loop-route', name: 'Out and back' },
    },
  ],
};

/** Layer definitions as map.js added them, by id. */
const layers = new Map();
/** Source definitions as map.js added them, by id. */
const sources = new Map();
/** Registered images: id → the options addImage was given. */
const images = new Map();

/**
 * MapLibre stub that records what installRoutesLayer builds.
 *
 * @returns {object} The map stub.
 */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (event, layerOrHandler, maybeHandler) => {
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
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => ((layers.get(id) || {}).layout || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources.get(id) || null,
    addSource: (id, def) => { sources.set(id, { ...def, setData: () => {} }); },
    addLayer: (def) => { layers.set(def.id, def); },
    removeLayer: (id) => layers.delete(id),
    removeSource: (id) => sources.delete(id),
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
    listImages: () => [...images.keys()],
    hasImage: (id) => images.has(id),
    addImage: (id, image, options) => { images.set(id, { image, options }); },
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
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

/**
 * The narrowest 2D-canvas double that keeps the colour path honest.
 *
 * jsdom ships no 2D canvas, and `map.js` resolves a token to three
 * channels by filling a 1x1 one (cssColourChannels) — the only way to
 * parse every CSS colour syntax a token might hold. This parses the hex
 * the route tokens are written in, so an icon built from the wrong colour
 * still fails. Modelled on tests/js/test_map_downloaded_overlay_colour.js's
 * stub, which parses the `rgb()` form that file needs.
 *
 * @returns {() => void} Restores the original `getContext`.
 */
function stubCanvas2D() {
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function getContext(type) {
    if (type !== '2d') return original ? original.call(this, type) : null;
    let channels = [0, 0, 0, 255];
    return {
      set fillStyle(value) {
        const hex = /^#([0-9a-f]{6})$/i.exec(String(value).trim());
        if (hex) {
          const n = parseInt(hex[1], 16);
          channels = [(n >> 16) & 255, (n >> 8) & 255, n & 255, 255];
          return;
        }
        const rgb = /rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)/.exec(String(value));
        channels = rgb
          ? [Number(rgb[1]), Number(rgb[2]), Number(rgb[3]), 255]
          : [0, 0, 0, 255];
      },
      get fillStyle() {
        return `rgb(${channels[0]}, ${channels[1]}, ${channels[2]})`;
      },
      fillRect: () => {},
      getImageData: () => ({ data: Uint8ClampedArray.from(channels) }),
    };
  };
  return () => { HTMLCanvasElement.prototype.getContext = original; };
}

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-community-reports-url="/api/community-reports.geojson"
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

let mapStub;

beforeAll(async () => {
  localStorage.clear();
  stubCanvas2D();
  buildFixture();
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => Promise.resolve({
      ok: true,
      json: () => Promise.resolve(
        String(url).includes('routes.geojson') ? ROUTES_FC : EMPTY_FC,
      ),
    })),
  );

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();

  await window.pwaRoutesOverlay.show();
});

describe('the route endpoints layer', () => {
  it('is installed alongside the line layers', () => {
    expect(layers.has('routes-endpoints')).toBe(true);
    expect(sources.has('route-endpoints')).toBe(true);
  });

  it('is gated to minzoom 10 — "only when zoomed in"', () => {
    expect(layers.get('routes-endpoints').minzoom).toBe(10);
  });

  it('draws both markers even where they nearly collide', () => {
    const layout = layers.get('routes-endpoints').layout;

    // MapLibre's default symbol collision would silently drop one end of a
    // short track, which is exactly the track whose direction is hardest
    // to read from the line alone.
    expect(layout['icon-allow-overlap']).toBe(true);
    expect(layout['icon-ignore-placement']).toBe(true);
  });

  it('picks the icon and the anchor from each feature\'s role', () => {
    const layout = layers.get('routes-endpoints').layout;

    // The dot is centred on its point; the flag hangs off a pole whose
    // foot is the point.
    expect(layout['icon-image']).toEqual([
      'case', ['==', ['get', 'role'], 'start'], 'route-start-dot', 'route-finish-flag',
    ]);
    expect(layout['icon-anchor']).toEqual([
      'case', ['==', ['get', 'role'], 'start'], 'center', 'bottom-left',
    ]);
  });

  it('registers both icons at the core\'s own pixel ratio', () => {
    // Sized in device pixels: at ratio 1 every marker draws double size.
    const expected = window.pwaRouteMarkersCore.PIXEL_RATIO;
    expect(images.get('route-start-dot').options.pixelRatio).toBe(expected);
    expect(images.get('route-finish-flag').options.pixelRatio).toBe(expected);
  });

  it('paints the start dot in the route line\'s own colour', () => {
    const { image } = images.get('route-start-dot');
    const centre = (image.height / 2) * image.width + image.width / 2;
    const [r, g, b] = Array.from(image.data.slice(centre * 4, centre * 4 + 3));

    // --color-route-line, the same fuchsia routes-line is stroked in. A dot
    // in a different fuchsia from its own line is a bug nobody would think
    // to look for.
    expect([r, g, b]).toEqual([0xc0, 0x26, 0xd3]);
  });

  it('feeds the source the endpoints derived from the routes payload', () => {
    const data = sources.get('route-endpoints').data;
    const roles = data.features.map((f) => `${f.properties.uuid}:${f.properties.role}`);

    // The open route gets both markers; the loop gets the flag alone,
    // because its two ends are the same place.
    expect(roles).toEqual([
      'open-route:start',
      'open-route:end',
      'loop-route:end',
    ]);
  });
});
