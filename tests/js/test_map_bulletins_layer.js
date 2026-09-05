/*
 * tests/js/test_map_bulletins_layer.js — the Bulletins fill control beside
 * the downloaded-areas overlay, wired end to end through map.js (SNOW-656).
 *
 * Named for exclusivity until the two layers stopped being exclusive: the
 * download squares became a hatch the danger colour reads through, so both
 * can now be on at once and the second half of this file asserts that they
 * leave each other alone.
 *
 * `tests/js/test_layer_visibility_core.js` covers the state machine in
 * isolation. This file covers the wiring: that `map.js` actually paints what
 * the machine says, and that the two IIFEs involved — the fill-step control
 * and the downloads panel's "Display on the map" switch, talking over
 * CustomEvents — cannot get out of step.
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
    <div class="map-controls-br" id="map-controls-br" data-expanded="true">
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
            >OpenFreeMap</button>
          </li>
          <li role="none">
            <button class="basemap-menu-item basemap-menu-item--overlay"
                    data-overlay-key="l4" aria-checked="true">Micro regions</button>
          </li>
        </ul>
      </div>
      <div id="map-controls-collapsible">
        <div class="map-controls-collapsible-inner">
          <div class="map-utility-pill map-utility-pill--fill" id="map-fill-pill" data-state="collapsed">
            <button id="map-fill-toggle" aria-expanded="false" aria-controls="map-fill-flyout"></button>
          </div>
        </div>
      </div>
      <div id="map-fill-flyout" class="map-fill-flyout" role="group" hidden>
        <button role="radio" aria-checked="false" class="map-fill-step" data-bulletins-step="0"></button>
        <button role="radio" aria-checked="false" class="map-fill-step" data-bulletins-step="0.25"></button>
        <button role="radio" aria-checked="true" class="map-fill-step" data-bulletins-step="0.5"></button>
        <button role="radio" aria-checked="false" class="map-fill-step" data-bulletins-step="0.75"></button>
        <button role="radio" aria-checked="false" class="map-fill-step" data-bulletins-step="1"></button>
      </div>
    </div>`;
}

/** The step the control currently reads, as a user would see it. */
function checkedStep() {
  const seg = document.querySelector('[data-bulletins-step][aria-checked="true"]');
  return seg ? Number(seg.dataset.bulletinsStep) : null;
}

/** `regions-fill`'s layout + paint, the pair the hit-test depends on. */
function fillState(map) {
  const opacity = map.getPaintProperty('regions-fill', 'fill-opacity');
  return {
    visibility: map.getLayoutProperty('regions-fill', 'visibility'),
    // A `case` expression when painted (resting value last), a flat 0 when not.
    opacity: Array.isArray(opacity) ? opacity[opacity.length - 1] : opacity,
  };
}

/**
 * Click a step for real, through map_basemap_picker.js's own handler.
 *
 * A genuine `click()` rather than a hand-dispatched CustomEvent: the picker
 * is a bundle member, so its handler is live here, and routing through it is
 * what makes this an integration test.
 */
function clickStep(step) {
  document.querySelector(`[data-bulletins-step="${step}"]`).click();
}

/** Flip the Micro regions row the way map_basemap_picker.js does. */
function setMicroRegions(next) {
  const row = document.querySelector('#basemap-menu [data-overlay-key="l4"]');
  if ((row.getAttribute('aria-checked') === 'true') !== next) row.click();
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
  // Every test starts from the default map: Micro regions on, the fill at its
  // default step, no downloads overlay.
  window.pwaDownloadedOverlay.hide();
  setMicroRegions(true);
  clickStep(0.5);
});

describe('the step control paints regions-fill', () => {
  it('paints at the chosen step', () => {
    clickStep(1);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 1 });

    clickStep(0.25);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0.25 });
  });

  it('goes transparent, NOT hidden, at the off step', () => {
    // The regression this file exists for: `visibility: none` here would
    // silently kill region selection, since queryRenderedFeatures returns
    // nothing from a hidden layer but everything from a transparent one.
    clickStep(0);

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });
  });

  it('drops the fill entirely only when the off step meets Micro regions off', () => {
    clickStep(0);
    setMicroRegions(false);

    expect(fillState(mapStub)).toEqual({ visibility: 'none', opacity: 0 });
  });

  it('keeps the fill when Micro regions is off but a step is chosen', () => {
    setMicroRegions(false);
    clickStep(0.75);

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0.75 });
  });

  it('emphasises a selected region proportionally at every step', () => {
    // The emphasis used to be a heavier blend weight; with the fill
    // translucent it is opacity, and it has to scale with the step rather
    // than sit at a fixed value that could fall below the resting one.
    clickStep(0.5);
    const expr = mapStub.getPaintProperty('regions-fill', 'fill-opacity');
    expect(expr[0]).toBe('case');
    expect(expr[2]).toBeGreaterThan(expr[expr.length - 1]);
  });

  it('persists the step, not merely on/off', () => {
    clickStep(0.25);
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('0.25');

    clickStep(1);
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('1');
  });
});

describe('coexistence with "Display on the map"', () => {
  // These two were mutually exclusive under SNOW-656, when both were flat
  // translucent fills over the same polygons. The squares are a hatch now
  // (static/js/hatch_core.js) — an annotation the danger colour reads
  // through rather than a second tint competing with it — so neither
  // control touches the other's state. Every assertion below is the
  // inverse of the one it replaces, and that is the point of keeping them:
  // the exclusivity had two directions and a regression could restore
  // either one on its own.

  it('switching the overlay ON leaves the choropleth exactly where it was', async () => {
    clickStep(0.75);

    await window.pwaDownloadedOverlay.show();

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0.75 });
    expect(checkedStep()).toBe(0.75);
  });

  it('switching the overlay OFF leaves it there too', async () => {
    clickStep(0.75);

    await window.pwaDownloadedOverlay.show();
    window.pwaDownloadedOverlay.hide();

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0.75 });
    expect(checkedStep()).toBe(0.75);
  });

  it('does NOT switch Bulletins on for a user who had already chosen 0', async () => {
    clickStep(0);

    await window.pwaDownloadedOverlay.show();
    window.pwaDownloadedOverlay.hide();

    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });
    expect(checkedStep()).toBe(0);
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('0');
  });

  it('choosing a step while areas are showing leaves the squares on', async () => {
    await window.pwaDownloadedOverlay.show();

    clickStep(0.5);

    expect(window.pwaDownloadedOverlay.isVisible()).toBe(true);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0.5 });
  });

  it('leaves the overlay alone when the OFF step is chosen too', async () => {
    await window.pwaDownloadedOverlay.show();

    clickStep(0);

    expect(window.pwaDownloadedOverlay.isVisible()).toBe(true);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });
  });

  it("broadcasts every change so the sheet's own switch can mirror it", async () => {
    const seen = [];
    const listener = (e) => seen.push(e.detail.visible);
    document.addEventListener('snowdesk:downloaded-overlay-changed', listener);

    await window.pwaDownloadedOverlay.show();
    // The step control is no longer a writer of this — the broadcast has
    // to stay silent for it, or the sheet's switch would flip itself off
    // for an overlay that is still drawn.
    clickStep(0.5);
    window.pwaDownloadedOverlay.hide();
    document.removeEventListener('snowdesk:downloaded-overlay-changed', listener);

    expect(seen).toEqual([true, false]);
  });
});

describe('resort-edit mode uses the same suppression', () => {
  it('suppresses without touching the step, and stacks with the overlay', async () => {
    clickStep(0.75);

    window.pwaBulletinsLayer.setSuppressed('edit-resorts', true);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });
    expect(localStorage.getItem(BULLETINS_KEY)).toBe('0.75');

    // The downloads overlay coming and going must not lift edit mode's
    // suppression — whichever reason clears second is the one that restores.
    await window.pwaDownloadedOverlay.show();
    window.pwaDownloadedOverlay.hide();
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0 });

    window.pwaBulletinsLayer.setSuppressed('edit-resorts', false);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0.75 });
  });
});

describe('the legacy l4 key seeds the step once', () => {
  it('brings a device that had the whole tier switched off back at 0', async () => {
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

    expect(checkedStep()).toBe(0);
    expect(fillState(mapStub)).toEqual({ visibility: 'none', opacity: 0 });
  });

  it('starts a fresh device at the default step', async () => {
    localStorage.clear();
    buildFixture();
    mapStub = stubMapLibre();
    vi.resetModules();
    await import('../../static/js/basemap_download_core.js');
    await import('../../static/js/search_core.js');
    await import('../../static/js/choropleth_core.js');
    loadMapBundle();
    for (const handler of mapStub.handlers.load || []) await handler();

    expect(checkedStep()).toBe(0.5);
    expect(fillState(mapStub)).toEqual({ visibility: 'visible', opacity: 0.5 });
  });
});
