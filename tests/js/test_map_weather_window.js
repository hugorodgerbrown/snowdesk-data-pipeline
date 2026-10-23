/*
 * tests/js/test_map_weather_window.js — the weather layer says when the day
 * on the map is outside its forecast window (SNOW-1196).
 *
 * static/js/map_weather_window.js against a reduced copy of the layers
 * menu's weather row and the map's notice, with ``window.pwaWeatherWindow``
 * (map.js) stood in for by a stub whose state each test sets.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the row's line shows only while the layer is on AND the day is outside
 *     the window — never on a day inside it, never with the layer off;
 *   - the map's line shows once per page, however many days are stepped
 *     through, and goes by itself or on its ×.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const MARKUP = `
  <div id="season-scrubber" data-today="2026-10-23"></div>
  <span data-weather-window-summary hidden></span>
  <div id="map-weather-window" hidden>
    <span data-weather-window-label></span>
    <button type="button" data-weather-window-close>×</button>
  </div>`;

const WINDOW = ['2026-10-23', '2026-10-24', '2026-10-25'];

let state;
let added = [];

const $ = (id) => document.getElementById(id);
const summary = () => document.querySelector('[data-weather-window-summary]');

/** Announce a new day as map_scrubber.js does. */
function goTo(date) {
  document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date } }));
}

/** Load a fresh copy of the wiring. */
async function load() {
  vi.resetModules();
  await import('../../static/js/map_weather_window.js');
}

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  state = { on: true, dates: WINDOW, day: '2026-02-12' };
  window.pwaWeatherWindow = { isOn: () => state.on, dates: () => state.dates };
  globalThis.readUrlDateParam = () => state.day;
  globalThis.formatDatePopup = (key) => `day ${key}`;
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    if (type.startsWith('snowdesk:')) added.push([type, fn]);
    return original(type, fn, opts);
  });
});

afterEach(() => {
  for (const [type, fn] of added) document.removeEventListener(type, fn);
  added = [];
  delete window.pwaWeatherWindow;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("the layers menu's weather row", () => {
  it('names the window on a day outside it', async () => {
    await load();
    expect(summary().hidden).toBe(false);
    expect(summary().textContent).toBe('Forecast only: day 2026-10-23–day 2026-10-25');
  });

  it('says nothing on a day inside the window', async () => {
    state.day = '2026-10-24';
    await load();
    expect(summary().hidden).toBe(true);
  });

  it('says nothing with the layer off, and follows it on', async () => {
    state.on = false;
    await load();
    expect(summary().hidden).toBe(true);
    state.on = true;
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
    expect(summary().hidden).toBe(false);
  });

  it('waits for the payload, then names it', async () => {
    state.dates = [];
    await load();
    expect(summary().hidden).toBe(true);
    state.dates = WINDOW;
    document.dispatchEvent(new CustomEvent('snowdesk:weather-window-loaded'));
    expect(summary().hidden).toBe(false);
  });
});

describe("the map's line", () => {
  it('shows once, then goes by itself', async () => {
    vi.useFakeTimers();
    await load();
    expect($('map-weather-window').hidden).toBe(false);
    expect(document.querySelector('[data-weather-window-label]').textContent).toBe(
      'Weather is forecast only: day 2026-10-23–day 2026-10-25'
    );
    vi.advanceTimersByTime(8000);
    expect($('map-weather-window').hidden).toBe(true);
    goTo('2026-02-11');
    expect($('map-weather-window').hidden).toBe(true);
    expect(summary().hidden).toBe(false);
  });

  it('goes on its ×, and when the day moves inside the window', async () => {
    await load();
    document.querySelector('[data-weather-window-close]').click();
    expect($('map-weather-window').hidden).toBe(true);

    document.body.innerHTML = MARKUP;
    await load();
    expect($('map-weather-window').hidden).toBe(false);
    goTo('2026-10-25');
    expect($('map-weather-window').hidden).toBe(true);
  });
});
