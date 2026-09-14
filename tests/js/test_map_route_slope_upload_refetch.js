/*
 * tests/js/test_map_route_slope_upload_refetch.js — the one delayed re-read
 * that lets an uploaded route become coloured without a page reload
 * (SNOW-910).
 *
 * Scenario: none — a timer scheduled off an event payload, asserted by
 * counting fetches. No browser is needed to prove it, and no manual test
 * script could observe it.
 *
 * In production terrain sampling is a QUEUED task: `create_route` returns
 * while `slope_samples` is still null, the upload's own refresh reads that
 * record, and the flat line is drawn. The worker's later save reaches no
 * client, so the route stays uncoloured until a full page reload — on the
 * one path every new user takes first. One delayed re-read fixes that, and
 * three things about it have to hold or the cure is worse:
 *
 *   1. it fires ONCE. A retry ladder would turn a worker that is merely
 *      busy into a stream of requests from every device that uploaded;
 *   2. it is armed by the writes that PUT A ROUTE ON THE SERVER — an
 *      upload and a claim — and by neither of the two that do not. A
 *      legacy route the backfill never reached is unsampled on every
 *      load, and arming off a rename or a delete would cost a pointless
 *      refetch on every visit for the rest of its life;
 *   3. it is inert if there is nothing left to paint — twenty seconds is
 *      long enough for the overlay to have been torn down.
 *
 * Booting map.js in jsdom follows tests/js/test_map_panel_overlay_refresh.js's
 * pattern; see its header for the general rationale.
 */

import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/** A route the server has not sampled yet — no `slope` property at all. */
const ROUTES_UNSAMPLED = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [[7.5, 46.1, 1500], [7.54, 46.14, 2100]],
      },
      properties: {
        uuid: 'r-1',
        name: 'Rosablanche',
        bounds: [7.5, 46.1, 7.54, 46.14],
      },
    },
  ],
};

/** The same route once the worker has stored its record. */
const ROUTES_SAMPLED = {
  type: 'FeatureCollection',
  features: [
    {
      ...ROUTES_UNSAMPLED.features[0],
      properties: {
        ...ROUTES_UNSAMPLED.features[0].properties,
        slope: {
          points: [[7.5, 46.1], [7.52, 46.12], [7.54, 46.14]],
          angles: [22.0, 38.0],
        },
      },
    },
  ],
};

/** What the routes feed answers next. Swapped between tests. */
let routesPayload = ROUTES_UNSAMPLED;

/** MapLibre stub — only what map.js's boot and the routes layer touch. */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const sources = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: (id) => (layers.has(id) ? { id } : null),
    getFilter: () => null,
    getLayoutProperty: (id, prop) => ((layers.get(id) || {}).layout || {})[prop],
    getPaintProperty: () => undefined,
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: (id) => sources.get(id) || null,
    addSource: (id, def) => { sources.set(id, { ...def, setData: vi.fn() }); },
    addLayer: (def) => { layers.set(def.id, def); },
    removeLayer: (id) => layers.delete(id),
    removeSource: (id) => sources.delete(id),
    moveLayer: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layer = layers.get(id);
      if (layer) (layer.layout ||= {})[prop] = value;
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
    getCenter: () => ({ lng: 7, lat: 46 }),
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 7, lat: 46 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
    layers,
    sources,
  };
  globalThis.maplibregl = {
    Map: function () { return map; },
    Popup: function () {
      const popup = {
        setHTML: () => popup,
        setDOMContent: () => popup,
        setLngLat: () => popup,
        addTo: () => popup,
        getElement: () => document.createElement('div'),
        on: () => {},
        remove: () => {},
      };
      return popup;
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/** The narrowest 2D-canvas double the route marker colours need. */
function stubCanvas2D() {
  const original = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = function getContext(type) {
    if (type !== '2d') return original ? original.call(this, type) : null;
    return {
      fillStyle: '#000000',
      fillRect: () => {},
      getImageData: () => ({ data: Uint8ClampedArray.from([0, 0, 0, 255]) }),
    };
  };
}

/** The DOM map.js's boot reads. */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-resorts-geojson-url="/api/resorts.geojson"
         data-community-reports-url="/api/community-reports.geojson"
         data-routes-url="/routes/routes.geojson"
         data-routes-eligible="true"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <section id="map-route-slope-section" hidden></section>`;
}

/** How many times the routes feed has been asked for. */
function routesFetchCount() {
  return globalThis.fetch.mock.calls.filter(
    ([url]) => String(url).includes('routes.geojson'),
  ).length;
}

/** Announce a write, optionally as the upload or claim path does. */
function announce(detail) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:routes-changed', { detail: detail || null }),
  );
}

/** Per-request response delays, in ms, consumed in call order. */
let routesDelays = [];

let mapStub;

beforeAll(async () => {
  localStorage.clear();
  stubCanvas2D();
  buildFixture();
  Object.defineProperty(window, 'caches', {
    value: { keys: async () => [], open: async () => ({ keys: async () => [] }) },
    configurable: true,
    writable: true,
  });
  vi.stubGlobal(
    'fetch',
    vi.fn((url) => {
      // Captured HERE, when the request is made, not when `json()` is
      // awaited. A response body is decided by the server when it serves
      // the request, and two requests issued either side of a write must
      // be able to answer differently — which is the whole condition the
      // overlapping-writes test needs. Reading `routesPayload` lazily in
      // `json()` gave every in-flight fetch the LATEST value, so that test
      // passed against the bug it was written to catch.
      const body = String(url).includes('routes.geojson') ? routesPayload : EMPTY_FC;
      // Per-request DELAY, so a test can decide which of two in-flight
      // refreshes lands first. Without it both settle in the same
      // microtask flush and interleave, which is not what two real HTTP
      // round trips do — and an overlapping-writes test run that way
      // cannot tell a correct implementation from a racy one.
      const delay = routesDelays.shift() || 0;
      return Promise.resolve({
        ok: true,
        json: () => (delay
          ? new Promise((resolve) => setTimeout(() => resolve(body), delay))
          : Promise.resolve(body)),
      });
    }),
  );

  mapStub = stubMapLibre();

  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  await import('../../static/js/route_markers_core.js');
  await import('../../static/js/route_slope_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();

  await window.pwaRoutesOverlay.show();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

beforeEach(() => {
  routesPayload = ROUTES_UNSAMPLED;
  routesDelays = [];
  // Fake timers are armed AFTER the boot above, which schedules work of
  // its own that has nothing to do with this suite.
  vi.useFakeTimers();
  globalThis.fetch.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('an upload of a route the server has not sampled yet', () => {
  it('re-reads the feed once, after the delay, and then stops', async () => {
    announce({ uploaded: true });
    // The upload's own refresh — the one that reads the null record.
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    // Well short of the delay: nothing yet.
    await vi.advanceTimersByTimeAsync(5000);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(2);

    // ONE shot. A poll would keep going here, from every device that has
    // ever uploaded a route.
    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(2);
  });

  it('schedules nothing when the record is already there', async () => {
    // ImmediateBackend — dev, test and staging — stores the samples before
    // the upload's response returns, so this is what those environments
    // see on every upload.
    routesPayload = ROUTES_SAMPLED;

    announce({ uploaded: true });
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(1);
  });

  it('does nothing once the overlay it would repaint has gone', async () => {
    announce({ uploaded: true });
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    // A basemap swap or a teardown between the schedule and the fire: the
    // sources are gone, so there is nothing to write the payload to.
    const removed = mapStub.sources.get('route-slopes');
    mapStub.sources.delete('route-slopes');

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(1);

    // RESTORED, because the bundle is booted once for the whole file and
    // this is the only test that takes a source away. Leaving it deleted
    // makes every later test read as "the overlay is gone" and pass by
    // asserting the wrong reason — which is how the claim case below
    // could have looked green while doing nothing.
    mapStub.sources.set('route-slopes', removed);
  });
});

describe('a claim that beat the sharer\'s sampling task (SNOW-910)', () => {
  it('re-reads the feed once, exactly as an upload does', async () => {
    // A claim copies the sharer's record, so it USUALLY arrives coloured.
    // But the link works from the moment it is minted, so a claim can beat
    // the sharer's own sampling task: the copy inherits null and
    // ``claim_route_share`` samples it. Same race an upload runs, and
    // until SNOW-910 the claim was excluded from the cure.
    announce({ claimed: true });
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(2);

    // One shot here too.
    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(2);
  });

  it('schedules nothing when the copy inherited a record', async () => {
    // The common case: the sharer's row was already sampled, so the copy
    // carries it and there is nothing to wait for.
    routesPayload = ROUTES_SAMPLED;

    announce({ claimed: true });
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(1);
  });
});

describe('two writes whose refreshes overlap', () => {
  it('does not let the first response answer for the second', async () => {
    // A claim that inherited the sharer's record and an upload that has
    // not been sampled can be in flight together. The claim's payload
    // carries nothing unsampled; the upload's does. While the two shared
    // one pending flag, whichever response landed first consumed it for
    // both, and the upload stayed flat until a reload.
    // The claim's refresh lands FIRST and finds nothing to wait for; the
    // upload's lands later carrying the unsampled route. That order is the
    // whole point — it is the one in which a shared flag is consumed by
    // the wrong response.
    routesDelays = [10, 50];

    routesPayload = ROUTES_SAMPLED;
    announce({ claimed: true });

    routesPayload = ROUTES_UNSAMPLED;
    announce({ uploaded: true });

    await vi.advanceTimersByTimeAsync(20);
    expect(routesFetchCount()).toBe(2);

    // The upload's response arrives now, and it is the one that must arm.
    await vi.advanceTimersByTimeAsync(40);
    expect(routesFetchCount()).toBe(2);

    await vi.advanceTimersByTimeAsync(20000);
    expect(routesFetchCount()).toBe(3);

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(3);
  });
});

describe('a rename or a delete', () => {
  it('leaves a legacy unsampled route alone', async () => {
    // Neither can put a route on the server, so neither can produce one
    // that is about to gain a record. The payload here is unsampled — the
    // state a route the one-shot backfill never reached is in for good —
    // and arming off that would cost a refetch on every page load for the
    // rest of its life.
    announce();
    await vi.advanceTimersByTimeAsync(1);
    expect(routesFetchCount()).toBe(1);

    await vi.advanceTimersByTimeAsync(120000);
    expect(routesFetchCount()).toBe(1);
  });
});
