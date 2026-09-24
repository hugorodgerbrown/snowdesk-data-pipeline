/*
 * tests/js/test_map_detail_popup_exclusivity.js — the anchored map-detail
 * popup takes part in "one open map overlay at a time" (SNOW-658).
 *
 * The popup — a resort pin's or an existing favourite pin's detail,
 * anchored to the point it describes — is the one map overlay that cannot
 * be reached without booting map.js, so it sits here rather than in the
 * surfaces matrix (tests/js/test_map_overlay_exclusivity_surfaces.js).
 *
 * SNOW-973 TOOK THE ROUTE OUT OF THAT POPUP. A saved route's detail is now
 * the docked `#route-detail-sheet` (static/js/map_route_detail.js), so the
 * route half of this file tests a SHEET reached through the same tap —
 * still here rather than in the matrix, because reaching it needs the whole
 * bundle booted against a MapLibre stub, which is the fixture cost that
 * keeps it out. The routes PANEL (`route-sheet`, SNOW-686) is a third
 * surface again, and stays in the matrix where it always was.
 *
 * SNOW-1018 MOVED THE SHEET ONE PRESS FURTHER. A tap opens rail one and
 * only the rail; the sheet opens from the rail's "Terrain and bulletin"
 * item, which calls the `details` function map.js handed the rail. The
 * rail is a recording stub here (tests/js/_route_rail_stub.js), so the
 * sheet's exclusivity is tested from that call — the same sheet, reached
 * one press later — and the fit is framed around the rail rather than the
 * sheet, because the rail is what a tap leaves over the map.
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

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/map_overlay_exclusivity.js';
import { loadMapBundle } from './_load_map_bundle.js';
import { installRouteRailStub } from './_route_rail_stub.js';

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
    // `railOpen` records the rail's state AT THE MOMENT OF THE FIT, which
    // is the only way to observe the order: the rail has to be on screen
    // and measurable before the camera can frame the route into the map
    // it leaves. `sheetOpen` holds the other half — a tap opens no sheet.
    fitBounds: (bounds, opts) => {
      const sheet = document.getElementById('route-detail-sheet');
      fits.push({
        bounds,
        opts,
        railOpen: !!window.pwaRouteRail && window.pwaRouteRail.isOpen(),
        sheetOpen: !!sheet && !sheet.hasAttribute('hidden'),
      });
    },
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
    <ul id="search-results" hidden></ul>
    <div id="route-detail-sheet" hidden tabindex="-1" data-overlay></div>
    <template id="route-detail-template">
      <div>
        <div data-route-detail-figures></div>
        <div data-route-detail-bulletin></div>
      </div>
    </template>`;
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

/** The route detail sheet, if it is open. */
function openRouteSheet() {
  const el = document.getElementById('route-detail-sheet');
  return el && !el.hasAttribute('hidden') ? el : null;
}

let mapStub;
let rail;

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
  // SNOW-973: the route detail sheet and the controller it attaches
  // through. Imported BEFORE the bundle so `window.pwaRouteDetail` exists
  // by the time map.js's route tap reaches for it — the page loads them in
  // the same order, both deferred.
  await import('../../static/js/map_sheet.js');
  await import('../../static/js/map_route_detail.js');
  rail = installRouteRailStub();
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
  window.pwaRouteDetail.close();
  rail.reset();
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

describe('a saved route opens rail one, and not the sheet (SNOW-1018)', () => {
  it('opens the rail on a route tap, with no sheet and no anchored popup', () => {
    tapTheRoute(mapStub);

    expect(rail.last()).not.toBeNull();
    expect(rail.last().feature.properties.uuid).toBe('r-1');
    expect(openRouteSheet()).toBeNull();
    expect(openPopup()).toBeNull();
  });

  it('hands the rail a way to open the sheet, and no Save for an owned route', () => {
    tapTheRoute(mapStub);

    expect(typeof rail.last().options.details).toBe('function');
    expect(rail.last().options.claim).toBeNull();
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
    expect(rail.last()).not.toBeNull();
  });

  it('still opens the rail when bounds are unusable', () => {
    // A route whose bbox cannot be read is a route the user can still be
    // told about — skip the fit, keep the detail. Throwing here would take
    // the tap out entirely.
    hitFeatures = [{
      ...ROUTE_FEATURE,
      properties: { ...ROUTE_FEATURE.properties, bounds: 'not-json' },
    }];

    expect(() => tapTheMap(mapStub)).not.toThrow();
    expect(fits).toHaveLength(0);
    expect(rail.last()).not.toBeNull();
  });

  it('frames with the plain padding when nothing can be measured', () => {
    // jsdom lays nothing out, so every rect is zero — the same answer a
    // `display: none` rail or a pre-layout measurement gives. A fit that
    // reserved space against a zero-height box would push the route off
    // centre for a strip that occupies nothing.
    tapTheRoute(mapStub);

    expect(fits[0].opts.padding).toEqual({
      top: 60, right: 40, bottom: 40, left: 40,
    });
  });
});

describe('the sheet, opened from the rail\'s menu', () => {
  it('opens on the rail\'s details press', () => {
    tapTheRoute(mapStub);

    rail.openDetails();

    expect(openRouteSheet()).not.toBeNull();
    // The rail stays: the sheet opens over it, not instead of it.
    expect(window.pwaRouteRail.isOpen()).toBe(true);
  });

  it('closes every other overlay as it opens, like any other detail', () => {
    // Free from MapSheet.attach, which registers the sheet under its own
    // DOM id — the same way the route popup got it from mountDetailPopup.
    const panel = { open: true };
    window.pwaMapOverlays.register('routes-panel-stub', {
      isOpen: () => panel.open,
      close: () => { panel.open = false; },
    });
    tapTheRoute(mapStub);

    rail.openDetails();

    expect(openRouteSheet()).not.toBeNull();
    expect(panel.open).toBe(false);
  });

  it('is closed when another overlay opens', () => {
    tapTheRoute(mapStub);
    rail.openDetails();
    expect(openRouteSheet()).not.toBeNull();

    window.pwaMapOverlays.opening('routes-panel-stub');

    expect(openRouteSheet()).toBeNull();
  });

  it('registers under its own DOM id', () => {
    expect(window.pwaMapOverlays.names()).toContain('route-detail-sheet');
  });
});

describe('framing a route around the rail (SNOW-1018)', () => {
  /** A DOMRect-shaped literal; jsdom returns all zeros without one. */
  function rect({ top, left, width, height }) {
    return {
      top, left, width, height, right: left + width, bottom: top + height,
    };
  }

  /**
   * Give #map and the rail real boxes, the way a browser would.
   *
   * @param {object} mapBox The map container's rect.
   * @param {object} railBox The rail's rect, once it is open.
   */
  function layOut(mapBox, railBox) {
    document.getElementById('map').getBoundingClientRect = () => mapBox;
    rail.element.getBoundingClientRect = () => railBox;
  }

  afterEach(() => {
    delete document.getElementById('map').getBoundingClientRect;
    delete rail.element.getBoundingClientRect;
  });

  it('opens the rail BEFORE the fit, and no sheet', () => {
    layOut(
      rect({ top: 0, left: 0, width: 1600, height: 900 }),
      rect({ top: 740, left: 16, width: 1568, height: 144 }),
    );

    tapTheRoute(mapStub);

    expect(fits[0].railOpen).toBe(true);
    expect(fits[0].sheetOpen).toBe(false);
  });

  it('reserves the room the rail takes at the bottom', () => {
    layOut(
      rect({ top: 0, left: 0, width: 1600, height: 900 }),
      rect({ top: 740, left: 16, width: 1568, height: 144 }),
    );

    tapTheRoute(mapStub);

    // The rail's top edge to the map's bottom — 160px, its height plus the
    // inset it sits above — on top of the 40 every fit starts with.
    expect(fits[0].opts.padding).toEqual({
      top: 60, right: 40, bottom: 200, left: 40,
    });
  });

  it('clamps a rail that would swallow the map', () => {
    // MapLibre throws outright when the padding exceeds the canvas; a rail
    // wrapped tall on a short phone must degrade to a usable fit.
    layOut(
      rect({ top: 0, left: 0, width: 390, height: 500 }),
      rect({ top: 150, left: 8, width: 374, height: 340 }),
    );

    tapTheRoute(mapStub);

    const padding = fits[0].opts.padding;
    // 40% of the 500px canvas, and no more.
    expect(padding.bottom).toBe(200);
    expect(padding.top + padding.bottom).toBeLessThan(500);
  });

  it('reserves space without reintroducing a zoom cap', () => {
    // SNOW-972's invariant, restated where it could most easily be lost:
    // padding frames into less map, which zooms OUT, and is not a floor.
    // tests/js/test_map_route_slope_layers.js holds the full version
    // against each route mark's own minzoom.
    layOut(
      rect({ top: 0, left: 0, width: 1600, height: 900 }),
      rect({ top: 740, left: 16, width: 1568, height: 144 }),
    );

    tapTheRoute(mapStub);

    expect(fits[0].opts).not.toHaveProperty('maxZoom');
  });
});

describe('what the route sheet says', () => {
  /** The text of the open sheet, whitespace-collapsed. */
  function sheetText() {
    return (openRouteSheet().textContent || '').replace(/\s+/g, ' ').trim();
  }

  it('leaves the name and the figures to the rail', () => {
    // SNOW-1018: the rail carries the name, the distance/ascent/descent
    // line and the profile, so the sheet repeating them would be the same
    // facts twice on one screen. What the rail does NOT show — the terrain
    // lines and the bulletin reading — is what the sheet is for now.
    tapTheRoute(mapStub);
    rail.openDetails();

    const text = sheetText();
    expect(text).not.toContain('Rosablanche');
    expect(text).not.toContain('12.4');
    expect(openRouteSheet().querySelector('svg')).toBeNull();
  });
});
