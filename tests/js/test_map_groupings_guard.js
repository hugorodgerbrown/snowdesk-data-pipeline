/*
 * tests/js/test_map_groupings_guard.js — Vitest test for the L4 guard on
 * the bulletin-groupings refetch in static/js/map.js.
 *
 * The bulletin boundary (L3) follows the Micro-regions toggle (L4): while
 * L4 is off the boundary is hidden, so every scrubbed date that refetches
 * `/api/bulletin-groupings.geojson` is network cost for nothing on screen.
 * The `snowdesk:date-changed` handler guards on that — but the 2026-08-03
 * JS review (finding M9) found the guard reading `overlayState.l4`, an
 * in-memory copy only a toggle-ON refreshes, so after a toggle-OFF it stayed
 * true for the session and the guard never fired. The picker's live source
 * of truth is localStorage, which is what the guard now reads.
 *
 * Booting map.js in jsdom: the file is one script of top-level IIFEs, so
 * importing it runs the lot; the main IIFE needs a `#map` element and a
 * `maplibregl` global. MapLibre's `load` event never fires here, which is
 * fine for this path — the module registers the date-changed listener at
 * IIFE scope for that reason (SNOW-47), and the L3 overlay is loaded
 * through the same `snowdesk:overlay-load` event the picker dispatches.
 *
 * Both cases are asserted: the guard firing (no fetch) and not firing (a
 * fetch), so a guard that simply returned early always would not pass.
 */

import { beforeAll, afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const GROUPINGS_URL = '/api/bulletin-groupings.geojson';
const L4_STORAGE_KEY = 'snowdesk.map.overlay.l4';
// GROUPINGS_SETTLE_MS is 250 in map.js — the debounce a scrubbed date waits
// out before its fetch.
const PAST_SETTLE_MS = 320;

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** Minimal MapLibre stub — every method map.js touches on this path. */
function stubMapLibre() {
  const handlers = {};
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    getLayer: () => null,
    getFilter: () => null,
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
    setStyle: () => {},
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [], sources: {} }),
    getCanvas: () => ({ style: {} }),
    getContainer: () => document.getElementById('map'),
    triggerRepaint: () => {},
    fitBounds: () => {},
    easeTo: () => {},
    getZoom: () => 8,
    getCenter: () => ({ lng: 8, lat: 46.5 }),
    getBounds: () => ({
      getWest: () => 5,
      getSouth: () => 45,
      getEast: () => 10,
      getNorth: () => 48,
    }),
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
      return {
        setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }),
        remove: () => {},
      };
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

function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-bulletin-groupings-url="${GROUPINGS_URL}"
         data-default-basemap-key="standard"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
}

function tick(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Every groupings URL fetched since the last mock reset. */
function groupingsFetches() {
  return globalThis.fetch.mock.calls
    .map((call) => String(call[0]))
    .filter((url) => url.includes('bulletin-groupings'));
}

/** Commit `dateKey` the way the scrubber does, then wait out the settle debounce. */
async function commitDate(dateKey) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:date-changed', { detail: { date: dateKey } }),
  );
  await tick(PAST_SETTLE_MS);
}

beforeAll(async () => {
  buildFixture();
  stubMapLibre();
  // L4 starts on, as it does for a user who has never touched the picker.
  localStorage.setItem(L4_STORAGE_KEY, 'true');
  vi.stubGlobal(
    'fetch',
    vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve(EMPTY_FC) }),
    ),
  );

  vi.resetModules();
  // SNOW-618: map.js's search box delegates to this; home.html loads it
  // immediately before map.js, and map.js does not guard for its absence.
  await import('../../static/js/search_core.js');
  // SNOW-623: map.js's choropleth paint delegates to this.
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/map.js');
  // The picker's own bridge for "this tier was enabled and isn't loaded
  // yet" — the only route to overlayLoaded.l3, which the guard also reads.
  document.dispatchEvent(
    new CustomEvent('snowdesk:overlay-load', { detail: { key: 'l3' } }),
  );
  await tick(50);
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

beforeEach(() => {
  globalThis.fetch.mockClear();
});

describe('bulletin-groupings refetch on a scrubbed date', () => {
  it('does not fetch once the picker has toggled L4 off', async () => {
    // What the picker writes on a toggle-off. It updates no in-memory
    // state — that omission is the bug this guards against.
    localStorage.setItem(L4_STORAGE_KEY, 'false');

    await commitDate('2026-05-20');

    expect(groupingsFetches()).toEqual([]);
  });

  it('still fetches while L4 is on', async () => {
    localStorage.setItem(L4_STORAGE_KEY, 'true');

    await commitDate('2026-05-19');

    expect(groupingsFetches()).toEqual([`${GROUPINGS_URL}?d=2026-05-19`]);
  });
});
