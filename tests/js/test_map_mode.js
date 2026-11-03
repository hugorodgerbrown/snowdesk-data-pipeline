/*
 * tests/js/test_map_mode.js — the Dated / Terrain toggle (SNOW-1394).
 *
 * static/js/map_mode.js against stub suppression hooks, so what it ASKS of
 * map.js is the assertion; tests/js/test_map_mode_suppression.js boots
 * map.js and pins what the hooks then paint. What is pinned here:
 *
 *   - the control's two states, on <html data-map-mode> and aria-pressed;
 *   - a change calls every dated overlay's setSuppressed with MAP_MODE, and
 *     no show() or hide(); a boot calls none (map.js applied it already);
 *   - the URL keeps ?d= and every other parameter, and the mode is stored;
 *   - in Terrain the dated menu rows are disabled and a tap on one does
 *     nothing, while a static row still works;
 *   - in Terrain a tap on the date comes back to Dated, not to the calendar;
 *   - the Today panel's sun is left alone.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/layer_visibility_core.js';
import '../../static/js/map_mode_core.js';

const STORAGE_KEY = 'snowdesk.map.mode';

const FIXTURE = `
  <div id="map-date-row">
    <button id="map-day-prev" type="button"></button>
    <button id="map-calendar-toggle" type="button" aria-label="Jump to a date" aria-controls="map-calendar">
      <span id="map-date-ribbon">Today</span>
    </button>
    <button id="map-day-next" type="button"></button>
    <button id="map-mode-terrain" type="button" aria-pressed="false">Terrain</button>
    <template id="map-mode-strings-template">
      <span data-string="back-to-date">Back to the dated map</span>
    </template>
    <button id="map-today-panel-toggle" type="button"></button>
  </div>
  <ul id="basemap-menu">
    <li role="none"><button data-layers-open="bulletins">Avalanche bulletins</button>
      <button data-layer-master="bulletins">switch</button></li>
    <li role="none"><button data-overlay-key="weather">Weather</button></li>
    <li role="none"><button data-overlay-key="resorts">Resorts</button></li>
    <li role="none"><button data-overlay-key="terrain">Steep terrain</button></li>
    <li role="none"><button data-layers-open="boundaries">Boundaries</button></li>
    <li role="none"><button data-overlay-key="community_reports">On the ground</button></li>
  </ul>`;

let calls;
let menuClicks;
let calendarOpens;
let modeEvents;
let added;

/** A suppression hook that records its calls. */
const hook = (name) => ({
  setSuppressed: (reason, active) => calls.push([name, reason, active]),
  show: () => calls.push([name, 'show']),
  hide: () => calls.push([name, 'hide']),
});

/** Load a fresh copy of the IIFE. */
async function load() {
  vi.resetModules();
  await import('../../static/js/map_mode.js');
}

/** The menu row a selector names. */
const row = (selector) => document.querySelector(`#basemap-menu ${selector}`);

beforeEach(() => {
  document.body.innerHTML = FIXTURE;
  delete document.documentElement.dataset.mapMode;
  localStorage.clear();
  history.replaceState(null, '', '/map/?d=2026-02-10&panel=routes');
  calls = [];
  menuClicks = [];
  calendarOpens = 0;
  modeEvents = [];
  added = [];
  window.pwaBulletinsLayer = hook('bulletins');
  window.pwaWeatherLayer = hook('weather');
  window.pwaCommunityReportsOverlay = hook('community_reports');
  window.pwaBulletinTerrain = hook('problem_terrain');
  // Stand-ins for the handlers the toggle has to get in front of: the
  // layers picker's row click and map_calendar.js's toggle.
  document.getElementById('basemap-menu').addEventListener('click', (e) => {
    menuClicks.push(e.target.textContent);
  });
  document.getElementById('map-calendar-toggle').addEventListener('click', () => {
    calendarOpens += 1;
  });
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    added.push([type, fn, opts]);
    return original(type, fn, opts);
  });
  document.addEventListener('snowdesk:map-mode-changed', (e) => modeEvents.push(e.detail.mode));
});

afterEach(() => {
  for (const [type, fn, opts] of added) document.removeEventListener(type, fn, opts);
  vi.restoreAllMocks();
  for (const name of ['pwaBulletinsLayer', 'pwaWeatherLayer', 'pwaCommunityReportsOverlay', 'pwaBulletinTerrain', 'pwaMapMode']) {
    delete window[name];
  }
});

describe('without the flag', () => {
  // SNOW-1412: map_mode.js is in the map bundle, so it loads for everyone;
  // only the flagged template renders the button it needs.
  it('does nothing when the page renders no Terrain button', async () => {
    document.getElementById('map-mode-terrain').remove();
    const before = added.length;
    await load();
    expect(window.pwaMapMode).toBeUndefined();
    expect(document.documentElement.dataset.mapMode).toBeUndefined();
    expect(added.length).toBe(before);
  });
});

describe('the control', () => {
  it('opens in Dated on a bare URL', async () => {
    await load();
    expect(document.documentElement.dataset.mapMode).toBe('dated');
    expect(document.getElementById('map-mode-terrain').getAttribute('aria-pressed')).toBe('false');
    expect(window.pwaMapMode.isTerrain()).toBe(false);
  });

  it('switches to Terrain on a tap of the mountain', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();

    expect(document.documentElement.dataset.mapMode).toBe('terrain');
    expect(document.getElementById('map-mode-terrain').getAttribute('aria-pressed')).toBe('true');
    expect(document.getElementById('map-calendar-toggle').getAttribute('aria-label')).toBe(
      'Back to the dated map',
    );
    expect(modeEvents).toEqual(['terrain']);
  });

  it('leaves the Today panel sun alone in both modes', async () => {
    await load();
    const sun = document.getElementById('map-today-panel-toggle');
    document.getElementById('map-mode-terrain').click();
    expect(sun.hidden).toBe(false);
    expect(sun.hasAttribute('aria-disabled')).toBe(false);
  });
});

describe('suppression, never preference', () => {
  it('asks every dated overlay to be held, and never shows or hides one', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();

    expect(calls).toEqual([
      ['bulletins', 'map-mode', true],
      ['weather', 'map-mode', true],
      ['community_reports', 'map-mode', true],
      ['problem_terrain', 'map-mode', true],
    ]);
  });

  it('releases them all on the way back', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();
    calls = [];
    document.getElementById('map-calendar-toggle').click();

    expect(calls).toEqual([
      ['bulletins', 'map-mode', false],
      ['weather', 'map-mode', false],
      ['community_reports', 'map-mode', false],
      ['problem_terrain', 'map-mode', false],
    ]);
  });

  it('calls no hook at boot, which map.js has already applied', async () => {
    history.replaceState(null, '', '/map/?mode=terrain');
    await load();

    expect(document.documentElement.dataset.mapMode).toBe('terrain');
    expect(calls).toEqual([]);
  });

  it('does nothing for a tap on the mode it is already in', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();
    document.getElementById('map-mode-terrain').click();

    expect(calls).toHaveLength(4);
    expect(modeEvents).toEqual(['terrain']);
  });
});

describe('the URL and this device', () => {
  it('writes ?mode=terrain and keeps ?d= and the other parameters', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();

    expect(location.search).toBe('?d=2026-02-10&panel=routes&mode=terrain');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('terrain');
  });

  it('drops ?mode= on the way back and keeps the day', async () => {
    history.replaceState(null, '', '/map/?d=2026-02-10&mode=terrain');
    await load();
    document.getElementById('map-calendar-toggle').click();

    expect(location.search).toBe('?d=2026-02-10');
    expect(localStorage.getItem(STORAGE_KEY)).toBe('dated');
  });

  it("opens in the device's last mode on a bare URL, and writes it into the URL", async () => {
    // So a link copied from the address bar reproduces the mode on a device
    // that has no stored one.
    localStorage.setItem(STORAGE_KEY, 'terrain');
    history.replaceState(null, '', '/map/?d=2026-02-10');
    await load();

    expect(window.pwaMapMode.get()).toBe('terrain');
    expect(location.search).toBe('?d=2026-02-10&mode=terrain');
    expect(calls).toEqual([]);
  });

  it('leaves a Dated URL as it is at boot', async () => {
    await load();

    expect(location.search).toBe('?d=2026-02-10&panel=routes');
  });

  it('stores the mode a link opened in', async () => {
    history.replaceState(null, '', '/map/?mode=terrain');
    await load();

    expect(localStorage.getItem(STORAGE_KEY)).toBe('terrain');
  });
});

describe('the layers menu in Terrain', () => {
  it('disables the dated rows and only them', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();

    for (const selector of [
      '[data-layers-open="bulletins"]', '[data-layer-master="bulletins"]',
      '[data-overlay-key="weather"]', '[data-layers-open="boundaries"]',
      '[data-overlay-key="community_reports"]',
    ]) {
      expect(row(selector).getAttribute('aria-disabled')).toBe('true');
    }
    expect(row('[data-overlay-key="resorts"]').hasAttribute('aria-disabled')).toBe(false);
    expect(row('[data-overlay-key="terrain"]').hasAttribute('aria-disabled')).toBe(false);
  });

  it('does nothing for a tap on a disabled row', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();
    row('[data-overlay-key="weather"]').click();
    row('[data-layers-open="bulletins"]').click();

    expect(menuClicks).toEqual([]);
  });

  it('still passes a tap on a static row through', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();
    row('[data-overlay-key="terrain"]').click();

    expect(menuClicks).toEqual(['Steep terrain']);
  });

  it('enables every row again in Dated', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();
    document.getElementById('map-calendar-toggle').click();
    row('[data-overlay-key="weather"]').click();

    expect(row('[data-overlay-key="weather"]').hasAttribute('aria-disabled')).toBe(false);
    expect(menuClicks).toEqual(['Weather']);
  });
});

describe('the date in Terrain', () => {
  it('comes back to Dated rather than opening the calendar', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();
    document.getElementById('map-calendar-toggle').click();

    expect(window.pwaMapMode.get()).toBe('dated');
    expect(calendarOpens).toBe(0);
  });

  it('opens the calendar as ever in Dated', async () => {
    await load();
    document.getElementById('map-calendar-toggle').click();

    expect(calendarOpens).toBe(1);
    expect(window.pwaMapMode.get()).toBe('dated');
  });
});
