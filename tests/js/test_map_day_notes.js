/*
 * tests/js/test_map_day_notes.js — the day's notes behind the date row's
 * bell (SNOW-1206).
 *
 * static/js/map_day_notes.js against a reduced copy of the bell and its
 * card, with ``window.pwaMapOverlays`` stood in for by a stub.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - the bell shows only while the day has a note, and the card lists the
 *     notes in rank order whatever order they were written in;
 *   - the dot means "not yet seen": it clears when the card opens, stays
 *     clear for the same notes, and comes back when they change;
 *   - a note's press runs its action and closes the card;
 *   - the card closes on a press outside it and on Escape, and opens
 *     without closing the sheet being read (spareObjectSheets).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';

const MARKUP = `
  <div id="season-scrubber" data-today="2026-10-23"></div>
  <div id="map-day-notes" hidden>
    <button id="map-day-notes-toggle" type="button" aria-expanded="false">
      <span data-day-notes-dot hidden></span>
    </button>
    <div id="map-day-notes-card" hidden>
      <p data-day-notes-heading></p>
      <ul data-day-notes-list></ul>
    </div>
  </div>
  <template id="map-day-notes-strings-template">
    <span data-string="heading">Über %(day)s</span>
  </template>
  <button id="elsewhere" type="button">elsewhere</button>`;

const EMPTY = { title: 'No bulletin', detail: 'The last one was for 30 Apr.', rank: 1 };
const WEATHER = { title: 'Weather is a forecast', detail: 'It covers 23–29 Oct.', rank: 2 };

let added = [];

const $ = (id) => document.getElementById(id);
const dot = () => document.querySelector('[data-day-notes-dot]');
const titles = () =>
  [...document.querySelectorAll('.map-day-notes-item-title')].map((el) => el.textContent);
const notes = () => window.pwaDayNotes;

/** Load a fresh copy of the wiring. */
async function load() {
  vi.resetModules();
  await import('../../static/js/map_day_notes.js');
}

beforeEach(() => {
  document.body.innerHTML = MARKUP;
  globalThis.readUrlDateParam = () => '2026-02-12';
  globalThis.formatDatePopup = (key) => `day ${key}`;
  window.pwaMapOverlays = { opening: vi.fn(), register: vi.fn() };
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    added.push([type, fn]);
    return original(type, fn, opts);
  });
});

afterEach(() => {
  for (const [type, fn] of added) document.removeEventListener(type, fn);
  added = [];
  delete window.pwaMapOverlays;
  delete window.pwaDayNotes;
  vi.restoreAllMocks();
});

describe('the bell', () => {
  it('shows only while the day has a note', async () => {
    await load();
    expect($('map-day-notes').hidden).toBe(true);
    notes().set('weather', WEATHER);
    expect($('map-day-notes').hidden).toBe(false);
    notes().set('weather', null);
    expect($('map-day-notes').hidden).toBe(true);
  });

  it('lists the notes by rank, under the day they are about', async () => {
    await load();
    notes().set('weather', WEATHER);
    notes().set('no-bulletin', EMPTY);
    expect(titles()).toEqual(['No bulletin', 'Weather is a forecast']);
    expect(document.querySelector('[data-day-notes-heading]').textContent).toBe('Über day 2026-02-12');
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-02-14' } }));
    expect(document.querySelector('[data-day-notes-heading]').textContent).toBe('Über day 2026-02-14');
  });
});

describe('the dot', () => {
  it('clears on opening, stays clear for the same notes, and returns for new ones', async () => {
    await load();
    notes().set('weather', WEATHER);
    expect(dot().hidden).toBe(false);
    $('map-day-notes-toggle').click();
    expect(dot().hidden).toBe(true);
    $('map-day-notes-toggle').click();
    notes().set('weather', { ...WEATHER });
    expect(dot().hidden).toBe(true);
    notes().set('no-bulletin', EMPTY);
    expect(dot().hidden).toBe(false);
  });
});

describe('the card', () => {
  it('opens beside the sheet rather than over it, and says so', async () => {
    await load();
    notes().set('weather', WEATHER);
    $('map-day-notes-toggle').click();
    expect($('map-day-notes-card').hidden).toBe(false);
    expect($('map-day-notes-toggle').getAttribute('aria-expanded')).toBe('true');
    expect(window.pwaMapOverlays.opening).toHaveBeenCalledWith('day-notes', { spareObjectSheets: true });
    expect(window.pwaMapOverlays.register).toHaveBeenCalledWith(
      'day-notes',
      expect.objectContaining({ isOpen: expect.any(Function), close: expect.any(Function) })
    );
  });

  it("runs a note's press and closes", async () => {
    const run = vi.fn();
    await load();
    notes().set('no-bulletin', { ...EMPTY, action: { label: 'Show 30 Apr', run } });
    $('map-day-notes-toggle').click();
    const press = document.querySelector('.map-day-notes-action');
    expect(press.textContent).toBe('Show 30 Apr');
    press.click();
    expect(run).toHaveBeenCalledOnce();
    expect($('map-day-notes-card').hidden).toBe(true);
  });

  it('closes on a press outside and on Escape', async () => {
    await load();
    notes().set('weather', WEATHER);
    $('map-day-notes-toggle').click();
    $('elsewhere').click();
    expect($('map-day-notes-card').hidden).toBe(true);
    $('map-day-notes-toggle').click();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect($('map-day-notes-card').hidden).toBe(true);
    expect($('map-day-notes-toggle').getAttribute('aria-expanded')).toBe('false');
  });

  it('closes, with the bell, when the last note goes', async () => {
    await load();
    notes().set('weather', WEATHER);
    $('map-day-notes-toggle').click();
    notes().set('weather', null);
    expect($('map-day-notes-card').hidden).toBe(true);
    expect($('map-day-notes').hidden).toBe(true);
  });
});
