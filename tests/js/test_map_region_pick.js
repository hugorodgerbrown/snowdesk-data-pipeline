/*
 * tests/js/test_map_region_pick.js — the "Download a region" task
 * (static/js/map_region_pick.js, SNOW-1414).
 *
 * The task runs against a hand-rolled fake map exposing only what it
 * touches (sources, layers, feature-state, click handlers and
 * queryRenderedFeatures), so the assertions are about its own bookkeeping:
 * PlacementFocus entered and left in the right order, its three layers on
 * and off, a tap choosing a region, and Download closing the task BEFORE
 * the region download it starts has settled.
 *
 * The panel's markup mirrors public/partials/_map_region_pick.html (the
 * _overlay_sheet.html container plus the body template); Vitest cannot
 * render a Django template. The real MapSheet controller and the shared
 * dismiss handler run, so Cancel, the ×, Escape and a tap on the map are
 * the panel's own behaviour, not a stub's.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/overlays.js';
import '../../static/js/map_sheet.js';

const LAYERS = ['region-pick-fill', 'region-pick-line', 'region-pick-label'];

/**
 * A fake MapLibre map with one region under every tap.
 *
 * @param {?Object} hit The feature queryRenderedFeatures returns, or null.
 * @returns {Object}
 */
function makeMap(hit) {
  const layers = new Map();
  const handlers = new Map();
  const map = {
    log: [],
    featureState: new Map(),
    getSource: vi.fn((id) => (id === 'regions' ? {} : undefined)),
    getLayer: vi.fn((id) => layers.get(id)),
    addLayer: vi.fn((layer) => {
      layers.set(layer.id, layer);
      map.log.push(`add:${layer.id}`);
    }),
    removeLayer: vi.fn((id) => {
      layers.delete(id);
      map.log.push(`remove:${id}`);
    }),
    getLayoutProperty: vi.fn(() => ['Noto Sans Regular']),
    setFeatureState: vi.fn((target, state) => map.featureState.set(target.id, state)),
    removeFeatureState: vi.fn((target) => map.featureState.delete(target.id)),
    queryRenderedFeatures: vi.fn(() => (hit ? [hit] : [])),
    on: vi.fn((name, fn) => handlers.set(name, fn)),
    off: vi.fn((name) => handlers.delete(name)),
    click: (point = { x: 1, y: 1 }) => handlers.get('click')?.({ point }),
    hasLayer: (id) => layers.has(id),
  };
  return map;
}

function buildFixture() {
  document.body.innerHTML = `
    <div id="map"></div>
    <div id="region-pick-sheet" data-overlay hidden tabindex="-1"></div>
    <template id="map-region-pick-template">
      <div data-region-pick-body data-pick-state="choosing">
        <button type="button" data-action="dismiss" aria-label="Close">×</button>
        <p data-region-pick-name></p>
        <p data-region-pick-readout></p>
        <button type="button" data-action="dismiss" data-region-pick-cancel>Cancel</button>
        <button type="button" id="region-pick-confirm">Download</button>
      </div>
    </template>
    <template id="map-region-pick-strings-template">
      <span data-string="choosing">Tap a region to download it</span>
      <span data-string="hint">Region outlines only, no danger ratings</span>
      <span data-string="readout">Up to %(mb)s MB</span>
      <span data-string="unavailable">This region can't be downloaded</span>
      <span data-string="too-big">Up to %(mb)s MB, too large for this device</span>
      <span data-string="busy">Another region is still downloading.</span>
      <span data-string="already">Already downloaded.</span>
      <span data-string="failed">Couldn't be started.</span>
    </template>
  `;
}

const HIT = { id: 7, properties: { regionID: 'CH-2101', name: 'Aletsch' } };

let map;
let focusLog;
let toast;
let start;

async function load({ hit = HIT, estimate = { mb: 40, terrainMb: 0, tooBig: false } } = {}) {
  vi.resetModules();
  buildFixture();
  map = makeMap(hit);
  window.snowdeskMapState = { map };
  focusLog = map.log;
  window.PlacementFocus = {
    enter: vi.fn(() => focusLog.push('enter')),
    exit: vi.fn(() => focusLog.push('exit')),
  };
  toast = vi.fn();
  window.MapSheet.toast = toast;
  start = vi.fn();
  window.pwaRegionDownload = { start, estimate: vi.fn(() => estimate) };
  await import('../../static/js/map_region_pick_core.js');
  await import('../../static/js/map_region_pick.js');
}

const sheet = () => document.getElementById('region-pick-sheet');
const part = (name) => sheet().querySelector(`[data-region-pick-${name}]`);
const confirmBtn = () => document.getElementById('region-pick-confirm');

beforeEach(async () => {
  await load();
});

afterEach(() => {
  window.pwaRegionPick?.close();
  document.body.className = '';
  delete window.pwaRegionPick;
  delete window.pwaRegionDownload;
  delete window.PlacementFocus;
  delete window.snowdeskMapState;
});

const realToast = window.MapSheet.toast;
afterEach(() => {
  window.MapSheet.toast = realToast;
});

describe('open', () => {
  it('clears the overlays, then draws its own outlines and shows the bar', () => {
    expect(window.pwaRegionPick.open()).toBe(true);
    expect(focusLog).toEqual(['enter', ...LAYERS.map((id) => `add:${id}`)]);
    expect(sheet().hidden).toBe(false);
    expect(document.body.classList.contains('map-framing')).toBe(true);
    expect(part('name').textContent).toBe('Tap a region to download it');
    expect(confirmBtn().disabled).toBe(true);
    expect(window.pwaRegionPick.isActive()).toBe(true);
  });

  it('does nothing a second time while open', () => {
    window.pwaRegionPick.open();
    window.pwaRegionPick.open();
    expect(window.PlacementFocus.enter).toHaveBeenCalledTimes(1);
  });

  it('refuses to open without the regions source', () => {
    map.getSource = vi.fn(() => undefined);
    expect(window.pwaRegionPick.open()).toBe(false);
    expect(window.PlacementFocus.enter).not.toHaveBeenCalled();
  });
});

describe('a tap', () => {
  it('on the map leaves the panel open', () => {
    window.pwaRegionPick.open();
    document.getElementById('map').click();
    expect(sheet().hidden).toBe(false);
    expect(window.pwaRegionPick.isActive()).toBe(true);
  });

  it('chooses the region under it and prices it', () => {
    window.pwaRegionPick.open();
    map.click();
    expect(map.setFeatureState).toHaveBeenCalledWith({ source: 'regions', id: 7 }, { picked: true });
    expect(window.pwaRegionDownload.estimate).toHaveBeenCalledWith('CH-2101');
    expect(part('name').textContent).toBe('Aletsch');
    expect(part('readout').textContent).toBe('Up to 40 MB');
    expect(confirmBtn().disabled).toBe(false);
  });

  it('cannot download a region too large for this device', async () => {
    await load({ estimate: { mb: 900, terrainMb: 0, tooBig: true } });
    window.pwaRegionPick.open();
    map.click();
    expect(part('readout').textContent).toBe('Up to 900 MB, too large for this device');
    expect(confirmBtn().disabled).toBe(true);
  });

  it('cannot download a region with no download summary', async () => {
    await load({ estimate: null });
    window.pwaRegionPick.open();
    map.click();
    expect(part('readout').textContent).toBe("This region can't be downloaded");
    expect(confirmBtn().disabled).toBe(true);
  });
});

describe('Download', () => {
  it('closes the task before the download it starts has settled', () => {
    let settle;
    start.mockReturnValue(new Promise((resolve) => (settle = resolve)));
    window.pwaRegionPick.open();
    map.click();
    focusLog.length = 0;
    confirmBtn().click();
    expect(start).toHaveBeenCalledWith('CH-2101');
    // Closed while the run is still going: layers off, THEN focus left.
    expect(window.pwaRegionPick.isActive()).toBe(false);
    expect(focusLog).toEqual([...LAYERS.map((id) => `remove:${id}`), 'exit']);
    expect(sheet().hidden).toBe(true);
    settle({ started: true, reason: '' });
  });

  it('tells the reader when another region is already downloading', async () => {
    start.mockResolvedValue({ started: false, reason: 'busy' });
    window.pwaRegionPick.open();
    map.click();
    confirmBtn().click();
    await vi.waitFor(() => expect(toast).toHaveBeenCalledWith('Another region is still downloading.'));
  });

  it('says nothing after a sign-in redirect', async () => {
    start.mockResolvedValue({ started: false, reason: 'signin' });
    window.pwaRegionPick.open();
    map.click();
    confirmBtn().click();
    await Promise.resolve();
    await Promise.resolve();
    expect(toast).not.toHaveBeenCalled();
  });
});

describe('closing', () => {
  it.each([
    ['Cancel', () => part('cancel').click()],
    ['the ×', () => sheet().querySelector('[aria-label="Close"]').click()],
    ['Escape', () => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))],
    ['a mode change', () => document.dispatchEvent(new CustomEvent('snowdesk:map-mode-changed', { detail: { mode: 'terrain' } }))],
  ])('%s removes the layers, then leaves placement focus', (_name, act) => {
    window.pwaRegionPick.open();
    map.click();
    focusLog.length = 0;
    act();
    expect(focusLog).toEqual([...LAYERS.map((id) => `remove:${id}`), 'exit']);
    expect(map.removeFeatureState).toHaveBeenCalledWith({ source: 'regions', id: 7 }, 'picked');
    expect(map.off).toHaveBeenCalledWith('click', expect.any(Function));
    expect(document.body.classList.contains('map-framing')).toBe(false);
    expect(start).not.toHaveBeenCalled();
  });
});
