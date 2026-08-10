/*
 * tests/js/test_map_boot_date_paint.js — the boot ratings paint must not
 * overwrite a `?d=` deep link's own day (SNOW-656).
 *
 * map.js's boot fetches ratings for `bootDateKey` — min(today, seasonEnd) —
 * and paints them once the regions source emits 'data'. On a `?d=` deep link
 * that lands asynchronously, typically AFTER the scrubber has already
 * repainted the map for the requested date.
 *
 * The paint runs with `clearMissing: false`, so it does not blank the map;
 * it silently overwrites exactly those regions the BOOT day has a rating
 * for, leaving them showing the wrong day's colour beside correct
 * neighbours. Nothing on screen explains the discrepancy, and the regions
 * affected are whichever ones the boot day happens to cover — which is why
 * it read as "one region is the wrong colour" against a seeded dev database
 * whose last populated day carries a single region, and would repaint most
 * of the map against a full season.
 *
 * The guard is one line in `paintTodayRatings`; this file is here because
 * the failure is invisible without a per-region comparison against the
 * frame, and nothing else in the suite makes one.
 */

import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const BOOT_DATE = '2026-04-30';
const DEEP_LINK_DATE = '2026-02-09';

/** The boot day covers ONE region; the deep-linked day covers three. */
const RATINGS = {
  [BOOT_DATE]: { 'CH-4115': 1 },
  [DEEP_LINK_DATE]: { 'CH-4115': 3, 'CH-4114': 4, 'CH-4113': 2 },
};

// The API emits the region identifier as `properties.id`; map.js normalises
// it to `properties.regionID` on the way in (see its own comment there), so
// the fixture has to speak the API's shape, not the normalised one.
const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: ['CH-4115', 'CH-4114', 'CH-4113'].map((id) => ({
    type: 'Feature',
    properties: { id, name: id, covered: true },
    geometry: { type: 'Polygon', coordinates: [[[7, 46], [7, 47], [8, 47], [7, 46]]] },
  })),
};

/** Minimal MapLibre stub that records feature-state writes. */
function stubMapLibre() {
  const handlers = {};
  const featureState = new Map();
  const layers = new Set();
  const map = {
    on: (ev, a, b) => { (handlers[ev] ||= []).push(typeof a === 'function' ? a : b); },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => 1,
    isSourceLoaded: () => true,
    getSource: (id) => (id === 'regions' ? { setData: () => {} } : null),
    addSource: () => {},
    addLayer: (def) => { layers.add(def.id); },
    removeLayer: (id) => { layers.delete(id); },
    removeSource: () => {},
    setLayoutProperty: () => {},
    setPaintProperty: () => {},
    setFilter: () => {},
    setFeatureState: (target, state) => {
      featureState.set(target.id, { ...(featureState.get(target.id) || {}), ...state });
    },
    getFeatureState: (target) => featureState.get(target.id) || {},
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

function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings/"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="${BOOT_DATE}"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/**
 * Boot the bundle at `url`, run the map's load handlers, and return the
 * rating painted onto each region.
 */
async function bootAt(url) {
  window.history.replaceState({}, '', url);
  buildFixture();
  const mapStub = stubMapLibre();
  vi.stubGlobal('fetch', vi.fn((input) => {
    const href = String(input);
    let body = {};
    if (href.includes('regions.geojson')) body = REGIONS_GEOJSON;
    else if (href.includes('/api/ratings/')) {
      // The endpoint answers with just the requested day when `?d=` is set,
      // and the whole season otherwise — the shape map.js and the scrubber
      // each rely on.
      const match = href.match(/[?&]d=(\d{4}-\d{2}-\d{2})/);
      body = match ? { [match[1]]: RATINGS[match[1]] || {} } : RATINGS;
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }));

  vi.resetModules();
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  // Let the boot fetches, the scrubber's own repaint, and the deferred
  // 'sourcedata' paint all settle.
  await new Promise((r) => setTimeout(r, 400));

  const byRegion = window.snowdeskMapState.featureByRegionId;
  const painted = {};
  for (const [regionID, feature] of Object.entries(byRegion)) {
    painted[regionID] = mapStub.getFeatureState({ id: feature.id }).rating || 'no_rating';
  }
  return painted;
}

afterAll(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
  window.history.replaceState({}, '', '/');
});

beforeEach(() => {
  localStorage.clear();
});

describe('the boot ratings paint vs a ?d= deep link', () => {
  it('stands down entirely when a ?d= owns the date', async () => {
    // The assertion is the absence of the boot day's colour, not the
    // presence of the deep-linked one: repainting for `?d=` is the
    // scrubber's job (`repaintRegionsForDate`, covered by its own tests) and
    // the scrubber is not mounted in this fixture. What is being pinned here
    // is that the boot frame no longer arrives late and overwrites it.
    //
    // CH-4115 is the one region the boot day covers, so it is the one the
    // unguarded paint clobbered — 'low' instead of the requested day's
    // 'considerable'. Its neighbours were never at risk, which is exactly
    // what made the bug look region-specific rather than date-specific.
    const painted = await bootAt(`/?d=${DEEP_LINK_DATE}`);

    expect(painted['CH-4115']).not.toBe('low');
    expect(painted).toEqual({
      'CH-4115': 'no_rating',
      'CH-4114': 'no_rating',
      'CH-4113': 'no_rating',
    });
  });

  it('still paints the boot day when there is no ?d= to supersede it', async () => {
    // The guard must not disable the boot paint outright — without a deep
    // link it is the only thing that colours the map on first load.
    const painted = await bootAt('/');

    expect(painted['CH-4115']).toBe('low');
  });
});
