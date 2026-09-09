/*
 * tests/js/test_map_exploded_restore.js — the layer-explainer demo's contract
 * with the map it borrows (PR #858 review).
 *
 * static/js/map_exploded.js drives the REAL MapLibre instance: it hides every
 * layer but one, photographs the canvas, and must hand the map back exactly as
 * it found it. Three of the four review findings are about that handover, and
 * none of them is visible from the server-rendered assertions in
 * tests/public/test_map_explainer.py — they need the module actually running
 * against a map, which is this layer's job (docs/client-side-tests.md).
 *
 * The fourth finding is a cascade bug with no runtime surface in jsdom (which
 * implements no layout and no user-agent dialog display rule), so it is
 * asserted against the CSS source, mirroring
 * tests/js/test_map_css_basemap_fallback.js.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const MODULE = '../../static/js/map_exploded.js';

/** Build a MapLibre stand-in whose style is a mutable list of layer objects. */
function makeMap(layers) {
  const find = (id) => layers.find((layer) => layer.id === id);
  const pending = {};
  return {
    layers,
    install(...added) {
      layers.push(...added);
    },
    getStyle: () => ({
      layers: layers.map((layer) => ({ ...layer, layout: { ...layer.layout } })),
    }),
    getLayer: find,
    setLayoutProperty(id, property, value) {
      const layer = find(id);
      if (layer) layer.layout = { ...layer.layout, [property]: value };
    },
    setFilter(id, filter) {
      const layer = find(id);
      if (layer) layer.filter = filter;
    },
    getPaintProperty: () => 0.5,
    setPaintProperty: () => {},
    getContainer: () => document.getElementById('map'),
    getCanvas: () => ({ toDataURL: () => 'data:image/png;base64,iVBORw0KGgo=' }),
    getCenter: () => ({ lng: 8, lat: 46.5 }),
    getZoom: () => 8,
    getBearing: () => 0,
    getPitch: () => 0,
    resize: () => {},
    fitBounds: () => {},
    jumpTo: () => {},
    once(event, handler) {
      (pending[event] ||= []).push(handler);
    },
    off(event, handler) {
      pending[event] = (pending[event] || []).filter((fn) => fn !== handler);
    },
    // waitForFrame registers its listener and then asks for a repaint, so
    // firing here is what advances every capture step.
    triggerRepaint() {
      for (const event of ['idle', 'render']) {
        const handlers = pending[event] || [];
        pending[event] = [];
        for (const handler of handlers) handler();
      }
    },
  };
}

/** The layers the map already has when ``snowdeskMapState.ready`` resolves. */
const bootLayers = () => [
  { id: 'background', type: 'background' },
  { id: 'regions-fill', type: 'fill', source: 'regions' },
  { id: 'regions-line', type: 'line', source: 'regions' },
  { id: 'regions-label', type: 'symbol', source: 'regions' },
];

let map;
let restoreCountries;

/**
 * Wire up the globals map_exploded.js reads, with ``slope`` deciding whether
 * the operator kill switch is on and ``lateLayers`` naming the tiers whose
 * boot-time lazy load lands only once ``prepare()`` is called.
 */
function mount({ slope = true } = {}) {
  document.body.innerHTML = '<div id="map"></div>';
  const mapEl = document.getElementById('map');
  if (slope) mapEl.dataset.slopeTileUrl = 'https://tiles.example/{z}/{x}/{y}.png';
  map = makeMap(bootLayers());
  if (slope) map.install({ id: 'slope-raster', type: 'raster', source: 'slope' });

  const late = {
    // Installed by the argument-less prepare() — the bulletin boundary's
    // boot-time restoreOverlay('l3'), still in flight when ready resolved.
    null: [{ id: 'bulletin-groupings-line', type: 'line', source: 'bulletin-groupings' }],
    l1: [
      { id: 'major-regions-line', type: 'line', source: 'major-regions' },
      { id: 'major-regions-label', type: 'symbol', source: 'major-regions' },
    ],
    l2: [
      { id: 'sub-regions-line', type: 'line', source: 'sub-regions' },
      { id: 'sub-regions-label', type: 'symbol', source: 'sub-regions' },
    ],
    resorts: [
      { id: 'resorts-pin', type: 'circle', source: 'resorts' },
      { id: 'resorts-label', type: 'symbol', source: 'resorts' },
    ],
  };

  restoreCountries = vi.fn();
  window.snowdeskMap = map;
  window.snowdeskMapState = { ready: Promise.resolve(), map };
  window.snowdeskLayerExplainer = {
    prepare: vi.fn(async (key) => {
      for (const layer of late[key] || []) {
        if (!map.getLayer(layer.id)) map.install(layer);
      }
      return { date: '2026-03-12', favouritesEligible: false, fillOpacity: 0.5 };
    }),
    focusSwitzerland: vi.fn(async () => restoreCountries),
  };
}

/** Run the demo to completion, draining its 1.1s reveal cadence. */
async function runDemo() {
  await import(MODULE);
  for (let step = 0; step < 40; step += 1) {
    await vi.advanceTimersByTimeAsync(300);
    if (document.querySelector('.map-exploded [role="status"]')?.textContent
      === 'Every layer, together in one map.') break;
  }
}

const rowTitles = () => Array.from(
  document.querySelectorAll('.exploded-label-copy strong'),
).map((el) => el.textContent);

const visibilityOf = (id) => map.getLayer(id).layout?.visibility;

beforeEach(() => {
  vi.useFakeTimers();
  vi.resetModules();
  window.ResizeObserver = class {
    observe() {}

    disconnect() {}
  };
  HTMLDialogElement.prototype.showModal = function showModal() {
    this.setAttribute('open', '');
  };
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    value: 900,
  });
  Image.prototype.decode = () => Promise.resolve();
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
  delete window.snowdeskLayerExplainer;
  delete window.snowdeskMapState;
  delete window.snowdeskMap;
});

describe('map_exploded.js hands the map back as it found it', () => {
  it('restores a layer that the boot-time lazy load installs mid-build', async () => {
    mount();
    await runDemo();

    // bulletin-groupings-line does not exist when ``ready`` resolves — the
    // boot restoreOverlay('l3') is still in flight — so a snapshot taken then
    // has no entry for it, and a ``|| 'none'`` restore would leave an
    // enabled overlay hidden with ``overlayLoaded`` already true, so no
    // loader ever reinstates it.
    expect(visibilityOf('bulletin-groupings-line')).toBe('visible');
    for (const id of ['regions-fill', 'regions-line', 'major-regions-line',
      'sub-regions-label', 'resorts-pin', 'slope-raster']) {
      expect(visibilityOf(id)).toBe('visible');
    }
  });

  it('puts the country filters back, even when the build aborts', async () => {
    mount();
    window.snowdeskLayerExplainer.prepare = vi.fn(async (key) => {
      if (key === 'l1') throw new Error('feed unavailable');
      return { date: null, favouritesEligible: false, fillOpacity: 0.5 };
    });
    await import(MODULE);
    for (let step = 0; step < 20; step += 1) await vi.advanceTimersByTimeAsync(300);

    expect(window.snowdeskLayerExplainer.focusSwitzerland).toHaveBeenCalled();
    expect(restoreCountries).toHaveBeenCalledTimes(1);
  });

  it('forces Switzerland on before the fixed Swiss view is captured', async () => {
    mount();
    await runDemo();

    expect(window.snowdeskLayerExplainer.focusSwitzerland).toHaveBeenCalledTimes(1);
    expect(restoreCountries).toHaveBeenCalledTimes(1);
  });
});

describe('map_exploded.js follows the operator slope kill switch', () => {
  it('demonstrates the slope sheet when the tile URL is configured', async () => {
    mount();
    await runDemo();

    expect(rowTitles()).toContain('Slope angle');
  });

  it('omits the slope sheet, and still builds every later layer, without one', async () => {
    mount({ slope: false });
    await runDemo();

    // The abort this guards against is total: the missing-layer check throws
    // on step two, so nothing after slope is ever demonstrated.
    expect(rowTitles()).not.toContain('Slope angle');
    expect(rowTitles()).toEqual([
      'Swisstopo', 'SLF bulletins', 'L1 · Major', 'L2 · Minor', 'L4 · Micro', 'Resorts',
    ]);
    expect(document.querySelector('.map-exploded [role="status"]').textContent)
      .toBe('Every layer, together in one map.');
  });
});

describe('map_exploded.css closed-dialog display', () => {
  const CSS = readFileSync(join(process.cwd(), 'static', 'css', 'map_exploded.css'), 'utf8');

  it('scopes the grid display to the open state', () => {
    // An author-level `display` on the bare `.map-exploded` selector beats the
    // user-agent `dialog:not([open]) { display: none }` rule, so Escape or the
    // close button would clear the modal state and leave the explainer on the
    // page — visible and inert.
    expect(CSS).toMatch(/\.map-exploded\[open\]\s*\{[^}]*display:\s*grid/);
    expect(CSS).not.toMatch(/\.map-exploded\s*\{[^}]*display:\s*grid/);
  });
});
