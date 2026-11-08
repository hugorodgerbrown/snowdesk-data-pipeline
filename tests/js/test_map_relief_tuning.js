/*
 * tests/js/test_map_relief_tuning.js — the staff panel that tunes the relief
 * past the Swiss border (static/js/map_relief_tuning.js).
 *
 * MapSheet.attach is stubbed (the test_map_3d_test.js shape: a close
 * empties the sheet, then tears down) and window.pwaBeyondSwitzerland is a
 * stub whose tune records what it is handed; the core is the real one, for
 * its defaults and normaliseTuning.
 *
 * What is pinned: nothing runs without data-relief-tuning; the request
 * event opens the panel on the tuning in use; a slider shows its value at
 * once and, after the debounce, tunes the relief and saves it; a close
 * mid-wait applies what is pending; Reset tunes to the defaults and forgets
 * the saved values.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/alpine_relief_core.js';

const reliefCore = window.pwaAlpineReliefCore;
const STORAGE_KEY = 'snowdesk.map.reliefTuning';

const MARKUP = (tuning) => `
  <div id="map" ${tuning ? 'data-relief-tuning' : ''}></div>
  <div id="map-relief-tuning-sheet" hidden></div>
  <template id="map-relief-tuning-template">
    <div data-map-relief-tuning>
      <button data-action="dismiss">Close</button>
      <output data-relief-readout="rockSlope">40°</output>
      <input type="range" min="25" max="60" step="1" value="40" data-relief-input="rockSlope">
      <output data-relief-readout="rockStrength">42 %</output>
      <input type="range" min="0" max="100" step="1" value="42" data-relief-input="rockStrength">
      <output data-relief-readout="shadowStrength">36 %</output>
      <input type="range" min="0" max="100" step="1" value="36" data-relief-input="shadowStrength">
      <output data-relief-readout="highlightStrength">24 %</output>
      <input type="range" min="0" max="100" step="1" value="24" data-relief-input="highlightStrength">
      <button type="button" data-action="relief-tuning-reset">Reset</button>
    </div>
  </template>`;

let controller = null;

/** A stand-in for MapSheet.attach: a close empties the sheet, then tears down. */
function stubMapSheet() {
  window.MapSheet = {
    attach: (el, opts) => {
      controller = {
        open: vi.fn(() => el.removeAttribute('hidden')),
        close: vi.fn(() => {
          el.setAttribute('hidden', '');
          el.innerHTML = '';
          opts.onTeardown?.();
        }),
        isOpen: () => !el.hasAttribute('hidden'),
      };
      return controller;
    },
  };
}

/**
 * Load a fresh copy of the panel over a stub runtime.
 *
 * @param {{tuning?: boolean, current?: object}} [options]
 */
async function boot({ tuning = true, current = reliefCore.TUNING_DEFAULTS } = {}) {
  document.body.innerHTML = MARKUP(tuning);
  stubMapSheet();
  let now = current;
  window.pwaBeyondSwitzerland = {
    TUNING_STORAGE_KEY: STORAGE_KEY,
    tuning: () => now,
    tune: vi.fn((values) => {
      now = reliefCore.normaliseTuning(values);
      return now;
    }),
  };
  vi.resetModules();
  await import('../../static/js/map_relief_tuning.js');
  return window.pwaBeyondSwitzerland;
}

const sheet = () => document.getElementById('map-relief-tuning-sheet');
const open = () => document.dispatchEvent(new CustomEvent('snowdesk:map-relief-tuning-requested'));
const slider = (name) => sheet().querySelector(`[data-relief-input="${name}"]`);
const readout = (name) => sheet().querySelector(`[data-relief-readout="${name}"]`).textContent;

/** Move a slider as a reader would. */
function move(name, value) {
  const el = slider(name);
  el.value = String(value);
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

afterEach(() => {
  vi.useRealTimers();
  delete window.MapSheet;
  delete window.pwaBeyondSwitzerland;
  window.localStorage.clear();
  controller = null;
});

describe('the relief tuning panel', () => {
  it('does nothing without data-relief-tuning', async () => {
    await boot({ tuning: false });
    expect(controller).toBeNull();
    open();
    expect(sheet().hidden).toBe(true);
  });

  it('opens on the request, showing the tuning in use', async () => {
    await boot({ current: { rockSlope: 33, rockStrength: 0.5, shadowStrength: 0.1, highlightStrength: 0 } });
    open();
    expect(sheet().hidden).toBe(false);
    expect(slider('rockSlope').value).toBe('33');
    expect(slider('rockStrength').value).toBe('50');
    expect(readout('rockSlope')).toBe('33°');
    expect(readout('rockStrength')).toBe('50 %');
    expect(readout('highlightStrength')).toBe('0 %');
  });

  it('applies a slider live after the debounce, and saves it on this device', async () => {
    vi.useFakeTimers();
    const api = await boot();
    open();
    move('rockStrength', 30);
    expect(readout('rockStrength')).toBe('30 %');
    move('rockStrength', 25);
    expect(api.tune).not.toHaveBeenCalled();
    vi.advanceTimersByTime(150);
    expect(api.tune).toHaveBeenCalledTimes(1);
    const expected = { ...reliefCore.TUNING_DEFAULTS, rockStrength: 0.25 };
    expect(api.tune).toHaveBeenCalledWith(expected);
    expect(JSON.parse(window.localStorage.getItem(STORAGE_KEY))).toEqual(expected);
  });

  it('applies a move still waiting when the panel closes', async () => {
    vi.useFakeTimers();
    const api = await boot();
    open();
    move('rockSlope', 48);
    controller.close();
    expect(api.tune).toHaveBeenCalledWith({ ...reliefCore.TUNING_DEFAULTS, rockSlope: 48 });
    vi.advanceTimersByTime(500);
    expect(api.tune).toHaveBeenCalledTimes(1);
  });

  it('resets to the defaults and forgets the saved tuning', async () => {
    window.localStorage.setItem(STORAGE_KEY, '{"rockStrength":0.1}');
    const api = await boot({ current: { rockSlope: 30, rockStrength: 0.1, shadowStrength: 0.1, highlightStrength: 0.1 } });
    open();
    sheet().querySelector('[data-action="relief-tuning-reset"]').click();
    expect(api.tune).toHaveBeenCalledWith(reliefCore.TUNING_DEFAULTS);
    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
    expect(slider('rockSlope').value).toBe('40');
    expect(readout('shadowStrength')).toBe('36 %');
  });
});
