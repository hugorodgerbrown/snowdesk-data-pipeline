/*
 * tests/js/test_map_placement_focus.js — Vitest unit tests for
 * static/js/map_placement_focus.js.
 *
 * The module's whole job is bookkeeping over MapLibre's layer-visibility
 * API, so the tests run against a hand-rolled fake map exposing only the
 * four methods it touches (getStyle / getLayer / getLayoutProperty /
 * setLayoutProperty). That keeps the interesting assertions — which layers
 * were hidden, and what each was restored to — direct, with no MapLibre or
 * WebGL in the loop.
 *
 * The fake style deliberately mixes source types: `vector` and `raster`
 * sources plus a sourceless `background` layer stand in for the basemap,
 * `geojson` sources for everything Snowdesk draws on top. That mix is the
 * module's entire definition of "an app overlay", so it is what every test
 * here is really exercising.
 *
 * The module reads the bare global `MAP` (declared at the top of
 * static/js/map.js in production), which resolves through the global scope
 * chain to `globalThis.MAP` here.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Mirrors a realistic post-boot style: an OpenFreeMap-style basemap
// (vector + raster sources, plus the sourceless background layer the
// offline fallback style uses) with the app's own geojson overlays on top.
const SOURCES = {
  openmaptiles: { type: 'vector' },
  hillshade: { type: 'raster' },
  regions: { type: 'geojson' },
  resorts: { type: 'geojson' },
  favourites: { type: 'geojson' },
};

const BASEMAP_LAYER_IDS = ['snowdesk-offline-fallback-bg', 'water', 'roads', 'hillshade-layer'];
const OVERLAY_LAYER_IDS = [
  'regions-fill',
  'regions-line',
  'regions-line-selected',
  'regions-label',
  'resorts-pin',
  'resorts-label',
  'favourites-pin',
  'favourites-label',
];

/**
 * Build a fake MapLibre map over the fixture style above.
 *
 * @param {Object<string, string>} [initialVisibility] — layer id →
 *   'visible' | 'none', for layers whose visibility is explicitly set
 *   before the test runs. Layers absent from it report `undefined`, which
 *   is what MapLibre returns for a layer that never had the layout
 *   property written (its default is 'visible').
 */
function makeMap(initialVisibility = {}) {
  const visibility = { ...initialVisibility };
  const layers = [
    ...BASEMAP_LAYER_IDS.map((id) => ({
      id,
      // The fallback background layer is genuinely sourceless; the rest
      // hang off the basemap's vector/raster sources.
      source: id === 'snowdesk-offline-fallback-bg' ? undefined : 'openmaptiles',
    })),
    { id: 'regions-fill', source: 'regions' },
    { id: 'regions-line', source: 'regions' },
    { id: 'regions-line-selected', source: 'regions' },
    { id: 'regions-label', source: 'regions' },
    { id: 'resorts-pin', source: 'resorts' },
    { id: 'resorts-label', source: 'resorts' },
    { id: 'favourites-pin', source: 'favourites' },
    { id: 'favourites-label', source: 'favourites' },
  ];
  // Methods read through `map` rather than closing over the initial
  // objects, so a test can swap out `map.visibility` / `map.layers`
  // wholesale to model a style teardown.
  const map = {
    visibility,
    layers,
    getStyle: () => ({ sources: SOURCES, layers: map.layers }),
    getLayer: (id) => (map.layers.some((l) => l.id === id) ? { id } : undefined),
    getLayoutProperty: (id, prop) => (prop === 'visibility' ? map.visibility[id] : undefined),
    setLayoutProperty: (id, prop, value) => {
      if (prop === 'visibility') map.visibility[id] = value;
    },
  };
  return map;
}

/** Re-run the module's IIFE against fresh state and return the fresh API. */
async function loadModule() {
  vi.resetModules();
  await import('../../static/js/map_placement_focus.js');
  return window.PlacementFocus;
}

/** Visibility of the named layers, as the fake map currently records it. */
function visibilityOf(map, ids) {
  return ids.map((id) => map.visibility[id]);
}

let map;
let focus;

beforeEach(async () => {
  map = makeMap();
  globalThis.MAP = map;
  focus = await loadModule();
});

afterEach(() => {
  // Leave no module instance holding an active snapshot — its
  // still-registered snowdesk:basemap-changed listener would otherwise fire
  // against the next test's map.
  focus.exit();
  delete globalThis.MAP;
});

describe('enter()', () => {
  it('hides every app overlay layer', () => {
    focus.enter();

    expect(visibilityOf(map, OVERLAY_LAYER_IDS)).toEqual(OVERLAY_LAYER_IDS.map(() => 'none'));
  });

  it('leaves every basemap layer alone', () => {
    focus.enter();

    expect(visibilityOf(map, BASEMAP_LAYER_IDS)).toEqual(BASEMAP_LAYER_IDS.map(() => undefined));
  });

  it('reports itself active', () => {
    expect(focus.isActive()).toBe(false);

    focus.enter();

    expect(focus.isActive()).toBe(true);
  });

  it('dispatches snowdesk:placement-focus with active: true', () => {
    const seen = [];
    document.addEventListener('snowdesk:placement-focus', (e) => seen.push(e.detail.active));

    focus.enter();

    expect(seen).toEqual([true]);
  });

  it('is a no-op when the map has not initialised', async () => {
    delete globalThis.MAP;
    focus = await loadModule();

    focus.enter();

    expect(focus.isActive()).toBe(false);
  });

  it('does not throw when the style is not readable yet', async () => {
    globalThis.MAP = {
      getStyle: () => {
        throw new Error('style is not done loading');
      },
      getLayer: () => undefined,
      getLayoutProperty: () => undefined,
      setLayoutProperty: () => {},
    };
    focus = await loadModule();

    expect(() => focus.enter()).not.toThrow();
  });
});

describe('exit()', () => {
  it('restores every layer that was visible before focus', () => {
    focus.enter();
    focus.exit();

    expect(visibilityOf(map, OVERLAY_LAYER_IDS)).toEqual(OVERLAY_LAYER_IDS.map(() => 'visible'));
  });

  it('leaves an overlay that was already off still off', async () => {
    // Resorts defaults off — hiding it for placement must not turn it on
    // again on the way out.
    map = makeMap({ 'resorts-pin': 'none', 'resorts-label': 'none' });
    globalThis.MAP = map;
    focus = await loadModule();

    focus.enter();
    focus.exit();

    expect(visibilityOf(map, ['resorts-pin', 'resorts-label'])).toEqual(['none', 'none']);
    expect(map.visibility['regions-fill']).toBe('visible');
  });

  it('dispatches snowdesk:placement-focus with active: false', () => {
    focus.enter();
    const seen = [];
    document.addEventListener('snowdesk:placement-focus', (e) => seen.push(e.detail.active));

    focus.exit();

    expect(seen).toEqual([false]);
  });

  it('is a no-op when focus was never entered', () => {
    const seen = [];
    document.addEventListener('snowdesk:placement-focus', (e) => seen.push(e.detail.active));

    focus.exit();

    expect(seen).toEqual([]);
    expect(visibilityOf(map, OVERLAY_LAYER_IDS)).toEqual(OVERLAY_LAYER_IDS.map(() => undefined));
  });

  it('tolerates a layer that was removed while focus was active', () => {
    focus.enter();
    map.layers = map.layers.filter((l) => l.id !== 'favourites-pin');

    expect(() => focus.exit()).not.toThrow();
    expect(map.visibility['regions-fill']).toBe('visible');
  });
});

describe('re-entry', () => {
  it('does not clobber the original snapshot', async () => {
    map = makeMap({ 'resorts-pin': 'none' });
    globalThis.MAP = map;
    focus = await loadModule();

    focus.enter();
    // A second arm (e.g. PlacePicker.activate called twice) must not
    // re-snapshot the now-hidden layers as "was hidden".
    focus.enter();
    focus.exit();

    expect(map.visibility['resorts-pin']).toBe('none');
    expect(map.visibility['regions-fill']).toBe('visible');
  });

  it('announces the transition only once per arm', () => {
    const seen = [];
    document.addEventListener('snowdesk:placement-focus', (e) => seen.push(e.detail.active));

    focus.enter();
    focus.enter();
    focus.exit();
    focus.exit();

    expect(seen).toEqual([true, false]);
  });
});

describe('basemap swap during placement', () => {
  it('re-hides the freshly re-installed overlays', () => {
    focus.enter();
    // map.js tears down the style and re-installs every overlay visible,
    // then dispatches this — mimic that by resetting the fake's record.
    map.visibility = {};

    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));

    expect(visibilityOf(map, OVERLAY_LAYER_IDS)).toEqual(OVERLAY_LAYER_IDS.map(() => 'none'));
  });

  it('restores the re-installed overlays on exit', () => {
    focus.enter();
    map.visibility = {};
    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));

    focus.exit();

    expect(visibilityOf(map, OVERLAY_LAYER_IDS)).toEqual(OVERLAY_LAYER_IDS.map(() => 'visible'));
  });

  it('is ignored when no placement is in progress', () => {
    document.dispatchEvent(new CustomEvent('snowdesk:basemap-changed'));

    expect(visibilityOf(map, OVERLAY_LAYER_IDS)).toEqual(OVERLAY_LAYER_IDS.map(() => undefined));
  });
});
