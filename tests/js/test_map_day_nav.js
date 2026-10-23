/*
 * tests/js/test_map_day_nav.js — the date row's day steps, the scrubber's
 * keys and the empty-day note (SNOW-1195, SNOW-1206).
 *
 * static/js/map_day_nav.js against a reduced copy of the date row, with
 * the scrubber stood in for by a listener that does what ``commitDate``
 * does: write ``?d=`` and announce ``snowdesk:date-changed``.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - every move asks the scrubber (``snowdesk:scrub-to``) rather than
 *     committing a day itself;
 *   - the steps and Home / End stop at the calendar's range, and the
 *     buttons say so by disabling;
 *   - the note is published only on a day no visible country has a
 *     bulletin for, names the nearest one that does, and goes there on a
 *     press; on today, when tomorrow has one, it names tomorrow's issue
 *     rather than calling the day empty (SNOW-1204). SNOW-1206 moved it from
 *     a pill in the date row into the day's notes (``window.pwaDayNotes``,
 *     stubbed here).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/calendar_core.js';
import '../../static/js/day_nav_core.js';

const ROW = `
  <div id="season-scrubber" role="slider" tabindex="0"
       data-today="2026-02-20" data-season-start="2026-02-10"></div>
  <button id="map-day-prev" type="button">prev</button>
  <button id="map-day-next" type="button">next</button>`;

const CACHE = {
  '2026-02-12': { 'CH-4115': 3 },
  '2026-02-13': { 'CH-4115': 3 },
  '2026-02-15': { 'CH-4115': 2 },
  '2026-02-20': { 'CH-4115': 2 },
};

let scrubs = [];
let added = [];
let notes;
let resolveRatings;

/** Commit a day as map_scrubber.js does on ``snowdesk:scrub-to``. */
function onScrub(e) {
  scrubs.push(e.detail);
  history.replaceState(null, '', `/map/?d=${e.detail.date}`);
  document.dispatchEvent(
    new CustomEvent('snowdesk:date-changed', { detail: { date: e.detail.date, source: 'scrubber' } })
  );
}

/** The day the URL says the map is on. */
const day = () => new URLSearchParams(location.search).get('d');
const $ = (id) => document.getElementById(id);
/** The empty-day note as published, or undefined. */
const note = () => notes.get('no-bulletin');

/** Load a fresh copy of the wiring and let the ratings arrive. */
async function load() {
  vi.resetModules();
  await import('../../static/js/map_day_nav.js');
  resolveRatings(CACHE);
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  document.body.innerHTML = ROW;
  history.replaceState(null, '', '/map/?d=2026-02-12');
  scrubs = [];
  added = [];
  globalThis.COUNTRY_STATE = { ch: true, fr: false };
  notes = new Map();
  window.pwaDayNotes = {
    set: (id, value) => (value ? notes.set(id, value) : notes.delete(id)),
  };
  globalThis.readUrlDateParam = () => day();
  globalThis.formatDatePopup = (key) => `day ${key}`;
  const ratings = new Promise((resolve) => {
    resolveRatings = resolve;
  });
  globalThis.getSeasonRatings = () => ratings;
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    if (type.startsWith('snowdesk:')) added.push([type, fn]);
    return original(type, fn, opts);
  });
  original('snowdesk:scrub-to', onScrub);
});

afterEach(() => {
  for (const [type, fn] of added) document.removeEventListener(type, fn);
  document.removeEventListener('snowdesk:scrub-to', onScrub);
  delete window.pwaDayNotes;
  vi.restoreAllMocks();
});

describe('the step buttons', () => {
  it('ask the scrubber for the day before and after', async () => {
    await load();
    $('map-day-next').click();
    $('map-day-next').click();
    $('map-day-prev').click();
    expect(scrubs.map((s) => s.date)).toEqual(['2026-02-13', '2026-02-14', '2026-02-13']);
    expect(scrubs[0].source).toBe('step');
  });

  it('stop at the ends of the calendar range, and disable there', async () => {
    history.replaceState(null, '', '/map/?d=2026-02-10');
    await load();
    expect($('map-day-prev').disabled).toBe(true);
    $('map-day-prev').click();
    expect(scrubs).toEqual([]);
    history.replaceState(null, '', '/map/?d=2026-02-19');
    $('map-day-next').click();
    expect(day()).toBe('2026-02-20');
    expect($('map-day-next').disabled).toBe(true);
  });
});

describe("the scrubber's keys", () => {
  /** Press a key on the scrubber. */
  const press = (key) =>
    $('season-scrubber').dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));

  it('move a day, a week and to either end', async () => {
    await load();
    press('ArrowRight');
    expect(day()).toBe('2026-02-13');
    press('PageUp');
    expect(day()).toBe('2026-02-20');
    press('Home');
    expect(day()).toBe('2026-02-10');
    press('End');
    expect(day()).toBe('2026-02-20');
  });

  it('name the day for a screen reader', async () => {
    await load();
    press('ArrowLeft');
    expect($('season-scrubber').getAttribute('aria-valuetext')).toBe('day 2026-02-11');
  });

  it('leave other keys alone', async () => {
    await load();
    expect(press('Tab')).toBe(true);
    expect(scrubs).toEqual([]);
  });
});

describe('a timelapse', () => {
  it('follows the announced day, not the URL it leaves behind', async () => {
    history.replaceState(null, '', '/map/?d=2026-02-10');
    await load();
    expect($('map-day-prev').disabled).toBe(true);
    // Playback announces each frame without writing ?d=.
    document.dispatchEvent(
      new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-02-17', source: 'timelapse' } })
    );
    expect($('map-day-prev').disabled).toBe(false);
    expect($('season-scrubber').getAttribute('aria-valuetext')).toBe('day 2026-02-17');
    expect(note()).toBeDefined();
  });
});

describe('the empty-day note', () => {
  it('is not published on a day with a bulletin', async () => {
    await load();
    expect(note()).toBeUndefined();
  });

  it('names an empty day and the last one issued, and goes there', async () => {
    history.replaceState(null, '', '/map/?d=2026-02-17');
    await load();
    expect(note().title).toBe('No bulletin for day 2026-02-17');
    expect(note().detail).toBe('The last one was for day 2026-02-15.');
    expect(note().action.label).toBe('Show day 2026-02-15');
    note().action.run();
    expect(day()).toBe('2026-02-15');
    expect(note()).toBeUndefined();
  });

  it('offers the next day when nothing came before', async () => {
    history.replaceState(null, '', '/map/?d=2026-02-10');
    await load();
    expect(note().title).toBe('No bulletin for day 2026-02-10');
    expect(note().detail).toBe('The first one is for day 2026-02-12.');
  });

  it("names tomorrow's bulletin on today when it is already out (SNOW-1204)", async () => {
    // Today is the 14th, which has no rating; the 15th's issue is out.
    $('season-scrubber').dataset.today = '2026-02-14';
    history.replaceState(null, '', '/map/?d=2026-02-14');
    await load();
    expect(note().title).toBe('Tomorrow’s bulletin is out');
    expect(note().detail).toBe('It is already published, for day 2026-02-15.');
    note().action.run();
    expect(day()).toBe('2026-02-15');
  });

  it('keeps "the last one" on a past day whose next day is rated', async () => {
    history.replaceState(null, '', '/map/?d=2026-02-14');
    await load();
    expect(note().title).toBe('No bulletin for day 2026-02-14');
    expect(note().detail).toBe('The last one was for day 2026-02-13.');
  });

  it('follows the countries that are on, with no press when none has a day', async () => {
    await load();
    globalThis.COUNTRY_STATE = { ch: false, fr: true };
    document.dispatchEvent(new CustomEvent('snowdesk:country-toggle'));
    expect(note().title).toBe('No bulletin for day 2026-02-12');
    expect(note().action).toBeUndefined();
  });
});
