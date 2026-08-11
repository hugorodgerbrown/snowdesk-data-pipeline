/*
 * tests/js/test_map_boot_date_paint.js — the boot ratings paint covers the
 * day the URL asked for, and only that day (SNOW-656, SNOW-660).
 *
 * map.js's boot fetches ratings once and paints them when the regions source
 * emits 'data'. SNOW-656 fetched them for `bootDateKey` — min(today,
 * seasonEnd) — so on a `?d=` deep link the frame landed late and silently
 * overwrote exactly those regions the BOOT day had a rating for, leaving
 * them showing the wrong day's colour beside correct neighbours. The guard
 * that fixed it was one line; this file exists because the failure is
 * invisible without a per-region comparison against the frame, and nothing
 * else in the suite makes one.
 *
 * SNOW-660 removed the boot day itself. There is no date but the one the
 * URL asked for, so the two cases below are the two halves of that rule:
 * `?d=` is the day fetched and painted, and an empty querystring paints
 * nothing at all rather than a day the map picked for the visitor. The
 * second case is the one this ticket changed — it used to assert the
 * opposite, which is what "the map opens showing Martigny-Verbier coloured
 * for a day nobody asked for" looked like from here.
 */

import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

// The season's last populated day — what map.js used to open on, and what
// nothing may open on now. Named for what it is rather than for the removed
// `bootDateKey` it used to seed.
const SEASON_END_DATE = '2026-04-30';
const DEEP_LINK_DATE = '2026-02-09';

/**
 * The season's last day covers ONE region; the deep-linked day covers three.
 *
 * The asymmetry is the point: one rated region is exactly the shape of the
 * off-season map SNOW-660 was reported against, so a paint that leaks
 * through shows up here as a single coloured polygon.
 */
const RATINGS = {
  [SEASON_END_DATE]: { 'CH-4115': 1 },
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
         data-season-end="${SEASON_END_DATE}"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/**
 * Boot the bundle at `url`, run the map's load handlers, and return the
 * rating painted onto each region plus every URL fetched.
 *
 * The fetches matter as much as the paint: SNOW-660 skips the ratings leg
 * entirely when no day was asked for, so "painted nothing" and "asked for
 * nothing" are two separate assertions, and a request for a day the visitor
 * never chose would be a bug even if its answer were dropped on the floor.
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
  const ratingsFetches = globalThis.fetch.mock.calls
    .map((call) => String(call[0]))
    .filter((href) => href.includes('/api/ratings/'));
  return { painted, ratingsFetches };
}

afterAll(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
  window.history.replaceState({}, '', '/');
});

beforeEach(() => {
  localStorage.clear();
});

describe('the boot ratings paint', () => {
  it('paints the day a ?d= deep link asked for, and no other', async () => {
    // CH-4115 is the one region the season's last day covers, so it is the
    // one a boot frame fetched for that day used to clobber — 'low' instead
    // of the requested day's 'considerable'. Its neighbours were never at
    // risk, which is exactly what made the bug look region-specific rather
    // than date-specific. Since SNOW-660 the boot leg fetches the requested
    // day itself, so all three carry that day's colours.
    const { painted, ratingsFetches } = await bootAt(`/?d=${DEEP_LINK_DATE}`);

    expect(painted).toEqual({
      'CH-4115': 'considerable',
      'CH-4114': 'high',
      'CH-4113': 'moderate',
    });
    expect(painted['CH-4115']).not.toBe('low');
    expect(ratingsFetches).toContain(`/api/ratings/?d=${DEEP_LINK_DATE}&country=ch`);
    expect(ratingsFetches.join(' ')).not.toContain(SEASON_END_DATE);
  });

  it('paints nothing when no ?d= asks for a day', async () => {
    // SNOW-660. This case used to assert the opposite — that the map paints
    // the season's last populated day on a cold boot — and that assertion
    // WAS the bug: one coloured polygon, off season, standing for a day the
    // visitor never chose and which nothing on screen named.
    //
    // Both halves are asserted because either alone can pass while the
    // behaviour is wrong: a paint with no fetch would mean a stale frame
    // leaked through, and a fetch with no paint would still cost the request
    // and leave the answer one line away from being painted.
    const { painted, ratingsFetches } = await bootAt('/');

    expect(painted).toEqual({
      'CH-4115': 'no_rating',
      'CH-4114': 'no_rating',
      'CH-4113': 'no_rating',
    });
    expect(ratingsFetches.filter((href) => href.includes('d='))).toEqual([]);
  });
});
