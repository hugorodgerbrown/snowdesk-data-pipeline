/*
 * tests/js/test_map_favourite_deep_link.js — /map/?favourite=<uuid> flies to
 * that favourite's pin and opens its popup (SNOW-711, static/js/map.js).
 *
 * The deep link is the map half of the favourite detail card's crosshair
 * link, and its whole promise is that following it is indistinguishable
 * from tapping the pin: the same popup, built by the same activateFavourite
 * the pin's own tap handler calls. So this suite asserts on the two things
 * that promise is made of — where the camera went, and what was mounted in
 * the popup — rather than on any internal the deep-link code owns.
 *
 * Most of the cases are things that must NOT happen, and each is a way the
 * feature could ship looking correct:
 *
 *   - no ?favourite= at all leaves the map alone AND leaves no listener
 *     bound, which is asserted by firing the signal it would have used;
 *   - a UUID matching nothing (deleted favourite, or somebody else's, since
 *     the URL is guessable) does nothing and says nothing, and neither does
 *     an arrival by a visitor not eligible for favourites at all;
 *   - the parameter is consumed, so the refetch behind every rename and
 *     delete cannot fly the map back here an hour later;
 *   - the favourites overlay switched off is switched back ON through the
 *     panel's own path, not by painting layers whose stored preference
 *     still reads 'false' — the state that looks right until the next thing
 *     to re-read that preference switches them off again.
 *
 * MapLibre never fires 'load' or 'sourcedata' in jsdom, so the harness
 * drives both, following test_map_boot_date_paint.js's bootAt() shape (see
 * its header for why the bundle is evaluated rather than imported). The
 * stub's off() genuinely unbinds, which is what lets the happy path fire
 * 'sourcedata' twice and hold the deep link to running once.
 */

import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const FAVOURITES_KEY = 'snowdesk.map.overlay.favourites';

const TARGET_UUID = 'ffffffff-2222-4444-8888-000000000002';
const TARGET_COORDS = [7.2286, 46.0964];

const FAVOURITES_GEOJSON = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [9.8398, 46.4972] },
      properties: {
        uuid: 'ffffffff-1111-4444-8888-000000000001',
        name: 'Piz Nair',
        created_at: '2026-02-01T08:00:00+00:00',
      },
    },
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: TARGET_COORDS },
      properties: {
        uuid: TARGET_UUID,
        name: 'Cabane du Mont Fort',
        created_at: '2026-02-02T08:00:00+00:00',
      },
    },
  ],
};

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/**
 * MapLibre stub recording camera moves and popups, with a working off().
 *
 * @param {object} record - Collector for `flyTo` calls and mounted popups.
 * @returns {object} The stub map.
 */
function stubMapLibre(record) {
  const handlers = {};
  const layouts = new Map();
  const map = {
    on: (ev, a, b) => {
      (handlers[ev] ||= []).push(typeof a === 'function' ? a : b);
    },
    // Load-bearing for this suite: the deep link unbinds its own
    // 'sourcedata' listener, and a no-op off() would let a second event run
    // it again — the exact defect the once-only case is written against.
    off: (ev, fn) => {
      handlers[ev] = (handlers[ev] || []).filter((h) => h !== fn);
    },
    once: () => {},
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
    // installFavouritesLayer raises its pin layer above the choropleth on
    // every install; without this the throw lands inside the lazy-load
    // promise, which map.js swallows, and the layer silently never appears.
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
    flyTo: (options) => {
      record.flights.push(options);
    },
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
      const popup = {
        setLngLat(lngLat) { popup.lngLat = lngLat; return popup; },
        setHTML(html) { popup.html = html; return popup; },
        setDOMContent(node) { popup.node = node; return popup; },
        addTo() { record.popups.push(popup); return popup; },
        on() { return popup; },
        remove() {},
      };
      return popup;
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

/**
 * The DOM map.js's boot reads.
 *
 * @param {boolean} eligible - Value for `data-favourites-eligible`.
 * @returns {void}
 */
function buildFixture(eligible) {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings/"
         data-resorts-url="/api/resorts.json"
         data-favourites-url="/favourites/favourites.geojson"
         data-favourites-eligible="${eligible}"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>`;
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

/**
 * Boot the map bundle at `url` and let its async boot work settle.
 *
 * Stands in for favourites.js as well as for MapLibre: activateFavourite
 * hands out an empty [data-favourite-detail] container over
 * snowdesk:favourite-selected and bails unless something fills it, so the
 * listener here is what makes a mounted popup observable at all.
 *
 * @param {string} url - The address the visitor arrives on.
 * @param {boolean} eligible - Whether the visitor may see favourites at all
 *   (`data-favourites-eligible`); false is an anonymous or ineligible
 *   visitor guessing the URL.
 * @returns {Promise<object>} The stub map, the recorded flights and popups,
 *   and the UUIDs favourites.js was asked to render.
 */
async function bootAt(url, eligible = true) {
  window.history.replaceState({}, '', url);
  buildFixture(eligible);

  const record = { flights: [], popups: [], selected: [] };
  document.addEventListener('snowdesk:favourite-selected', (event) => {
    record.selected.push(event.detail.uuid);
    event.detail.container.appendChild(document.createTextNode(event.detail.name));
  });

  const mapStub = stubMapLibre(record);
  vi.stubGlobal('fetch', vi.fn((input) => {
    const href = String(input);
    const body = href.includes('favourites') ? FAVOURITES_GEOJSON : EMPTY_FC;
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }));

  vi.resetModules();
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  for (const handler of mapStub.handlers.load || []) await handler();
  // The favourites fetch is async on every path — the boot restore when the
  // overlay is on, showPanelOverlay's lazy load when the deep link switches
  // it back on — so nothing is installed yet at this point.
  // An ineligible visitor never fetches the layer, so this is a wait for
  // something that will not arrive — bounded low, since nothing after it
  // depends on the layer existing.
  await waitFor(() => !!mapStub.getLayer('favourites-pin'), eligible ? 1000 : 100);
  await new Promise((resolve) => setTimeout(resolve, 50));
  return { map: mapStub, ...record };
}

/** Fire the 'sourcedata' event MapLibre emits once a source has loaded. */
function emitSourceData(map, sourceId = 'favourites') {
  for (const handler of [...(map.handlers.sourcedata || [])]) {
    handler({ sourceId, isSourceLoaded: true, dataType: 'source' });
  }
}

afterAll(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
  window.history.replaceState({}, '', '/');
});

beforeEach(() => {
  localStorage.clear();
});

describe('/map/?favourite=<uuid>', () => {
  it('flies to the pin and opens the popup the pin\'s own tap opens', async () => {
    const booted = await bootAt(`/map/?favourite=${TARGET_UUID}`);

    emitSourceData(booted.map);

    expect(booted.flights).toEqual([{ center: TARGET_COORDS, zoom: 11 }]);
    // The popup is anchored to the pin, and its body is the container
    // favourites.js filled — i.e. it came through activateFavourite, which
    // is the whole point of not having a second popup path.
    expect(booted.selected).toEqual([TARGET_UUID]);
    expect(booted.popups).toHaveLength(1);
    expect(booted.popups[0].lngLat).toEqual(TARGET_COORDS);
    expect(booted.popups[0].node.textContent).toBe('Cabane du Mont Fort');
  });

  it('strips the parameter so a later refetch cannot re-fire it', async () => {
    const booted = await bootAt(`/map/?favourite=${TARGET_UUID}&d=2026-02-09`);

    emitSourceData(booted.map);
    // A second load of the same source — what snowdesk:favourites-changed's
    // setData produces — must not fly anywhere a second time.
    emitSourceData(booted.map);

    expect(booted.flights).toHaveLength(1);
    expect(new URLSearchParams(location.search).has('favourite')).toBe(false);
    // Every other parameter survives: ?d= is the day the map is showing.
    expect(new URLSearchParams(location.search).get('d')).toBe('2026-02-09');
  });

  it('does nothing, and binds nothing, without the parameter', async () => {
    const booted = await bootAt('/map/');

    emitSourceData(booted.map);

    expect(booted.flights).toEqual([]);
    expect(booted.popups).toEqual([]);
    expect(booted.selected).toEqual([]);
  });

  it('does nothing for a uuid the collection does not hold', async () => {
    // A deleted favourite, or one belonging to another user — the URL is
    // guessable, and neither case may produce a popup, a camera move, or
    // any hint that the UUID was or was not real.
    const booted = await bootAt('/map/?favourite=ffffffff-9999-4444-8888-000000000009');

    emitSourceData(booted.map);

    expect(booted.flights).toEqual([]);
    expect(booted.popups).toEqual([]);
    expect(booted.selected).toEqual([]);
  });

  it('does nothing for a visitor who may not see favourites at all', async () => {
    // The crosshair link is only rendered for the favourite's owner, but the
    // URL is guessable — so an anonymous or ineligible visitor arriving here
    // must not fetch the collection, must not flip the overlay preference on
    // their behalf, and above all must not throw on the way to a map that
    // otherwise opens normally.
    const booted = await bootAt(`/map/?favourite=${TARGET_UUID}`, false);

    emitSourceData(booted.map);

    expect(booted.flights).toEqual([]);
    expect(booted.popups).toEqual([]);
    expect(booted.map.getLayer('favourites-pin')).toBeNull();
    expect(localStorage.getItem(FAVOURITES_KEY)).toBeNull();
  });

  it('switches the favourites overlay back on rather than flying to nothing', async () => {
    localStorage.setItem(FAVOURITES_KEY, 'false');

    const booted = await bootAt(`/map/?favourite=${TARGET_UUID}`);
    await waitFor(
      () => booted.map.getLayoutProperty('favourites-pin', 'visibility') === 'visible',
    );
    emitSourceData(booted.map);

    expect(booted.map.getLayoutProperty('favourites-pin', 'visibility')).toBe('visible');
    expect(booted.map.getLayoutProperty('favourites-label', 'visibility')).toBe('visible');
    // Through the panel's own path, so the stored preference agrees with
    // what is painted — otherwise the next thing to re-read it (SNOW-493's
    // overlay-load re-read, a basemap swap) hides the pin again.
    expect(localStorage.getItem(FAVOURITES_KEY)).toBe('true');
    expect(window.pwaFavouritesOverlay.isEnabled()).toBe(true);
    expect(booted.flights).toEqual([{ center: TARGET_COORDS, zoom: 11 }]);
    expect(booted.popups).toHaveLength(1);
  });
});
