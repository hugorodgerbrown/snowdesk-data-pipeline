/*
 * tests/js/test_map_weather_window.js — the weather layer says when the day
 * on the map is outside its forecast window (SNOW-1196).
 *
 * static/js/map_weather_window.js against a reduced copy of the layers
 * menu's weather row, with ``window.pwaWeatherWindow`` (map.js) and
 * ``window.pwaDayNotes`` (map_day_notes.js) stood in for by stubs.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the row's line shows only while the layer is on AND the day is outside
 *     the window — never on a day inside it, never with the layer off;
 *   - the day's notes carry the same fact for exactly as long, and drop it
 *     when the day moves inside the window or the layer goes off
 *     (SNOW-1206 moved it there from a pill on the map).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const MARKUP = `
  <div id="season-scrubber" data-today="2026-10-23"></div>
  <span data-weather-window-summary hidden></span>`;

const WINDOW = ['2026-10-23', '2026-10-24', '2026-10-25'];

let state;
let added = [];
let notes;

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
  notes = new Map();
  window.pwaDayNotes = {
    set: (id, note) => (note ? notes.set(id, note) : notes.delete(id)),
  };
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
  delete window.pwaDayNotes;
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

describe("the day's note", () => {
  it('says the weather is a forecast, naming the window', async () => {
    await load();
    expect(notes.get('weather')).toEqual({
      title: 'Weather is a forecast',
      detail: 'It covers day 2026-10-23–day 2026-10-25, so this day has none.',
      rank: 2,
    });
  });

  it('stays while the day is outside the window, however many days pass', async () => {
    await load();
    goTo('2026-02-11');
    goTo('2026-02-10');
    expect(notes.has('weather')).toBe(true);
  });

  it('goes when the day moves inside the window, or the layer goes off', async () => {
    await load();
    goTo('2026-10-25');
    expect(notes.has('weather')).toBe(false);
    goTo('2026-02-11');
    expect(notes.has('weather')).toBe(true);
    state.on = false;
    document.dispatchEvent(new CustomEvent('snowdesk:overlay-visibility-changed'));
    expect(notes.has('weather')).toBe(false);
  });
});
