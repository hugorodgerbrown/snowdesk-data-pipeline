/*
 * tests/js/test_map_mode.js — the Conditions / Terrain toggle (SNOW-1394)
 * and its second control, the layers menu's tabs (SNOW-1421).
 *
 * static/js/map_mode.js against stub suppression hooks, so what it ASKS of
 * map.js is the assertion; tests/js/test_map_mode_suppression.js boots
 * map.js and pins what the hooks then paint. What is pinned here:
 *
 *   - the control's two states, on <html data-map-mode> and aria-pressed;
 *   - a change holds the other mode's own layers and releases this one's,
 *     through every hook's setSuppressed with MAP_MODE (holds first), and
 *     calls no show() or hide(); a boot calls none (map.js applied it);
 *   - the URL keeps ?d= and every other parameter, and the mode is stored;
 *   - the menu's tabs set the mode live and stay in step with the date
 *     row; the rows carry their data-mode-only markers and are never
 *     disabled (hiding is map.css's, which jsdom cannot compute, so it is
 *     pinned in tests/public/test_map_page.py instead);
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
  <div id="basemap-menu">
    <div class="basemap-menu-header">
      <button type="button" data-map-mode-tab="dated" aria-pressed="true">Conditions</button>
      <button type="button" data-map-mode-tab="terrain" aria-pressed="false">Terrain</button>
    </div>
    <ul data-layers-page="main">
      <li role="none" data-mode-only="dated"><ul>
        <li role="none"><button data-layers-open="bulletins">Avalanche bulletins</button>
          <button data-layer-master="bulletins">switch</button></li>
        <li role="none"><button data-overlay-key="weather">Weather</button></li>
      </ul></li>
      <li role="none" data-mode-only="terrain"><ul>
        <li role="none"><button data-overlay-key="terrain">Steep terrain</button></li>
        <li role="none"><button data-layers-open="ski_touring">SAC ski touring</button></li>
      </ul></li>
      <li role="none"><button data-overlay-key="resorts">Resorts</button></li>
      <li role="none"><button data-layers-open="boundaries">Regions</button></li>
      <li role="none"><button data-overlay-key="community_reports">Field observations</button></li>
    </ul>
    <ul data-layers-page="ski_touring" hidden>
      <li role="none"><button data-layers-back>Back</button></li>
    </ul>
  </div>
  <div id="season-scrubber" data-today="2026-02-12"></div>
  <div id="region-sheet">
    <button type="button" data-terrain-only data-map-mode-to-dated>Select a date</button>
  </div>`;

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
  window.pwaBulletinTerrain = hook('problem_terrain');
  window.pwaTerrainFilter = hook('terrain_filter');
  window.pwaSkiTours = hook('ski_tours');
  window.pwaRestZones = hook('rest_zones');
  // A shared layer: no hook of its own, and never asked.
  window.pwaCommunityReportsOverlay = hook('community_reports');
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
  for (const name of [
    'pwaBulletinsLayer', 'pwaWeatherLayer', 'pwaCommunityReportsOverlay', 'pwaBulletinTerrain',
    'pwaTerrainFilter', 'pwaSkiTours', 'pwaRestZones', 'pwaMapMode',
  ]) {
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
  it('holds Conditions\' own and releases Terrain\'s, holds first, never showing or hiding one', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();

    expect(calls).toEqual([
      ['bulletins', 'map-mode', true],
      ['weather', 'map-mode', true],
      ['problem_terrain', 'map-mode', true],
      ['terrain_filter', 'map-mode', false],
      ['ski_tours', 'map-mode', false],
      ['rest_zones', 'map-mode', false],
    ]);
  });

  it('holds Terrain\'s own and releases Conditions\' on the way back', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();
    calls = [];
    document.getElementById('map-calendar-toggle').click();

    expect(calls).toEqual([
      ['terrain_filter', 'map-mode', true],
      ['ski_tours', 'map-mode', true],
      ['rest_zones', 'map-mode', true],
      ['bulletins', 'map-mode', false],
      ['weather', 'map-mode', false],
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

    expect(calls).toHaveLength(6);
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

describe('the layers menu\'s tabs', () => {
  const tab = (name) => document.querySelector(`[data-map-mode-tab="${name}"]`);
  const pressed = () => ['dated', 'terrain'].map((name) => tab(name).getAttribute('aria-pressed'));

  it('opens pressed on the mode the map opens in', async () => {
    history.replaceState(null, '', '/map/?mode=terrain');
    await load();

    expect(pressed()).toEqual(['false', 'true']);
  });

  it('changes the mode live from the menu, as the date row does', async () => {
    await load();
    tab('terrain').click();

    expect(window.pwaMapMode.get()).toBe('terrain');
    expect(document.documentElement.dataset.mapMode).toBe('terrain');
    expect(pressed()).toEqual(['false', 'true']);
    expect(document.getElementById('map-mode-terrain').getAttribute('aria-pressed')).toBe('true');
    expect(location.search).toBe('?d=2026-02-10&panel=routes&mode=terrain');
    expect(calls).toHaveLength(6);
    expect(modeEvents).toEqual(['terrain']);
  });

  it('follows the date row, and the date row follows it', async () => {
    await load();
    document.getElementById('map-mode-terrain').click();
    expect(pressed()).toEqual(['false', 'true']);

    tab('dated').click();
    expect(pressed()).toEqual(['true', 'false']);
    expect(document.getElementById('map-mode-terrain').getAttribute('aria-pressed')).toBe('false');
    expect(modeEvents).toEqual(['terrain', 'dated']);
  });

  it('does nothing for a tap on the pressed tab', async () => {
    await load();
    tab('dated').click();

    expect(calls).toEqual([]);
    expect(modeEvents).toEqual([]);
  });

  it('keeps its taps from reaching the page', async () => {
    const outside = [];
    const listen = () => outside.push('click');
    document.addEventListener('click', listen);
    await load();
    tab('terrain').click();
    document.removeEventListener('click', listen);

    expect(outside).toEqual([]);
  });

  it('goes back to the first level from an options page', async () => {
    let backs = 0;
    document.querySelector('[data-layers-back]').addEventListener('click', () => { backs += 1; });
    document.querySelector('[data-layers-page="ski_touring"]').hidden = false;
    await load();
    tab('terrain').click();

    expect(backs).toBe(1);
  });

  it('marks each mode\'s own rows and leaves the shared ones unmarked', async () => {
    await load();
    const marked = (selector) => document.querySelector(selector).closest('[data-mode-only]');

    expect(marked('[data-layers-open="bulletins"]').dataset.modeOnly).toBe('dated');
    expect(marked('[data-overlay-key="weather"]').dataset.modeOnly).toBe('dated');
    expect(marked('[data-overlay-key="terrain"]').dataset.modeOnly).toBe('terrain');
    expect(marked('[data-layers-open="ski_touring"]').dataset.modeOnly).toBe('terrain');
    for (const selector of [
      '[data-overlay-key="resorts"]', '[data-layers-open="boundaries"]',
      '[data-overlay-key="community_reports"]',
    ]) {
      expect(marked(selector)).toBeNull();
    }
  });

  it('disables no row in either mode, and passes every tap through', async () => {
    await load();
    for (const name of ['terrain', 'dated']) {
      tab(name).click();
      for (const button of document.querySelectorAll('#basemap-menu ul button')) {
        expect(button.hasAttribute('aria-disabled')).toBe(false);
        expect(button.closest('li').hasAttribute('data-map-mode-disabled')).toBe(false);
      }
    }
    row('[data-overlay-key="weather"]').click();
    tab('terrain').click();
    row('[data-overlay-key="terrain"]').click();

    expect(menuClicks).toEqual(['Weather', 'Steep terrain']);
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

  it("switches to Dated on today from the region sheet's panel", async () => {
    const scrubs = [];
    const listen = (e) => scrubs.push(e.detail);
    document.addEventListener('snowdesk:scrub-to', listen);
    await load();
    document.getElementById('map-mode-terrain').click();
    document.querySelector('[data-map-mode-to-dated]').click();
    document.removeEventListener('snowdesk:scrub-to', listen);

    expect(window.pwaMapMode.get()).toBe('dated');
    expect(new URLSearchParams(location.search).has('mode')).toBe(false);
    // Today, not the 10th the reader left: the panel offers current conditions.
    expect(scrubs).toEqual([{ date: '2026-02-12', source: 'today' }]);
    expect(calendarOpens).toBe(0);
  });

  it('asks for no day change from the panel when the map is on today', async () => {
    history.replaceState(null, '', '/map/?d=2026-02-12');
    const scrubs = [];
    const listen = (e) => scrubs.push(e.detail);
    document.addEventListener('snowdesk:scrub-to', listen);
    await load();
    document.getElementById('map-mode-terrain').click();
    document.querySelector('[data-map-mode-to-dated]').click();
    document.removeEventListener('snowdesk:scrub-to', listen);

    expect(window.pwaMapMode.get()).toBe('dated');
    expect(scrubs).toEqual([]);
  });

  it('opens the calendar as ever in Dated', async () => {
    await load();
    document.getElementById('map-calendar-toggle').click();

    expect(calendarOpens).toBe(1);
    expect(window.pwaMapMode.get()).toBe('dated');
  });
});
