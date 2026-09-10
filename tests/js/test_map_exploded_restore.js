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
    // Every capture records the layer set the photograph actually contains,
    // which is the only way to assert what each sheet shows.
    frames: [],
    getCanvas() {
      return {
        toDataURL: () => {
          map.frames.push(layers
            .filter((layer) => (layer.layout?.visibility || 'visible') === 'visible')
            .map((layer) => layer.id));
          return 'data:image/png;base64,iVBORw0KGgo=';
        },
      };
    },
    getCenter: () => ({ lng: 8, lat: 46.5 }),
    getZoom: () => 8,
    getBearing: () => 0,
    getPitch: () => 0,
    resize: () => {},
    fitBounds: () => {},
    jumpTo: () => {},
    // A boot-time restoreOverlay landing mid-build: the layer appears once
    // the demo has already hidden everything for the current step.
    lateArrival: null,
    once(event, handler) {
      (pending[event] ||= []).push(handler);
    },
    off(event, handler) {
      pending[event] = (pending[event] || []).filter((fn) => fn !== handler);
    },
    // waitForFrame registers its listener and then asks for a repaint, so
    // firing here is what advances every capture step.
    triggerRepaint() {
      if (this.lateArrival) {
        layers.push(this.lateArrival);
        this.lateArrival = null;
      }
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
    // None of these exists when ``ready`` resolves: the bulletin boundary's
    // boot-time restoreOverlay('l3') is still in flight, and the tiers are
    // lazy. Each installs when its loader is asked for.
    l3: [{ id: 'bulletin-groupings-line', type: 'line', source: 'bulletin-groupings' }],
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

// DOM order, which is now also the painted order: top of the ladder first.
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
  // jsdom ships neither method. `close()` models what the UA does for the
  // close button AND for Escape: clear the open state, then fire `close`.
  HTMLDialogElement.prototype.close = function close() {
    this.removeAttribute('open');
    this.dispatchEvent(new Event('close'));
  };
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', {
    configurable: true,
    value: 900,
  });
  Image.prototype.decode = () => Promise.resolve();
});

afterEach(() => {
  vi.useRealTimers();
  window.history.replaceState({}, '', '/');
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
      'Resorts', 'L4 · Micro', 'L2 · Minor', 'L1 · Major', 'SLF bulletins', 'Swisstopo',
    ]);
    expect(document.querySelector('.map-exploded [role="status"]').textContent)
      .toBe('Every layer, together in one map.');
  });
});

describe('map_exploded.js builds the ladder clean, whatever the map is showing', () => {
  it('photographs only the basemap on the first sheet, even mid-install', async () => {
    mount();
    // The visitor has L1 and Resorts switched on, so their boot restores are
    // in flight when ``ready`` resolves. This one lands during the Swisstopo
    // step's own idle wait — after that step hid everything it could see.
    map.lateArrival = { id: 'community-reports-pin', type: 'circle', source: 'community-reports' };
    await runDemo();

    expect(map.frames[0]).toEqual(['background']);
    expect(map.frames[0]).not.toContain('community-reports-pin');
  });

  it('shows one rung per sheet, in ladder order', async () => {
    mount();
    await runDemo();

    expect(map.frames.slice(1)).toEqual([
      ['slope-raster'],
      ['regions-fill', 'bulletin-groupings-line'],
      ['major-regions-line', 'major-regions-label'],
      ['sub-regions-line', 'sub-regions-label'],
      ['regions-line', 'regions-label'],
      ['resorts-pin', 'resorts-label'],
    ]);
  });

  it('installs every tier before the first shutter, so a tier the visitor has off still appears', async () => {
    mount();
    await runDemo();

    // The ladder is fixed. `prepare` is called for each keyed tier before any
    // capture, so a visitor with L1, L2 or Resorts switched off still sees
    // those sheets — and each tier is left at whatever visibility its own
    // toggle dictates, which the restore below puts back.
    const keys = window.snowdeskLayerExplainer.prepare.mock.calls.map(([key]) => key);
    expect(keys.slice(0, 5)).toEqual([undefined, 'l3', 'l1', 'l2', 'resorts']);
  });

  it('leaves the demo switches disconnected from the map', async () => {
    mount();
    await runDemo();
    const before = map.layers.map((layer) => [layer.id, layer.layout?.visibility]);

    const slope = document.querySelector('[aria-label="Slope angle"]');
    slope.checked = false;
    slope.dispatchEvent(new Event('change'));

    // Turning a sheet off dims an SVG group. It must not reach the map.
    expect(map.layers.map((layer) => [layer.id, layer.layout?.visibility])).toEqual(before);
  });
});

describe('map_exploded.js lists the ladder in one fixed order', () => {
  it('reads top of the ladder down to the basemap, in both views', async () => {
    mount();
    await runDemo();

    const expected = [
      'Resorts', 'L4 · Micro', 'L2 · Minor', 'L1 · Major',
      'SLF bulletins', 'Slope angle', 'Swisstopo',
    ];
    expect(rowTitles()).toEqual(expected);

    // Switching views must not turn the list upside down. It used to be built
    // basemap-first and flipped with `column-reverse` for the exploded view
    // alone, so the stacked view put the basemap at the top of a ladder it is
    // the bottom of, and the order changed under the reader mid-demo.
    document.querySelector('[data-view="exploded"]').click();
    expect(rowTitles()).toEqual(expected);
    document.querySelector('[data-view="stacked"]').click();
    expect(rowTitles()).toEqual(expected);
  });

  it('numbers no rung, and names the basemap row by attribute not position', async () => {
    mount();
    await runDemo();

    // A fixed order that runs top-to-bottom cannot carry ascending numbers
    // without reading wrong, so there are none.
    expect(document.querySelectorAll('.exploded-label-number')).toHaveLength(0);
    const fixed = document.querySelectorAll('.exploded-label[data-fixed="true"]');
    expect(fixed).toHaveLength(1);
    expect(fixed[0].querySelector('strong').textContent).toBe('Swisstopo');
  });
});

describe('map_exploded.js hands the URL back too', () => {
  /*
   * ``layers=exploded`` is what opens the demo and what makes map.js force
   * the Swiss winter basemap. Closing the dialog has to take it off the
   * address bar as well, or the demo returns on the next reload, on a
   * back/forward step, and in any link the visitor copies out of the bar.
   */
  const at = (search) => window.history.replaceState({}, '', `/${search}`);

  it('drops the parameter when the demo is closed', async () => {
    at('?layers=exploded&d=2026-03-12');
    mount();
    await runDemo();

    document.querySelector('.exploded-close').click();

    // Every other parameter is the visitor's, and survives untouched.
    expect(window.location.search).toBe('?d=2026-03-12');
  });

  it('drops it on Escape as well, which closes the dialog without the button', async () => {
    at('?layers=exploded');
    mount();
    await runDemo();

    document.querySelector('.map-exploded').close();

    expect(window.location.search).toBe('');
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

  it('never reverses the label list for a view, and styles no rung number', () => {
    // The order is fixed in the DOM now, so reading order matches paint order
    // and the tab order with it. A `column-reverse` here would break all three.
    expect(CSS).not.toMatch(/column-reverse/);
    expect(CSS).not.toMatch(/exploded-label-number/);
  });
});
