/*
 * tests/js/test_map_groupings_guard.js — Vitest test for the guard on the
 * bulletin-groupings refetch in static/js/map.js.
 *
 * The bulletin boundary (L3) follows the **Bulletins** toggle: while it is
 * hidden, every scrubbed date that refetches
 * `/api/bulletin-groupings.geojson` is network cost for nothing on screen.
 * The `snowdesk:date-changed` handler guards on that.
 *
 * SNOW-656 changed WHICH toggle, and what "hidden" means. The boundary used
 * to follow Micro regions (L4) because one key drove the whole tier; it now
 * follows Bulletins, because the dissolved outline of the regions sharing a
 * bulletin is a bulletin concept and, like the choropleth and unlike the
 * micro-region geography, is date-bound. "Hidden" also covers a
 * SUPPRESSION — resort-edit mode takes the Bulletins layers off the map
 * without touching the user's step — and a boundary hidden for that reason
 * must not be refetched either.
 *
 * The downloaded-areas overlay was the other suppression until the two
 * layers stopped being exclusive. It is now a plain non-event for this
 * guard, which is worth a test of its own: an overlay that no longer hides
 * the boundary must no longer withhold its data.
 *
 * The 2026-08-03 JS review (finding M9) is why this file exists: the guard
 * then read `overlayState.l4`, an in-memory copy only a toggle-ON refreshed,
 * so after a toggle-OFF it stayed true for the session and the guard never
 * fired. It read localStorage for a while afterwards. It now reads the
 * Bulletins state machine, which every transition writes through — so the
 * toggle-OFF case below is still the assertion that matters.
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
import { loadMapBundle } from './_load_map_bundle.js';

const GROUPINGS_URL = '/api/bulletin-groupings.geojson';
const BULLETINS_STORAGE_KEY = 'snowdesk.map.overlay.bulletins';
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

/**
 * Set the bulletin-fill step the way the flyout control does.
 *
 * `next` is an opacity step: 0 is the off position, anything above it is on.
 */
function setBulletinsStep(next) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:bulletins-step', { detail: { step: next } }),
  );
}

beforeAll(async () => {
  buildFixture();
  stubMapLibre();
  // Bulletins starts at its default step, as it does for a user who has never
  // touched the control.
  localStorage.setItem(BULLETINS_STORAGE_KEY, '0.5');
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
  loadMapBundle();
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
  it('does not fetch once the fill control is set to the off step', async () => {
    setBulletinsStep(0);

    await commitDate('2026-05-20');

    expect(groupingsFetches()).toEqual([]);
  });

  it('still fetches at any step above off', async () => {
    setBulletinsStep(0.5);
    globalThis.fetch.mockClear();

    await commitDate('2026-05-19');

    expect(groupingsFetches()).toEqual([`${GROUPINGS_URL}?d=2026-05-19`]);
  });

  it('keeps fetching while the downloaded-areas overlay is on', async () => {
    // The squares no longer take the boundary off the map — they are a
    // hatch drawn over it — so the data behind it is still worth having.
    setBulletinsStep(0.5);
    await window.pwaDownloadedOverlay.show();
    globalThis.fetch.mockClear();

    await commitDate('2026-05-18');

    expect(groupingsFetches()).toEqual([`${GROUPINGS_URL}?d=2026-05-18`]);
  });

  it('does not fetch while a suppression is holding the boundary off the map', async () => {
    // Bulletins' own preference is still on here — this is the
    // suppression, not a toggle. A boundary the user cannot see because
    // resort-edit mode has the map is no more worth fetching than one they
    // switched off.
    window.pwaDownloadedOverlay.hide();
    setBulletinsStep(0.5);
    window.pwaBulletinsLayer.setSuppressed('edit-resorts', true);
    globalThis.fetch.mockClear();

    await commitDate('2026-05-17');

    expect(groupingsFetches()).toEqual([]);
  });

  it('fetches again once the suppression lifts and the preference returns', async () => {
    window.pwaBulletinsLayer.setSuppressed('edit-resorts', false);
    globalThis.fetch.mockClear();

    await commitDate('2026-05-16');

    expect(groupingsFetches()).toEqual([`${GROUPINGS_URL}?d=2026-05-16`]);
  });
});
