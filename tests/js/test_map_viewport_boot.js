/*
 * tests/js/test_map_viewport_boot.js — map.js wires the stored camera into
 * the MapLibre constructor, and keeps writing it back (SNOW-737).
 *
 * `test_map_viewport_core.js` covers the validation in isolation. This file
 * covers the three things that live in `map.js` itself and that a pure-core
 * test cannot see:
 *
 *   - a valid stored camera reaches the constructor as `center`/`zoom`, and
 *     the default `bounds` frame is NOT also passed (the two are alternatives
 *     — passing both is how a restore silently does nothing);
 *   - a `#REGION-ID` deep link suppresses the restore AND survives the boot
 *     with the stored camera untouched;
 *   - the `moveend` writer is bound WITHOUT the `load` handler having run;
 *   - SNOW-1450: a linked camera (`?at=`) beats the stored one, a flat boot
 *     zeroes its pitch, one outside the elevation's coverage boots a
 *     `?view=3d` flat, and `at` leaves the address bar.
 *
 * That last one is not hypothetical. The first cut of this ticket bound the
 * writer inside `map.on('load')`, reasoning that the constructor's own
 * `bounds` framing would otherwise be mistaken for a real move. It does not
 * — MapLibre 4.7.1 applies the constructor camera without emitting `moveend`
 * at all — and binding inside `load` meant the viewport silently stopped
 * being remembered whenever the basemap style failed to load, which is a
 * state this map genuinely reaches (it is why SNOW-483's fallback style
 * exists). Every test below therefore boots the bundle and fires `moveend`
 * without running a single `load` handler.
 */

import { afterAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const STORAGE_KEY = 'snowdesk.map.viewport';

// map.js's own frame and limits. A camera inside them, and two that a past
// build could plausibly have written but this one must refuse.
const ECRINS = { lng: 6.35, lat: 44.92, zoom: 11.5, bearing: 0, pitch: 0 };
const DEFAULT_BOUNDS = [[5.9, 45.8], [10.5, 47.9]];

const REGIONS_GEOJSON = {
  type: 'FeatureCollection',
  features: [{
    type: 'Feature',
    properties: { id: 'CH-4115', name: 'CH-4115', covered: true },
    geometry: { type: 'Polygon', coordinates: [[[7, 46], [7, 47], [8, 47], [7, 46]]] },
  }],
};

/**
 * MapLibre stub that records the constructor options and the camera reads.
 *
 * The camera getters return the Écrins rather than a neutral point so the
 * value written by `moveend` is distinguishable from anything a default
 * could have produced.
 */
function stubMapLibre() {
  const handlers = {};
  const constructorOptions = [];
  const map = {
    on: (ev, a, b) => { (handlers[ev] ||= []).push(typeof a === 'function' ? a : b); },
    once: () => {},
    off: () => {},
    addControl: () => {},
    removeControl: () => {},
    getLayer: () => null,
    getFilter: () => null,
    getLayoutProperty: () => 'visible',
    getPaintProperty: () => 1,
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
    getFeatureState: () => ({}),
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
    getZoom: () => ECRINS.zoom,
    getCenter: () => ({ lng: ECRINS.lng, lat: ECRINS.lat }),
    getBearing: () => ECRINS.bearing,
    getPitch: () => ECRINS.pitch,
    getBounds: () => ({
      getWest: () => 5, getSouth: () => 45, getEast: () => 10, getNorth: () => 48,
    }),
    project: () => ({ x: 0, y: 0 }),
    unproject: () => ({ lng: 8, lat: 46.5 }),
    queryRenderedFeatures: () => [],
    resize: () => {},
    handlers,
    constructorOptions,
  };
  globalThis.maplibregl = {
    Map: function (options) { constructorOptions.push(options); return map; },
    Popup: function () {
      return { setLngLat: () => ({ setHTML: () => ({ addTo: () => {} }) }), remove: () => {} };
    },
    GeolocateControl: function () { return { on: () => {} }; },
    AttributionControl: function () { return {}; },
    MercatorCoordinate: { fromLngLat: () => ({ x: 0, y: 0 }) },
  };
  return map;
}

function buildFixture({ view = false } = {}) {
  // SNOW-1450: `view` is a reader in map_3d and map_modes, whose #map says
  // the modes and the 3D view are on.
  const flags = view ? 'data-map-modes-enabled="true" data-map-view-enabled="true"' : '';
  document.body.innerHTML = `
    <div id="map" ${flags}
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

/**
 * Boot the bundle at `url` with `stored` in the viewport key.
 *
 * Deliberately does NOT run the `load` handlers — see this file's header.
 *
 * @param {string} url - the address to boot at.
 * @param {?string} stored - the raw viewport value, or null for none.
 * @param {{view?: boolean}} [options] - `view` boots a reader in the 3D view's
 *     flags, with the mode and coverage cores loaded.
 * @returns {Promise<Object>} The MapLibre stub.
 */
async function bootWith(url, stored, { view = false } = {}) {
  localStorage.clear();
  if (stored !== null) localStorage.setItem(STORAGE_KEY, stored);
  window.history.replaceState({}, '', url);
  buildFixture({ view });
  const mapStub = stubMapLibre();
  vi.stubGlobal('fetch', vi.fn((input) => {
    const body = String(input).includes('regions.geojson') ? REGIONS_GEOJSON : {};
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  }));

  vi.resetModules();
  if (view) {
    await import('../../static/js/map_mode_core.js');
    await import('../../static/js/slope_classes_core.js');
    await import('../../static/js/terrain_filter_core.js');
  }
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  return mapStub;
}

/** The options the boot IIFE handed the Map constructor. */
function cameraOptions(mapStub) {
  const options = mapStub.constructorOptions[0];
  return {
    center: options.center,
    zoom: options.zoom,
    bearing: options.bearing,
    pitch: options.pitch,
    bounds: options.bounds,
    maxPitch: options.maxPitch,
  };
}

afterAll(() => {
  vi.unstubAllGlobals();
  delete globalThis.maplibregl;
  window.history.replaceState({}, '', '/');
});

beforeEach(() => {
  localStorage.clear();
});

describe('the stored camera reaches the constructor', () => {
  it('boots at the stored centre and zoom, not the default frame', async () => {
    const mapStub = await bootWith('/', JSON.stringify(ECRINS));
    const options = cameraOptions(mapStub);

    expect(options.center).toEqual([ECRINS.lng, ECRINS.lat]);
    expect(options.zoom).toBe(ECRINS.zoom);
    // The alternative, not the pair: `bounds` alongside a restored centre is
    // how a restore silently does nothing.
    expect(options.bounds).toBeUndefined();
  });

  it('falls back to the default frame when nothing is stored', async () => {
    const mapStub = await bootWith('/', null);
    const options = cameraOptions(mapStub);

    expect(options.bounds).toEqual(DEFAULT_BOUNDS);
    expect(options.center).toBeUndefined();
    expect(options.zoom).toBeUndefined();
  });

  it('falls back to the default frame for a camera outside the limits', async () => {
    // A zoom this build refuses — the shape a viewport written before
    // SNOW-442 retuned maxZoom would have.
    const stale = JSON.stringify({ ...ECRINS, zoom: 25 });
    const options = cameraOptions(await bootWith('/', stale));

    expect(options.bounds).toEqual(DEFAULT_BOUNDS);
    expect(options.center).toBeUndefined();
  });

  it('falls back to the default frame for a malformed camera', async () => {
    const options = cameraOptions(await bootWith('/', 'not json at all{'));

    expect(options.bounds).toEqual(DEFAULT_BOUNDS);
    expect(options.center).toBeUndefined();
  });
});

describe('deep links suppress the restore', () => {
  it('ignores the stored camera for a #REGION-ID hash', async () => {
    // The hash does not move the camera on its own — selectFeature does not
    // frame the region — so a restore here would open a popup for a region
    // nowhere near the view.
    const mapStub = await bootWith('/#CH-4115', JSON.stringify(ECRINS));
    const options = cameraOptions(mapStub);

    expect(options.bounds).toEqual(DEFAULT_BOUNDS);
    expect(options.center).toBeUndefined();
  });

  it('ignores the stored camera for a ?favourite= deep link', async () => {
    const mapStub = await bootWith('/?favourite=abc-123', JSON.stringify(ECRINS));

    expect(cameraOptions(mapStub).bounds).toEqual(DEFAULT_BOUNDS);
  });

  it('restores for a hash that does not name a region', async () => {
    // A stray '#' or an unrelated fragment is not a deep link, and treating
    // it as one would silently cost the visitor their restore.
    const mapStub = await bootWith('/#', JSON.stringify(ECRINS));

    expect(cameraOptions(mapStub).center).toEqual([ECRINS.lng, ECRINS.lat]);
  });

  it('leaves the stored camera untouched through a deep-link boot', async () => {
    // The assertion that matters most: following a shared region link must
    // not cost the visitor the position they saved.
    const raw = JSON.stringify(ECRINS);
    await bootWith('/#CH-4115', raw);

    expect(localStorage.getItem(STORAGE_KEY)).toBe(raw);
  });
});

describe('the moveend writer', () => {
  it('is bound without the load handler having run', async () => {
    // The regression guard. Bound inside `map.on('load')`, this writer never
    // fires when the basemap style fails to load, and the viewport silently
    // stops being remembered exactly when the visitor is offline.
    const mapStub = await bootWith('/', null);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();

    for (const handler of mapStub.handlers.moveend || []) handler();

    expect(JSON.parse(localStorage.getItem(STORAGE_KEY))).toEqual(ECRINS);
  });

  it('records a move made during a deep-link boot', async () => {
    // The restore is suppressed for a deep link, but the visitor panning
    // afterwards is still a real move and must be remembered.
    const mapStub = await bootWith('/#CH-4115', null);
    for (const handler of mapStub.handlers.moveend || []) handler();

    expect(JSON.parse(localStorage.getItem(STORAGE_KEY))).toEqual(ECRINS);
  });
});

describe('a linked camera (SNOW-1450)', () => {
  // Over Saas-Fee, tilted, inside the elevation's coverage; the Écrins sit
  // outside it (south of 45.72°).
  const SAAS_FEE_AT = '7.87,46.07,12.6,64,215';
  const ECRINS_AT = `${ECRINS.lng},${ECRINS.lat},11.5,60,0`;

  it('beats the stored camera', async () => {
    const options = cameraOptions(await bootWith(`/?at=${SAAS_FEE_AT}`, JSON.stringify(ECRINS)));

    expect(options.center).toEqual([7.87, 46.07]);
    expect(options.zoom).toBe(12.6);
    expect(options.bearing).toBe(215);
    expect(options.bounds).toBeUndefined();
  });

  it('beats the default frame too', async () => {
    const options = cameraOptions(await bootWith(`/?at=${SAAS_FEE_AT}`, null));

    expect(options.center).toEqual([7.87, 46.07]);
    expect(options.bounds).toBeUndefined();
  });

  it('is ignored when malformed, and the stored camera stands', async () => {
    const options = cameraOptions(await bootWith('/?at=7.87,46.07,12.6,89,0', JSON.stringify(ECRINS)));

    expect(options.center).toEqual([ECRINS.lng, ECRINS.lat]);
  });

  it('boots flat with its pitch zeroed outside the 3D view', async () => {
    // Signed out, or outside the flags: ?view=3d means nothing here.
    const options = cameraOptions(await bootWith(`/?at=${SAAS_FEE_AT}&view=3d`, null));

    expect(options.pitch).toBe(0);
    expect(options.bearing).toBe(215);
    expect(options.maxPitch).toBe(0);
  });

  it('keeps its pitch in the 3D view, inside the coverage', async () => {
    const options = cameraOptions(await bootWith(`/?at=${SAAS_FEE_AT}&view=3d`, null, { view: true }));

    expect(options.pitch).toBe(64);
    expect(options.maxPitch).toBe(85);
  });

  it('boots a ?view=3d flat when it lies outside the coverage', async () => {
    // The stored camera is inside it, so only the link's centre can say no.
    const stored = JSON.stringify({ lng: 7.87, lat: 46.07, zoom: 12, bearing: 0, pitch: 0 });
    const options = cameraOptions(await bootWith(`/?at=${ECRINS_AT}&view=3d`, stored, { view: true }));

    expect(options.center).toEqual([ECRINS.lng, ECRINS.lat]);
    expect(options.pitch).toBe(0);
    expect(options.maxPitch).toBe(0);
    expect(new URLSearchParams(location.search).has('view')).toBe(false);
  });

  it('leaves the address bar, every other parameter kept', async () => {
    await bootWith(`/?at=${SAAS_FEE_AT}`, null);
    expect(location.search).toBe('');

    await bootWith(`/?at=${SAAS_FEE_AT}&view=3d`, null, { view: true });
    expect(location.search).toBe('?view=3d');
  });

  it('leaves the stored camera untouched until the reader moves', async () => {
    const raw = JSON.stringify(ECRINS);
    await bootWith(`/?at=${SAAS_FEE_AT}`, raw);

    expect(localStorage.getItem(STORAGE_KEY)).toBe(raw);
  });
});
