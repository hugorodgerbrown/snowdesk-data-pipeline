/*
 * tests/js/test_map_roundel_overlay_state.js — Vitest DOM test for
 * static/js/map_roundel_overlay_state.js (SNOW-658).
 *
 * The three overlay roundels — downloads, favourites, field observations —
 * each carry ``data-overlay-shown``, meaning one thing on all three: "the
 * overlay this roundel's panel switches is drawn on the map right now".
 * Before this ticket only one of them carried a state at all, and it meant
 * something else ("this device holds a downloaded area").
 *
 * What is worth testing here is not the attribute write — it is that the
 * state stays TRUE over time. Three paths can change an overlay's
 * visibility, and only the first is obvious:
 *
 *   1. the panel switch, through the bridge (both directions, all three);
 *   2. the bulletins-exclusivity path, which switches the downloaded
 *      squares off from a control in a different surface entirely;
 *   3. the ``styledata`` re-seed after a basemap swap, which rewrites
 *      overlayState wholesale — the path an implementation that painted
 *      only at boot would still pass every other test here.
 *
 * Booting map.js in jsdom follows test_map_favourites_overlay_bridge.js's
 * pattern — see its header, and test_map_download_bytes.js's, for the
 * general rationale.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const DOWNLOADS_ROUNDEL = 'map-custom-download-control';
const FAVOURITES_ROUNDEL = 'favourite-add-btn';
const REPORTS_ROUNDEL = 'report-btn';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** Minimal MapLibre stub — layer layout only, which is all map.js needs here. */
function stubMapLibre() {
  const handlers = {};
  const layouts = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layouts.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => (layouts.get(id) || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => {
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => {
      layouts.delete(id);
    },
    removeSource: () => {},
    moveLayer: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
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
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
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
  return map;
}

/**
 * The map DOM plus the three roundels and the strings template, matching
 * what _map_embed.html renders (the server-rendered aria-labels are the
 * stems the module composes its "shown" label from).
 */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-favourites-url="/favourites/favourites.geojson"
         data-favourites-eligible="true"
         data-community-reports-url="/api/community-reports.geojson"
         data-community-reports-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <button id="${DOWNLOADS_ROUNDEL}"
            data-overlay-shown="false"
            aria-label="Manage offline downloads"
            title="Manage offline downloads"></button>
    <button id="${FAVOURITES_ROUNDEL}"
            data-overlay-shown="false"
            aria-label="Your favourites"></button>
    <button id="${REPORTS_ROUNDEL}"
            data-overlay-shown="false"
            aria-label="Your field observations"></button>
    <template id="map-roundel-strings-template">
      <span data-string="roundel-overlay-shown">%(label)s — shown on the map</span>
    </template>`;
}

/** Poll `predicate` until it holds or the budget runs out. */
async function waitFor(predicate, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  return predicate();
}

/** The `data-overlay-shown` value on one roundel. */
function shown(id) {
  return document.getElementById(id).dataset.overlayShown;
}

/** The live `aria-label` on one roundel. */
function label(id) {
  return document.getElementById(id).getAttribute('aria-label');
}

let mapStub;

beforeAll(async () => {
  localStorage.clear();
  buildFixture();
  Object.defineProperty(navigator, 'storage', {
    value: { estimate: async () => ({ quota: 1e10, usage: 0 }) },
    configurable: true,
  });
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) })),
  );

  mapStub = stubMapLibre();

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom, and the styledata handler this
  // suite drives directly is registered from inside it.
  for (const handler of mapStub.handlers.load || []) await handler();
  await import('../../static/js/map_roundel_overlay_state.js');
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

beforeEach(() => {
  // A known starting point for every case: nothing on the map.
  window.pwaDownloadedOverlay.hide();
  window.pwaFavouritesOverlay.hide();
  window.pwaCommunityReportsOverlay.hide();
});

describe('the three roundels track their own overlay', () => {
  it('paints the boot state, which is not simply "everything off"', () => {
    // overlayState.favourites defaults to ON, so a roundel painted from an
    // assumed-empty map would be wrong the moment the page settled. Re-seed
    // the preference and re-run the boot paint the module does at parse time.
    window.pwaFavouritesOverlay.show();
    window.pwaRoundelOverlayState.refresh();

    expect(shown(FAVOURITES_ROUNDEL)).toBe('true');
  });

  it('follows the downloads overlay in both directions', async () => {
    await window.pwaDownloadedOverlay.show();
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('true');

    window.pwaDownloadedOverlay.hide();
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('false');
  });

  it('follows the favourites overlay in both directions', () => {
    window.pwaFavouritesOverlay.show();
    expect(shown(FAVOURITES_ROUNDEL)).toBe('true');

    window.pwaFavouritesOverlay.hide();
    expect(shown(FAVOURITES_ROUNDEL)).toBe('false');
  });

  it('follows the community-reports overlay in both directions', () => {
    window.pwaCommunityReportsOverlay.show();
    expect(shown(REPORTS_ROUNDEL)).toBe('true');

    window.pwaCommunityReportsOverlay.hide();
    expect(shown(REPORTS_ROUNDEL)).toBe('false');
  });

  it('leaves the other two roundels alone', () => {
    window.pwaFavouritesOverlay.show();

    expect(shown(DOWNLOADS_ROUNDEL)).toBe('false');
    expect(shown(REPORTS_ROUNDEL)).toBe('false');
  });
});

describe('the state is announced, not only painted', () => {
  it('composes each roundel\'s own name into its aria-label, and restores it', () => {
    window.pwaFavouritesOverlay.show();
    expect(label(FAVOURITES_ROUNDEL)).toBe('Your favourites — shown on the map');

    window.pwaFavouritesOverlay.hide();
    expect(label(FAVOURITES_ROUNDEL)).toBe('Your favourites');
  });

  it('keeps a roundel\'s title in step with its label where it has one', async () => {
    await window.pwaDownloadedOverlay.show();
    const el = document.getElementById(DOWNLOADS_ROUNDEL);

    expect(el.title).toBe('Manage offline downloads — shown on the map');
    expect(el.getAttribute('aria-label')).toBe(el.title);
  });

  it('invents no title for a roundel that never had one', () => {
    window.pwaFavouritesOverlay.show();

    expect(document.getElementById(FAVOURITES_ROUNDEL).hasAttribute('title')).toBe(false);
  });
});

describe('changes from outside the panel', () => {
  it('follows the downloaded overlay off when Bulletins takes the map back', async () => {
    // SNOW-656: the two paint the same polygons, so turning Bulletins on
    // switches the squares off — from a control in another surface entirely,
    // with this roundel nowhere near the interaction.
    await window.pwaDownloadedOverlay.show();
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('true');

    document.dispatchEvent(
      new CustomEvent('snowdesk:bulletins-step', { detail: { step: 1 } }),
    );

    expect(window.pwaDownloadedOverlay.isVisible()).toBe(false);
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('false');
  });

  it('survives the basemap-swap re-seed', async () => {
    // The styledata handler rewrites overlayState wholesale from
    // localStorage. A boot-only implementation passes every other case in
    // this file and fails this one: the roundels would go on stating
    // whatever was true before the swap for the rest of the session.
    window.pwaFavouritesOverlay.show();
    window.pwaCommunityReportsOverlay.hide();
    expect(shown(FAVOURITES_ROUNDEL)).toBe('true');

    // Force the handler past its own guard (it early-returns while the
    // regions fill is still installed) and hand it the same DOM state a
    // real setStyle leaves behind.
    document.getElementById(FAVOURITES_ROUNDEL).dataset.overlayShown = 'false';
    mapStub.removeLayer('regions-fill');
    for (const handler of mapStub.handlers.styledata || []) await handler();

    await waitFor(() => shown(FAVOURITES_ROUNDEL) === 'true');
    expect(shown(FAVOURITES_ROUNDEL)).toBe('true');
    expect(shown(REPORTS_ROUNDEL)).toBe('false');
  });
});
