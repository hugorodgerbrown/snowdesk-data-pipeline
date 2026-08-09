/*
 * tests/js/test_map_bulletins_exclusivity.js — the Bulletins row against the
 * downloaded-areas overlay, wired end to end through map.js (SNOW-656).
 *
 * `tests/js/test_layer_visibility_core.js` covers the state machine in
 * isolation. This file covers the wiring: that `map.js` actually paints what
 * the machine says, that both toggles mirror each other, and that the two
 * entry points — the layers-menu row and the sheet's "Show areas on the map"
 * switch, which are separate IIFEs talking over CustomEvents — cannot get
 * out of step.
 *
 * The assertion worth stating plainly is the fill's LAYOUT. `regions-fill`
 * is the map's hit-test target: region selection resolves through
 * `queryRenderedFeatures(e.point, {layers: ['regions-fill', 'resorts-pin']})`
 * and the hover cursor hangs off its mouseenter/mouseleave. A layer at
 * `visibility: none` returns nothing from that query, so hiding the
 * choropleth that way would leave visible borders that cannot be tapped —
 * no popup, no pointer cursor, and no error anywhere to say so. It is
 * therefore dropped to `fill-opacity: 0` and left installed. A regression to
 * `visibility: none` is the thing this file exists to catch.
 *
 * Booting map.js in jsdom follows test_map_download_bytes.js's pattern — see
 * its header for the general rationale. The stub here tracks layout AND
 * paint per layer, since both halves of that assertion matter.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import { loadMapBundle } from './_load_map_bundle.js';

const BULLETINS_KEY = 'snowdesk.map.overlay.bulletins';
const L4_KEY = 'snowdesk.map.overlay.l4';

const EMPTY_FC = { type: 'FeatureCollection', features: [] };

/**
 * Minimal MapLibre stub with real per-layer `layout` and `paint` records, so
 * a test can read back both the visibility and the opacity map.js wrote.
 */
function stubMapLibre() {
  const handlers = {};
  const layers = new Map();
  const layouts = new Map();
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
    getPaintProperty: (id, prop) => (layers.get(id) || {})[prop],
    getFeatureState: () => ({}),
    isSourceLoaded: () => true,
    getSource: () => null,
    addSource: () => {},
    addLayer: (def) => {
      layers.set(def.id, { ...(def.paint || {}) });
      layouts.set(def.id, { ...(def.layout || {}) });
    },
    removeLayer: (id) => {
      layers.delete(id);
      layouts.delete(id);
    },
    removeSource: () => {},
    setLayoutProperty: (id, prop, value) => {
      const layout = layouts.get(id) || {};
      layout[prop] = value;
      layouts.set(id, layout);
    },
    setPaintProperty: (id, prop, value) => {
      const paint = layers.get(id) || {};
      paint[prop] = value;
      layers.set(id, paint);
    },
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
 * The DOM map.js's boot reads, plus the two menu rows this ticket splits the
 * old `l4` row into — the Bulletins one is what the mirroring writes to.
 */
function buildFixture() {
  document.body.innerHTML = `
    <div id="map"
         data-regions-url="/api/regions.geojson"
         data-ratings-url="/api/ratings.json"
         data-resorts-url="/api/resorts.json"
         data-default-basemap-key="openfreemap_liberty"
         data-season-end="2026-05-31"></div>
    <div id="search-pill" data-state="collapsed">
      <button id="search-toggle" aria-expanded="false"></button>
      <input id="search-input">
    </div>
    <ul id="search-results" hidden></ul>
    <div id="basemap-pill" data-state="collapsed">
      <button id="basemap-toggle" aria-expanded="false"></button>
      <ul id="basemap-menu" hidden>
        <li role="none">
          <button
            type="button"
            class="basemap-menu-item"
            data-basemap-key="openfreemap_liberty"
            data-basemap-url="https://tiles.example.invalid/liberty.json"
            aria-checked="true"
          >Standard</button>
        </li>
        <li role="none">
          <button class="basemap-menu-item basemap-menu-item--overlay"
                  data-overlay-key="l4" aria-checked="true">Micro regions</button>
        </li>
        <li role="none">
          <button class="basemap-menu-item basemap-menu-item--overlay"
                  data-overlay-key="bulletins" aria-checked="true">Bulletins</button>
        </li>
      </ul>
    </div>`;
}

/** The Bulletins row's live checked state, as a user would read it. */
function rowChecked() {
  return document
    .querySelector('#basemap-menu [data-overlay-key="bulletins"]')
    .getAttribute('aria-checked');
}

/** `regions-fill`'s layout + paint, the pair the hit-test depends on. */
function fillState(map) {
  return {
    visibility: map.getLayoutProperty('regions-fill', 'visibility'),
    opacity: map.getPaintProperty('regions-fill', 'fill-opacity'),
  };
}

/**
 * Click a layers-menu row for real, if it isn't already in the wanted state.
 *
 * A genuine `click()` rather than a hand-dispatched CustomEvent:
 * `map_basemap_picker.js` is a bundle member, so its click handler is live
 * here, and routing through it is what makes this an integration test — the
 * picker deciding to delegate rather than flip the layer itself is half of
 * what SNOW-656 changed.
 *
 * @param {string} key A `data-overlay-key` value.
 * @param {boolean} next The state the row should end up in.
 */
function setRow(key, next) {
  const row = document.querySelector(`#basemap-menu [data-overlay-key="${key}"]`);
  if ((row.getAttribute('aria-checked') === 'true') !== next) row.click();
}

/** Click the Bulletins row. */
function clickBulletinsRow(next) {
  setRow('bulletins', next);
}

/** Click the Micro regions row. */
function clickMicroRegionsRow(next) {
  setRow('l4', next);
}

let mapStub;

beforeAll(async () => {
  buildFixture();
  mapStub = stubMapLibre();
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

  vi.resetModules();
  await import('../../static/js/basemap_download_core.js');
  await import('../../static/js/search_core.js');
  await import('../../static/js/choropleth_core.js');
  loadMapBundle();
  // MapLibre never fires 'load' in jsdom, and installRegionsLayers hangs
  // off it.
  for (const handler of mapStub.handlers.load || []) await handler();
});

afterAll(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  delete globalThis.maplibregl;
});

beforeEach(() => {
  // Every test starts from the default map: both rows on, no overlay.
  window.pwaDownloadedOverlay.hide();
  clickMicroRegionsRow(true);
  clickBulletinsRow(true);
});

describe('the Bulletins row paints regions-fill', () => {
  it('paints an opaque fill when it is on', () => {
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 1 });
  });

  it('goes transparent, NOT hidden, when it is switched off', () => {
    // The regression this file exists for: `visibility: none` here would
    // silently kill region selection, since queryRenderedFeatures returns
    // nothing from a hidden layer but everything from a transparent one.
    clickBulletinsRow(false);

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });
  });

  it('drops the fill entirely only when neither row is on', () => {
    clickBulletinsRow(false);
    clickMicroRegionsRow(false);

    expect(fillState(mapStub)).toEqual({ visibility: 'none', opacity: 0 });
  });

  it('keeps the fill when Micro regions is off but Bulletins is on', () => {
    clickMicroRegionsRow(false);

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 1 });
  });

  it('persists the preference, not the effective value', () => {
    clickBulletinsRow(false);
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('false');

    clickBulletinsRow(true);
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('true');
  });
});

describe('mutual exclusion with "Show areas on the map"', () => {
  it('switching the overlay ON hides the choropleth and unchecks the row', async () => {
    await window.pwaDownloadedOverlay.show();

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });
    expect(rowChecked()).toBe('false');
  });

  it('switching the overlay OFF brings the choropleth back', async () => {
    await window.pwaDownloadedOverlay.show();
    window.pwaDownloadedOverlay.hide();

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 1 });
    expect(rowChecked()).toBe('true');
  });

  it('does NOT switch Bulletins on for a user who had already switched it off', async () => {
    // The restore case: the overlay comes and goes over a preference that
    // was already false, and must leave it false.
    clickBulletinsRow(false);

    await window.pwaDownloadedOverlay.show();
    window.pwaDownloadedOverlay.hide();

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });
    expect(rowChecked()).toBe('false');
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('false');
  });

  it('switching Bulletins on from the layers menu switches the overlay off', async () => {
    await window.pwaDownloadedOverlay.show();

    clickBulletinsRow(true);

    expect(window.pwaDownloadedOverlay.isVisible()).toBe(false);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 1 });
  });

  it('leaves the overlay alone when Bulletins is switched OFF — both may be off', async () => {
    await window.pwaDownloadedOverlay.show();

    clickBulletinsRow(false);

    expect(window.pwaDownloadedOverlay.isVisible()).toBe(true);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });
  });

  it('broadcasts every change so the sheet\'s own switch can mirror it', async () => {
    const seen = [];
    const listener = (e) => seen.push(e.detail.visible);
    document.addEventListener('snowdesk:downloaded-overlay-changed', listener);

    await window.pwaDownloadedOverlay.show();
    clickBulletinsRow(true);
    document.removeEventListener('snowdesk:downloaded-overlay-changed', listener);

    // On for the show, off again when the layers menu took the map back.
    expect(seen).toEqual([true, false]);
  });
});

describe('resort-edit mode uses the same suppression', () => {
  it('suppresses without touching the preference, and stacks with the overlay', async () => {
    window.pwaBulletinsLayer.setSuppressed('edit-resorts', true);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('true');

    // The downloads overlay coming and going must not lift edit mode's
    // suppression — whichever reason clears second is the one that restores.
    await window.pwaDownloadedOverlay.show();
    window.pwaDownloadedOverlay.hide();
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });

    window.pwaBulletinsLayer.setSuppressed('edit-resorts', false);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 1 });
  });
});

describe('the legacy l4 key seeds the Bulletins preference once', () => {
  it('brings a device that had the whole tier switched off back with both rows off', async () => {
    // Rebooting the bundle is the only way to exercise a boot-time seed.
    localStorage.clear();
    localStorage.setItem(L4_KEY, 'false');
    buildFixture();
    mapStub = stubMapLibre();
    vi.resetModules();
    await import('../../static/js/basemap_download_core.js');
    await import('../../static/js/search_core.js');
    await import('../../static/js/choropleth_core.js');
    loadMapBundle();
    for (const handler of mapStub.handlers.load || []) await handler();

    expect(rowChecked()).toBe('false');
    expect(fillState(mapStub)).toEqual({ visibility: 'none', opacity: 0 });
  });
});
