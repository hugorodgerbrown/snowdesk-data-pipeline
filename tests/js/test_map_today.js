/*
 * tests/js/test_map_today.js — the map's ways back to today (SNOW-1165).
 *
 * static/js/today_core.js decides whether "Today" has anywhere to go;
 * static/js/map_today.js enables the date control's "Today" half on any
 * other day and, from it or a sheet strip's ``[data-back-to-today]``, asks
 * the scrubber to move to today.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - "Today" is disabled on today and enabled on any other day, including
 *     from a boot ``?d=`` before the scrubber has committed anything;
 *   - a press asks the scrubber (``snowdesk:scrub-to``) rather than
 *     committing a day itself;
 *   - a sheet's strip button, swapped in after load, works the same way.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/today_core.js';

const core = window.pwaTodayCore;

describe('canGoToToday', () => {
  it('is false on today', () => {
    expect(core.canGoToToday('2026-10-21', '2026-10-21')).toBe(false);
  });

  it('is true on any other day, past or future', () => {
    expect(core.canGoToToday('2026-04-08', '2026-10-21')).toBe(true);
    expect(core.canGoToToday('2026-10-22', '2026-10-21')).toBe(true);
  });

  it('is true on no day at all, when there is a today to go to', () => {
    expect(core.canGoToToday(null, '2026-10-21')).toBe(true);
  });

  it.each([[''], [null], [undefined]])('is false with no today (%s)', (today) => {
    expect(core.canGoToToday('2026-04-08', today)).toBe(false);
  });
});

const MAP = `
  <div id="season-scrubber" data-today="2026-10-21"></div>
  <button id="map-today-control" type="button" disabled>Today</button>
  <div id="sheet"></div>`;

const control = () => document.getElementById('map-today-control');
let added = [];
let scrubs = [];
const onScrub = (e) => scrubs.push(e.detail);

/** Load a fresh copy of the wiring (an IIFE that binds at load). */
async function load() {
  vi.resetModules();
  await import('../../static/js/map_today.js');
}

/** Announce a committed day, as map_scrubber.js does. */
function commit(date) {
  document.dispatchEvent(new CustomEvent('snowdesk:date-changed', { detail: { date, source: 'scrubber' } }));
}

beforeEach(() => {
  document.body.innerHTML = MAP;
  history.replaceState(null, '', '/map/');
  scrubs = [];
  added = [];
  const original = document.addEventListener.bind(document);
  vi.spyOn(document, 'addEventListener').mockImplementation((type, fn, opts) => {
    if (['click', 'snowdesk:date-changed'].includes(type)) added.push([type, fn]);
    return original(type, fn, opts);
  });
  original('snowdesk:scrub-to', onScrub);
});

afterEach(() => {
  for (const [type, fn] of added) document.removeEventListener(type, fn);
  document.removeEventListener('snowdesk:scrub-to', onScrub);
  vi.restoreAllMocks();
});

describe('the "Today" control', () => {
  it('stays disabled on a bare /map/, which is today', async () => {
    await load();
    expect(control().disabled).toBe(true);
  });

  it('is enabled at once by a boot ?d= for another day', async () => {
    history.replaceState(null, '', '/map/?d=2026-04-08');
    await load();
    expect(control().disabled).toBe(false);
  });

  it('follows the committed day', async () => {
    await load();
    commit('2026-04-08');
    expect(control().disabled).toBe(false);
    commit('2026-10-21');
    expect(control().disabled).toBe(true);
  });

  it('asks the scrubber for today on a press', async () => {
    history.replaceState(null, '', '/map/?d=2026-04-08');
    await load();
    control().click();
    expect(scrubs).toEqual([{ date: '2026-10-21', source: 'today' }]);
  });
});

describe("a sheet's strip", () => {
  it('asks the scrubber for today from a button swapped in after load', async () => {
    await load();
    document.getElementById('sheet').innerHTML =
      '<div><button type="button" data-back-to-today><span>Back to today</span></button></div>';
    document.querySelector('[data-back-to-today] span').click();
    expect(scrubs).toEqual([{ date: '2026-10-21', source: 'today' }]);
  });

  it('ignores clicks anywhere else', async () => {
    await load();
    document.getElementById('sheet').click();
    expect(scrubs).toEqual([]);
  });
});
