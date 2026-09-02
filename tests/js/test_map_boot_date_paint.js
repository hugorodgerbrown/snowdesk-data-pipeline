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
 * SNOW-660 removed the boot day itself, and SNOW-793 gave it one back —
 * deliberately not the same one. The three cases below are that rule in
 * full:
 *
 *   - `?d=` is the day fetched and painted, and it still wins outright.
 *   - An empty querystring fetches and paints TODAY, read from the
 *     scrubber's server-rendered `data-today`. Never the season's last
 *     populated day, which is the date SNOW-660 was actually about: today
 *     is nameable and #map-date-ribbon names it, so the map opens coloured
 *     for a day that is both defaulted and stated.
 *   - With no readable `data-today` there is no day at all, and SNOW-660's
 *     uncoloured map is what remains. That is the fallback, not the norm.
 *
 * The middle case has now asserted three different things across two
 * tickets, which is the honest history of this surface — so each one says
 * which day it expects AND which days it must not touch.
 */

import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

// The season's last populated day — what map.js used to open on, and what
// nothing may open on now. Named for what it is rather than for the removed
// `bootDateKey` it used to seed.
const SEASON_END_DATE = '2026-04-30';
const DEEP_LINK_DATE = '2026-02-09';
// SNOW-793's default. Deliberately neither of the two above: a boot that
// reached for the season's last day, or that leaked the deep link across
// cases, has to look different from a boot that reached for today.
const TODAY_DATE = '2026-03-15';
const TODAY_PCT = 50;

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
  // Two regions, and both ratings differ from every other day's — so a
  // frame for the wrong day cannot pass for today's, and today's cannot
  // pass for anyone else's.
  [TODAY_DATE]: { 'CH-4115': 5, 'CH-4114': 2 },
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

/**
 * @param {{today?: string}} [opts] `today` omitted renders the scrubber
 *   with NO `data-today`, which is the no-day-knowable fallback.
 */
function buildFixture(opts = {}) {
  const today = 'today' in opts ? opts.today : TODAY_DATE;
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
    <ul id="search-results" hidden></ul>
    <div id="season-scrubber"
         ${today ? `data-today="${today}"` : ''}
         data-today-pct="${TODAY_PCT}"
         data-season-start="${DEEP_LINK_DATE}"
         data-season-end="${SEASON_END_DATE}"
         data-state="loading">
      <div class="season-scrubber-track">
        <div class="season-scrubber-thumb"></div>
      </div>
      <div class="season-scrubber-loading"></div>
    </div>`;
}

/**
 * Boot the bundle at `url`, run the map's load handlers, and return the
 * rating painted onto each region plus every URL fetched.
 *
 * The fetches matter as much as the paint: the ratings leg is skipped
 * entirely when no day is known, so "painted nothing" and "asked for
 * nothing" are two separate assertions, and a request for a day the visitor
 * never chose would be a bug even if its answer were dropped on the floor.
 * The same pairing proves the SNOW-793 default is a real fetch for today
 * rather than a stale frame relabelled.
 */
async function bootAt(url, fixtureOpts) {
  window.history.replaceState({}, '', url);
  buildFixture(fixtureOpts);
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

  it('paints today when no ?d= asks for a day', async () => {
    // SNOW-793. CH-4113 has no rating today and must stay grey: the default
    // is one specific day's frame, not a licence to colour the map in.
    const { painted, ratingsFetches } = await bootAt('/');

    expect(painted).toEqual({
      'CH-4115': 'very_high',
      'CH-4114': 'moderate',
      'CH-4113': 'no_rating',
    });
    // The day is FETCHED, not recovered from a season payload that happens
    // to be in hand — a frame lifted out of the full-season response would
    // paint identically here while skipping the cheap single-date leg the
    // cold path is budgeted for.
    expect(ratingsFetches).toContain(`/api/ratings/?d=${TODAY_DATE}&country=ch`);
    // And no other day is asked for. The season's last populated day is the
    // one SNOW-660 removed; seeing it here again would be that regression.
    expect(ratingsFetches.join(' ')).not.toContain(SEASON_END_DATE);
    expect(ratingsFetches.join(' ')).not.toContain(DEEP_LINK_DATE);
  });

  it('leaves the URL bare, so a shared link still means "today"', async () => {
    // SNOW-793 decision 2. The default is not a choice the visitor made, so
    // it is not written to the URL: a link copied off a defaulted map has to
    // keep meaning "today" when it is opened next week, rather than pinning
    // to the day it was copied. Visitor-driven commits still write `?d=` —
    // test_map_scrubber_no_boot_snap.js owns that half.
    await bootAt('/');

    expect(window.location.search).toBe('');
  });

  it('paints nothing when no day is knowable at all', async () => {
    // SNOW-660's state, now reached only where the scrubber is absent or
    // its `data-today` is unreadable. It is still the right answer there:
    // the alternative is the map inventing a day again, which is the whole
    // history of this file. #map-date-ribbon reads "No date selected" in
    // this state (test_map_ribbon_no_date.js).
    const { painted, ratingsFetches } = await bootAt('/', { today: null });

    expect(painted).toEqual({
      'CH-4115': 'no_rating',
      'CH-4114': 'no_rating',
      'CH-4113': 'no_rating',
    });
    expect(ratingsFetches.filter((href) => href.includes('d='))).toEqual([]);
  });
});
