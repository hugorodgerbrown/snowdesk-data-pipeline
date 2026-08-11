/*
 * tests/js/test_map_country_paint_mid_playback.js — a country switched on
 * paints for the day on SCREEN, not for the day in the URL (SNOW-660).
 *
 * `ensureCountryLoaded` paints the newly-loaded country's regions for the
 * current day, so its regions colour immediately instead of waiting for the
 * next scrubber interaction. Which day that is has to be the same question
 * the rest of the map answers, and the URL alone cannot answer it: the
 * timelapse commits a date per frame and only syncs `?d=` where playback
 * settles, so mid-playback the committed date is real and the querystring is
 * still bare. Reading the URL raw there skips the paint entirely and leaves
 * the new country grey beside correctly-graded neighbours — a country the
 * user just switched on, showing as though it had no bulletins.
 *
 * So it goes through `repaintDateForStyleSwap`, the precedence the codebase
 * already uses for this question: committed date first, `?d=` behind it,
 * null meaning nothing has been chosen. Both ends are pinned below, because
 * the null end is this ticket's own rule — with no day asked for, a country
 * toggle must not invent one either.
 */

import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

// The day a timelapse frame has committed. Deliberately never written to the
// URL by this test — that is the state under test.
const FRAME_DATE = '2026-02-10';

const CH_REGION = 'CH-4115';
const FR_REGION = 'FR-38';

/** France's whole-season ratings, as `?country=fr` returns them. */
const FR_RATINGS = {
  '2026-02-09': { [FR_REGION]: 1 },
  [FRAME_DATE]: { [FR_REGION]: 4 },
};

/** The API emits the region identifier as `properties.id` (see map.js). */
const featureCollection = (ids) => ({
  type: 'FeatureCollection',
  features: ids.map((id) => ({
    type: 'Feature',
    properties: { id, name: id, covered: true },
    geometry: { type: 'Polygon', coordinates: [[[7, 46], [7, 47], [8, 47], [7, 46]]] },
  })),
});

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
         data-season-end="2026-04-30"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

/** Boot the bundle at a bare `/` and run the map's load handlers. */
async function bootBare() {
  window.history.replaceState({}, '', '/');
  buildFixture();
  const mapStub = stubMapLibre();
  vi.stubGlobal('fetch', vi.fn((input) => {
    const href = String(input);
    let body = {};
    if (href.includes('regions.geojson')) {
      body = featureCollection(href.includes('country=fr') ? [FR_REGION] : [CH_REGION]);
    } else if (href.includes('/api/ratings/') && href.includes('country=fr')) {
      body = FR_RATINGS;
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }));

  vi.resetModules();
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  await new Promise((r) => setTimeout(r, 200));
  return mapStub;
}

/** Switch France on, the way map_basemap_picker.js's row click does. */
async function toggleFranceOn() {
  document.dispatchEvent(new CustomEvent('snowdesk:country-toggle', {
    detail: { code: 'fr', next: true },
  }));
  await new Promise((r) => setTimeout(r, 200));
}

/** The rating painted onto a region, or 'no_rating' if none was written. */
function paintedRating(mapStub, regionID) {
  const feature = window.snowdeskMapState.featureByRegionId[regionID];
  if (!feature) return null;
  return mapStub.getFeatureState({ id: feature.id }).rating || 'no_rating';
}

afterAll(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
  window.history.replaceState({}, '', '/');
});

beforeEach(() => {
  localStorage.clear();
});

describe('a country switched on mid-playback', () => {
  it('paints it for the committed day, with the URL still bare', async () => {
    const mapStub = await bootBare();
    // A timelapse frame: the day is genuinely on screen, and `?d=` has not
    // caught up because playback only writes it where it settles.
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: FRAME_DATE, source: 'timelapse' },
    }));

    await toggleFranceOn();

    expect(location.search).toBe('');
    expect(paintedRating(mapStub, FR_REGION)).toBe('high');
  });

  it('paints the committed day, not some other day the payload carries', async () => {
    // The frame's own rating, not the first or last in the season payload —
    // a paint that reached for any available frame would pass the assertion
    // above by accident.
    const mapStub = await bootBare();
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: FRAME_DATE, source: 'timelapse' },
    }));

    await toggleFranceOn();

    expect(paintedRating(mapStub, FR_REGION)).not.toBe('low');
  });
});

describe('a country switched on with no day chosen', () => {
  it('paints nothing, rather than inventing a day of its own', async () => {
    const mapStub = await bootBare();

    await toggleFranceOn();

    expect(paintedRating(mapStub, FR_REGION)).toBe('no_rating');
  });
});
