/*
 * tests/js/test_map_roundel_overlay_state.js — Vitest DOM test for
 * static/js/map_roundel_overlay_state.js (SNOW-658).
 *
 * The overlay roundels — downloads, field observations and, since SNOW-687,
 * routes — each carry ``data-overlay-shown`` (the favourites star did too
 * until SNOW-1481 made it open Saved; the routes roundel is the probe the
 * star used to be), meaning one
 * thing on all of them: "the overlay this roundel's panel switches is drawn
 * on the map right now". Before SNOW-658 only one of them carried a state at
 * all, and it meant something else ("this device holds a downloaded area").
 *
 * SNOW-687 is the first roundel added since, and the case that shows the
 * shape held: it joined by adding one line to that module's ROUNDELS array
 * — no new event, no new CSS, and no edit to any assertion below.
 *
 * What is worth testing here is not the attribute write — it is that the
 * state stays TRUE over time. Five paths can change an overlay's
 * visibility, and only the first is obvious:
 *
 *   1. the panel switch, through the bridge (both directions, all three);
 *   2. the bulletins-exclusivity path, which switches the downloaded
 *      squares off from a control in a different surface entirely;
 *   3. the ``styledata`` re-install after a basemap swap, which tears every
 *      layer off the map and puts it back — the path an implementation that
 *      painted only at boot would still pass every other test here;
 *   4. a lazy overlay's own load settling, long after the switch that asked
 *      for it: the layers appear (state on) or never do (state stays off);
 *   5. a placement flow, which clears every app layer off the map without
 *      going anywhere near a bridge.
 *
 * 4 and 5 are the SNOW-658 review's subject: ``isVisible()`` answers from
 * the layers MapLibre is drawing, not from the stored preference, so the
 * state can only ever claim what is on screen. The preference lives on as
 * ``isEnabled()``, which is what the panel switch reads — the two are
 * asserted apart below, because the state where they disagree (asked for,
 * not drawn) is the state this distinction exists to make visible.
 *
 * The state used to drive a blue on-map ring as well (SNOW-658). That ring
 * was dropped by design with the two-capsule control column — readers took
 * it for "selected" — so what is asserted here is the attribute and the
 * composed aria-label, which the module still paints. "Lit" and "on" below
 * mean ``data-overlay-shown="true"``.
 *
 * Booting map.js in jsdom follows test_map_favourites_overlay_bridge.js's
 * pattern — see its header, and test_map_download_bytes.js's, for the
 * general rationale.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { MAP_BUNDLE, loadMapBundle } from './_load_map_bundle.js';

const DOWNLOADS_ROUNDEL = 'map-custom-download-control';
// SNOW-1481: the star opens Saved, four lists and no one layer, so it
// carries no overlay state; it is in the fixture to prove that.
const STAR_ROUNDEL = 'favourite-add-btn';
const REPORTS_ROUNDEL = 'report-btn';
const ROUTES_ROUNDEL = 'route-add-btn';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/**
 * Minimal MapLibre stub — layer layout plus enough of the style graph for
 * static/js/map_placement_focus.js, which decides what an "app overlay" is
 * by looking for layers hanging off a geojson source.
 */
function stubMapLibre() {
  const handlers = {};
  const layouts = new Map();
  // Layer id → its addLayer definition, and source id → its type, so
  // getStyle() can report what has actually been installed.
  const layerDefs = new Map();
  const sourceTypes = new Map();
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
    // Always null: map.js's install functions early-return on an existing
    // source, and this suite wants each install call to run (that is how a
    // freshly-fetched overlay gets back onto the map here).
    getSource: () => null,
    addSource: (id, def) => {
      sourceTypes.set(id, (def && def.type) || 'geojson');
    },
    addLayer: (def) => {
      layouts.set(def.id, { ...(def.layout || {}) });
      layerDefs.set(def.id, def);
    },
    removeLayer: (id) => {
      layouts.delete(id);
      layerDefs.delete(id);
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
    getStyle: () => ({
      layers: Array.from(layerDefs.values()),
      sources: Object.fromEntries(
        Array.from(sourceTypes.entries()).map(([id, type]) => [id, { type }]),
      ),
    }),
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
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <button id="${DOWNLOADS_ROUNDEL}"
            data-overlay-shown="false"
            aria-label="Manage downloads"
            title="Manage downloads"></button>
    <button id="${STAR_ROUNDEL}"
            aria-label="Saved"></button>
    <button id="${REPORTS_ROUNDEL}"
            data-overlay-shown="false"
            aria-label="Your field observations"></button>
    <button id="${ROUTES_ROUNDEL}"
            data-overlay-shown="false"
            aria-label="Your routes"></button>
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

/** Let every pending microtask (and the MutationObserver queue) drain. */
async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0));
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
  // Community reports are on by default (SNOW-1290); this suite boots with
  // them switched off so its first enable still reaches the network.
  localStorage.setItem('snowdesk.map.overlay.community_reports', 'false');
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
  // static/js/map_placement_focus.js rides along with the bundle: it reads
  // the bare ``MAP`` binding map_state.js declares, which in the browser it
  // reaches through the shared classic-script scope home.html gives it. Its
  // own <script> tag is outside the bundle's contiguous run, so it is passed
  // here rather than added to MAP_BUNDLE (which mirrors that run exactly,
  // and is checked against the template by tests/map/test_map_script_order.py).
  loadMapBundle(MAP_BUNDLE.concat(['map_placement_focus.js']));
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

/** Take one overlay's layers off the map, as a load that never landed
 * leaves them. */
function uninstallLayers(...layerIds) {
  for (const id of layerIds) mapStub.removeLayer(id);
}

/** Put the routes back the way map.js does after a basemap swap: its
 * styledata handler re-installs every overlay that is switched on. A
 * routes write only refreshes the data of layers already there. */
async function installRoutesLayers() {
  if (mapStub.getLayer('regions-fill')) mapStub.removeLayer('regions-fill');
  for (const handler of mapStub.handlers.styledata || []) await handler();
  await waitFor(() => !!mapStub.getLayer('routes-line'));
}

beforeEach(async () => {
  // A known starting point for every case: nothing on the map. The
  // routes re-install covers a case below that deliberately takes those
  // layers away.
  if (!mapStub.getLayer('routes-line')) {
    window.pwaRoutesOverlay.show();
    await installRoutesLayers();
  }
  window.pwaDownloadedOverlay.hide();
  window.pwaFavouritesOverlay.hide();
  window.pwaCommunityReportsOverlay.hide();
  window.pwaRoutesOverlay.hide();
});

// FIRST in the file on purpose: the suite boots with community reports
// switched off, so this is the only point at which nothing has fetched them yet and an
// enable genuinely reaches the network. map.js loads each lazy overlay once
// per session, so a later test could only ever exercise the short-circuit.
describe('an overlay whose data never arrives', () => {
  it('leaves the state off when the fetch fails', async () => {
    // Offline, first enable, no cached copy: the preference is written and
    // nothing at all is installed. The user needs to see that this did not
    // reach the map, which a state reading the preference would hide.
    globalThis.fetch.mockImplementationOnce(() => Promise.reject(new Error('offline')));

    window.pwaCommunityReportsOverlay.show();
    await settle();

    expect(window.pwaCommunityReportsOverlay.isEnabled()).toBe(true);
    expect(window.pwaCommunityReportsOverlay.isVisible()).toBe(false);
    expect(shown(REPORTS_ROUNDEL)).toBe('false');

    // The retry that succeeds does light it, from the same switch — so the
    // "off" above was the missing layers, not a flag stuck on the failure.
    window.pwaCommunityReportsOverlay.show();
    await waitFor(() => shown(REPORTS_ROUNDEL) === 'true');
    expect(shown(REPORTS_ROUNDEL)).toBe('true');
  });
});

describe('each roundel tracks its own overlay', () => {
  it('paints from the layers, not from the stored preference', async () => {
    // The distinction the SNOW-658 review turns on: the preference below
    // never moves, and the state still goes off, because the pins did.
    window.pwaRoutesOverlay.show();
    await waitFor(() => shown(ROUTES_ROUNDEL) === 'true');

    mapStub.setLayoutProperty('routes-line', 'visibility', 'none');
    window.pwaRoundelOverlayState.refresh();

    expect(window.pwaRoutesOverlay.isEnabled()).toBe(true);
    expect(shown(ROUTES_ROUNDEL)).toBe('false');
  });

  it('follows the downloads overlay in both directions', async () => {
    await window.pwaDownloadedOverlay.show();
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('true');

    window.pwaDownloadedOverlay.hide();
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('false');
  });

  it('follows the community-reports overlay in both directions', async () => {
    window.pwaCommunityReportsOverlay.show();
    await waitFor(() => shown(REPORTS_ROUNDEL) === 'true');
    expect(shown(REPORTS_ROUNDEL)).toBe('true');

    window.pwaCommunityReportsOverlay.hide();
    expect(shown(REPORTS_ROUNDEL)).toBe('false');
  });

  it('follows the routes overlay in both directions', async () => {
    // SNOW-687's roundel. Asserted the same way as the other three and for
    // the same reason: the state lights on the announcement its lazy load
    // makes, not on the switch, so a bridge that forgot to announce would
    // leave the state dark over a drawn route.
    window.pwaRoutesOverlay.show();
    await waitFor(() => shown(ROUTES_ROUNDEL) === 'true');
    expect(shown(ROUTES_ROUNDEL)).toBe('true');

    window.pwaRoutesOverlay.hide();
    expect(shown(ROUTES_ROUNDEL)).toBe('false');
  });

  it('leaves the other roundels alone', async () => {
    window.pwaRoutesOverlay.show();
    await waitFor(() => shown(ROUTES_ROUNDEL) === 'true');

    expect(shown(DOWNLOADS_ROUNDEL)).toBe('false');
    expect(shown(REPORTS_ROUNDEL)).toBe('false');
  });

  it('leaves the star alone, which opens Saved rather than one layer (SNOW-1481)', async () => {
    window.pwaFavouritesOverlay.show();
    await settle();
    window.pwaRoundelOverlayState.refresh();

    expect(document.getElementById(STAR_ROUNDEL).hasAttribute('data-overlay-shown')).toBe(false);
    expect(label(STAR_ROUNDEL)).toBe('Saved');
    window.pwaFavouritesOverlay.hide();
  });
});

describe('an overlay that was asked for but never drawn', () => {
  it('reports not-shown while its layers are not installed', async () => {
    // First enable offline: the preference is written and the fetch brings
    // back nothing, so there is no layer to paint. A state reading the
    // preference would claim "shown" over a map with no pins on it.
    uninstallLayers('routes-line', 'routes-line-casing');

    window.pwaRoutesOverlay.show();
    await settle();

    expect(window.pwaRoutesOverlay.isEnabled()).toBe(true);
    expect(window.pwaRoutesOverlay.isVisible()).toBe(false);
    expect(shown(ROUTES_ROUNDEL)).toBe('false');
  });

  it('lights the state when the layers finally install', async () => {
    uninstallLayers('routes-line', 'routes-line-casing');
    window.pwaRoutesOverlay.show();
    await settle();
    expect(shown(ROUTES_ROUNDEL)).toBe('false');

    // The data lands later — map.js installs the pins, and the install is
    // what has to speak up, since nothing about the preference has changed.
    await installRoutesLayers();

    expect(shown(ROUTES_ROUNDEL)).toBe('true');
  });

});

describe('a placement flow clears the map', () => {
  it('drops the states for its duration and gives them back', async () => {
    // static/js/map_placement_focus.js hides every geojson-sourced layer
    // without touching a bridge — the overlays genuinely are gone, so the
    // states have to go with them, and come back when the flow ends.
    window.pwaRoutesOverlay.show();
    await window.pwaDownloadedOverlay.show();
    await waitFor(() => shown(ROUTES_ROUNDEL) === 'true');

    window.PlacementFocus.enter();

    expect(shown(ROUTES_ROUNDEL)).toBe('false');
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('false');
    // The user's own settings are untouched — this is the map being
    // cleared, not their overlays being switched off.
    expect(window.pwaRoutesOverlay.isEnabled()).toBe(true);
    expect(window.pwaDownloadedOverlay.isEnabled()).toBe(true);

    window.PlacementFocus.exit();

    expect(shown(ROUTES_ROUNDEL)).toBe('true');
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('true');
  });

  it('repaints once per transition, so the states do not flicker', async () => {
    // A state that flickered — off, on, off — through every placement flow
    // would be worse than one that lied, so count the repaints rather than
    // reading the value at the end. Two announcements, one per transition,
    // each already carrying the settled value (this module's own listener
    // is registered first, so it has painted by the time this one runs).
    window.pwaRoutesOverlay.show();
    await waitFor(() => shown(ROUTES_ROUNDEL) === 'true');

    const seen = [];
    const record = () => seen.push(shown(ROUTES_ROUNDEL));
    document.addEventListener('snowdesk:overlay-visibility-changed', record);

    window.PlacementFocus.enter();
    window.PlacementFocus.exit();
    await settle();
    document.removeEventListener('snowdesk:overlay-visibility-changed', record);

    expect(seen).toEqual(['false', 'true']);
  });
});

describe('the state is announced, not only painted', () => {
  it('composes each roundel\'s own name into its aria-label, and restores it', async () => {
    window.pwaRoutesOverlay.show();
    await waitFor(() => shown(ROUTES_ROUNDEL) === 'true');
    expect(label(ROUTES_ROUNDEL)).toBe('Your routes — shown on the map');

    window.pwaRoutesOverlay.hide();
    expect(label(ROUTES_ROUNDEL)).toBe('Your routes');
  });

  it('keeps a roundel\'s title in step with its label where it has one', async () => {
    await window.pwaDownloadedOverlay.show();
    const el = document.getElementById(DOWNLOADS_ROUNDEL);

    expect(el.title).toBe('Manage downloads — shown on the map');
    expect(el.getAttribute('aria-label')).toBe(el.title);
  });

  it('invents no title for a roundel that never had one', () => {
    window.pwaRoutesOverlay.show();

    expect(document.getElementById(ROUTES_ROUNDEL).hasAttribute('title')).toBe(false);
  });
});

describe('changes from outside the panel', () => {
  it('stays lit when Bulletins takes a step — the two share the map now', async () => {
    // SNOW-656 had these two paint the same polygons as competing tints, so
    // raising the fill step switched the squares off and this roundel with
    // them. The squares are a hatch the choropleth reads through now, and
    // the roundel has to report what is actually drawn: dimming it here
    // would tell the user their downloads had gone when they had not.
    await window.pwaDownloadedOverlay.show();
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('true');

    document.dispatchEvent(
      new CustomEvent('snowdesk:bulletins-step', { detail: { step: 1 } }),
    );

    expect(window.pwaDownloadedOverlay.isVisible()).toBe(true);
    expect(shown(DOWNLOADS_ROUNDEL)).toBe('true');
  });

  it('survives the basemap-swap re-install', async () => {
    // setStyle tears every layer off the map and the styledata handler puts
    // them back. A boot-only implementation passes every other case in this
    // file and fails this one: the roundels would go on stating whatever was
    // true before the swap for the rest of the session. It also pins the
    // ORDER — announcing beside that handler's overlayState re-seed, before
    // the re-installs, would read an empty style and report "not shown".
    window.pwaRoutesOverlay.show();
    window.pwaCommunityReportsOverlay.hide();
    await waitFor(() => shown(ROUTES_ROUNDEL) === 'true');

    // Force the handler past its own guard (it early-returns while the
    // regions fill is still installed) and hand it the same DOM state a
    // real setStyle leaves behind.
    document.getElementById(ROUTES_ROUNDEL).dataset.overlayShown = 'false';
    mapStub.removeLayer('regions-fill');
    uninstallLayers('routes-line', 'routes-line-casing');
    for (const handler of mapStub.handlers.styledata || []) await handler();

    await waitFor(() => shown(ROUTES_ROUNDEL) === 'true');
    expect(shown(ROUTES_ROUNDEL)).toBe('true');
    expect(shown(REPORTS_ROUNDEL)).toBe('false');
  });
});
