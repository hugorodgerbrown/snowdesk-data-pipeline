/*
 * tests/js/test_map_detail_popup_exclusivity.js — the anchored map-detail
 * popup takes part in "one open map overlay at a time" (SNOW-658).
 *
 * The popup — a resort pin's, an existing favourite pin's, or (since
 * SNOW-687) a saved route's detail, anchored to the point it describes — is
 * the one map overlay that cannot be reached without booting map.js, so it
 * sits here rather than in the surfaces matrix
 * (tests/js/test_map_overlay_exclusivity_surfaces.js).
 *
 * SNOW-687's route popup belongs HERE and not in that matrix, which already
 * carries `route-sheet` — the routes PANEL, registered by SNOW-686 through
 * window.MapSheet.attach. The panel and the popup are two different
 * surfaces owned by one feature: the panel is a sheet, the popup is this
 * registry member, and reaching the second needs the whole bundle booted
 * against a MapLibre stub, which is exactly the fixture cost that keeps it
 * out of the matrix.
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

/** The saved route the route-tap below lands on. */
const ROUTE_FEATURE = {
  layer: { id: 'routes-line' },
  geometry: {
    type: 'LineString',
    coordinates: [[7.5, 46.1, 1500], [7.54, 46.14, 2100]],
  },
  properties: {
    uuid: 'r-1',
    name: 'Rosablanche',
    distance_m: 12400.0,
    ascent_m: 850.0,
    bounds: [7.5, 46.1, 7.54, 46.14],
  },
};

/** Every popup this suite's stub has built, newest last. */
const popups = [];

/**
 * What the next hit-test returns. Defaults to the favourite pin, which is
 * what most of this file taps; the route cases swap it for one tap.
 */
let hitFeatures = [];

/** Every fitBounds call the stub has taken, newest last. */
const fits = [];

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
    // Both marker layers are 'installed' — markerUnderPoint filters the
    // exclusion set to layers actually present before hit-testing.
    getLayer: (id) => (['favourites-pin', 'routes-line'].includes(id) ? { id } : null),
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
    fitBounds: (bounds, opts) => { fits.push({ bounds, opts }); },
    easeTo: () => {},
    flyTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 8, lat: 46.5 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 8, lat: 46.5 }),
    // What the tap lands on — the exclusion-zone hit-test and the
    // region/resort query both read this.
    queryRenderedFeatures: () => hitFeatures,
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
        setDOMContent: (node) => { popup.node = node; return popup; },
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

/** Tap the map at the given point, whatever the hit-test currently returns. */
function tapTheMap(mapStub) {
  for (const handler of mapStub.handlers.click || []) {
    handler({
      point: { x: 10, y: 10 },
      lngLat: { lng: 7.52, lat: 46.12 },
      originalEvent: { target: document.body },
    });
  }
}

/** Tap the map where the favourite pin is. */
function tapTheFavouritePin(mapStub) {
  hitFeatures = [FAVOURITE_FEATURE];
  tapTheMap(mapStub);
}

/** Tap the map where the saved route's line is. */
function tapTheRoute(mapStub) {
  hitFeatures = [ROUTE_FEATURE];
  tapTheMap(mapStub);
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
  fits.length = 0;
  hitFeatures = [FAVOURITE_FEATURE];
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

describe('a saved route opens the same popup (SNOW-687)', () => {
  it('opens the anchored popup on a route tap', () => {
    tapTheRoute(mapStub);

    expect(openPopup()).not.toBeNull();
  });

  it('closes every other overlay as it opens, like any other detail', () => {
    // The whole reason the route popup reuses mountDetailPopup rather than
    // building a popup of its own, as the community-report pin does: it
    // joins the exclusivity registry for free.
    const sheet = { open: true };
    window.pwaMapOverlays.register('routes-panel-stub', {
      isOpen: () => sheet.open,
      close: () => { sheet.open = false; },
    });

    tapTheRoute(mapStub);

    expect(openPopup()).not.toBeNull();
    expect(sheet.open).toBe(false);
  });

  it('is closed when another overlay opens', () => {
    tapTheRoute(mapStub);
    expect(openPopup()).not.toBeNull();

    window.pwaMapOverlays.opening('routes-panel-stub');

    expect(openPopup()).toBeNull();
  });

  it('fits the viewport to the route bounds', () => {
    tapTheRoute(mapStub);

    expect(fits).toHaveLength(1);
    // MapLibre's fitBounds takes [[west, south], [east, north]] — the stored
    // bbox is the flat [minLon, minLat, maxLon, maxLat] GeoJSON shape, so a
    // wrong unpacking here would frame the wrong rectangle.
    expect(fits[0].bounds).toEqual([[7.5, 46.1], [7.54, 46.14]]);
  });

  it('survives bounds arriving as a JSON string', () => {
    // MapLibre serialises non-scalar feature properties, so a bbox read back
    // from queryRenderedFeatures can be a string rather than an array
    // depending on how the source was loaded. Both have to work.
    hitFeatures = [{
      ...ROUTE_FEATURE,
      properties: { ...ROUTE_FEATURE.properties, bounds: '[7.5,46.1,7.54,46.14]' },
    }];
    tapTheMap(mapStub);

    expect(fits[0].bounds).toEqual([[7.5, 46.1], [7.54, 46.14]]);
    expect(openPopup()).not.toBeNull();
  });

  it('still opens the popup when bounds are unusable', () => {
    // A route whose bbox cannot be read is a route the user can still be
    // told about — skip the fit, keep the detail. Throwing here would take
    // the tap out entirely.
    hitFeatures = [{
      ...ROUTE_FEATURE,
      properties: { ...ROUTE_FEATURE.properties, bounds: 'not-json' },
    }];

    expect(() => tapTheMap(mapStub)).not.toThrow();
    expect(fits).toHaveLength(0);
    expect(openPopup()).not.toBeNull();
  });
});

describe('what the route popup says', () => {
  /** The text of the popup currently on the map, whitespace-collapsed. */
  function popupText() {
    return (openPopup().node.textContent || '').replace(/\s+/g, ' ').trim();
  }

  it('names the route and states distance and ascent', () => {
    tapTheRoute(mapStub);

    const text = popupText();
    expect(text).toContain('Rosablanche');
    expect(text).toContain('12.4km');
    expect(text).toContain('850m ↑');
  });

  it('OMITS ascent entirely when the GPX carried no elevation', () => {
    // The safety-relevant one. Route.ascent_m is null — not zero — when the
    // source file has no <ele> at all, and "we don't know" and "flat" are
    // different facts about a mountain route. Rendering "0m ↑" for an
    // unknown would be a lie a user could plan on, so the line is absent
    // rather than zeroed. tests/routes/test_views.py asserts the null
    // survives the wire; this asserts what the map does with it.
    hitFeatures = [{
      ...ROUTE_FEATURE,
      properties: { ...ROUTE_FEATURE.properties, ascent_m: null },
    }];
    tapTheMap(mapStub);

    const text = popupText();
    expect(text).toContain('Rosablanche');
    expect(text).toContain('12.4km');
    expect(text).not.toContain('m ↑');
    expect(text).not.toContain('↑');
  });

  it('still renders a genuine zero ascent, which is not the same thing', () => {
    // The other side of the null test: 0 is a real measurement (a flat
    // valley loop), so it must survive. An implementation that used a
    // falsy check instead of an explicit null test passes the case above
    // and fails this one.
    hitFeatures = [{
      ...ROUTE_FEATURE,
      properties: { ...ROUTE_FEATURE.properties, ascent_m: 0 },
    }];
    tapTheMap(mapStub);

    expect(popupText()).toContain('0m ↑');
  });
});
