/*
 * tests/js/test_stale_day.js — the stale-day notice (SNOW-920).
 *
 * A navigation the service worker serves from cache after its network
 * budget ran out carries the day it was rendered on, and SNOW-793 makes
 * that the only "today" the map has. static/js/stale_day_core.js decides
 * whether that day is behind the server's; static/js/stale_day.js polls
 * /api/version and shows "Showing 18 Oct · Reload".
 *
 * What is pinned, because each is the way this goes wrong:
 *   - a mismatch shows the label and a match shows nothing;
 *   - a failed, empty or malformed answer shows nothing (no false alarm),
 *     and does not clear a label already showing;
 *   - an explicit ?d= is never labelled;
 *   - Reload calls the shell reload and nothing reloads by itself;
 *   - the browser clock is never asked for a day (SNOW-793).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/stale_day_core.js';

const core = window.pwaStaleDayCore;

describe('isStale', () => {
  it('is stale when the page day differs from the server day', () => {
    expect(core.isStale('2026-10-18', '2026-10-19')).toBe(true);
  });

  it('is not stale when they match', () => {
    expect(core.isStale('2026-10-19', '2026-10-19')).toBe(false);
  });

  it.each([
    ['', '2026-10-19'],
    ['2026-10-18', ''],
    ['2026-10-18', undefined],
    ['yesterday', '2026-10-19'],
    ['2026-13-40', '2026-10-19'],
    ['2026-10-18', '19/10/2026'],
    [null, null],
  ])('cannot confirm from (%s, %s), so says nothing', (page, server) => {
    expect(core.isStale(page, server)).toBe(false);
  });

  it('leaves an explicit ?d= alone', () => {
    expect(core.isStale('2026-10-18', '2026-10-19', '2026-10-10')).toBe(false);
  });
});

describe('formatDay', () => {
  it('formats a day without shifting it by the device zone', () => {
    // en-GB reads "18 Oct", en-US "Oct 18": pin the locale.
    expect(core.formatDay('2026-10-18', 'en-GB')).toBe('18 Oct');
    expect(core.formatDay('2026-01-01', 'en-GB')).toBe('1 Jan');
  });

  it('hands back what it cannot format', () => {
    expect(core.formatDay('nonsense', 'en')).toBe('nonsense');
  });
});

const NOTICE = `
  <div id="season-scrubber" data-today="2026-10-18"></div>
  <div id="stale-day-notice" hidden>
    <span data-stale-day-label></span>
    <button type="button" data-stale-day-reload>Reload</button>
  </div>
  <template id="stale-day-strings-template">
    <span data-string="stale-day-showing">Montrant %(day)s</span>
  </template>`;

let refresh;
let reload;
let dateSpy;
// Each load() re-imports the IIFE, which adds document / window listeners
// that outlive it; record and remove them so one test's handler never
// answers another's event.
let added = [];

/** Load a fresh copy of the wiring (it is an IIFE with module-scope latches). */
async function load() {
  vi.resetModules();
  await import('../../static/js/stale_day.js');
  // Let the first check() settle.
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

const notice = () => document.getElementById('stale-day-notice');

beforeEach(() => {
  document.body.innerHTML = NOTICE;
  document.documentElement.lang = 'en-GB';
  history.replaceState(null, '', '/map/');
  refresh = vi.fn(async () => ({ today: '2026-10-19' }));
  reload = vi.fn(async () => {});
  window.pwaVersionInfo = { refresh, reload };
  added = [];
  for (const target of [document, window]) {
    const original = target.addEventListener.bind(target);
    vi.spyOn(target, 'addEventListener').mockImplementation((type, fn, opts) => {
      if (['visibilitychange', 'pageshow', 'popstate', 'snowdesk:date-changed'].includes(type)) added.push([target, type, fn]);
      return original(type, fn, opts);
    });
  }
  dateSpy = vi.spyOn(Date, 'now');
  dateSpy.mockReturnValue(1_000_000);
});

afterEach(() => {
  for (const [target, type, fn] of added) target.removeEventListener(type, fn);
  delete window.pwaVersionInfo;
  vi.restoreAllMocks();
});

describe('the notice', () => {
  it('shows the page day and a Reload when the server is ahead', async () => {
    await load();

    expect(notice().hidden).toBe(false);
    expect(notice().querySelector('[data-stale-day-label]').textContent).toBe('Montrant 18 Oct');
  });

  it('stays hidden when the page is current', async () => {
    refresh.mockResolvedValue({ today: '2026-10-18' });

    await load();

    expect(notice().hidden).toBe(true);
  });

  it('stays hidden when the poll fails', async () => {
    refresh.mockResolvedValue(null);

    await load();

    expect(notice().hidden).toBe(true);
  });

  it('stays hidden when the server sends no day', async () => {
    refresh.mockResolvedValue({ today: '' });

    await load();

    expect(notice().hidden).toBe(true);
  });

  it('stays hidden when the poll throws', async () => {
    refresh.mockRejectedValue(new Error('boom'));

    await load();

    expect(notice().hidden).toBe(true);
  });

  it('is not shown on an explicit ?d=', async () => {
    history.replaceState(null, '', '/map/?d=2026-10-10');

    await load();

    expect(notice().hidden).toBe(true);
  });

  it('does nothing without the version module', async () => {
    delete window.pwaVersionInfo;

    await load();

    expect(notice().hidden).toBe(true);
  });

  it('does not raise a label it cannot confirm over one already showing', async () => {
    await load();
    expect(notice().hidden).toBe(false);

    // A later poll that fails must leave it showing; one that agrees clears it.
    dateSpy.mockReturnValue(1_000_000 + 61_000);
    refresh.mockResolvedValue(null);
    document.dispatchEvent(new Event('visibilitychange'));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
    expect(notice().hidden).toBe(false);

    dateSpy.mockReturnValue(1_000_000 + 122_000);
    refresh.mockResolvedValue({ today: '2026-10-18' });
    document.dispatchEvent(new Event('visibilitychange'));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
    expect(notice().hidden).toBe(true);
  });
});

describe('choosing a day while the notice shows', () => {
  it('hides it when the calendar or scrubber commits a day', async () => {
    await load();
    expect(notice().hidden).toBe(false);

    // map_scrubber.js writes ?d= and THEN emits the event.
    history.replaceState(null, '', '/map/?d=2026-10-10');
    document.dispatchEvent(new CustomEvent('snowdesk:date-changed'));

    expect(notice().hidden).toBe(true);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('hides it on back/forward onto an explicit day, and shows it again off one', async () => {
    await load();

    history.replaceState(null, '', '/map/?d=2026-10-10');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(notice().hidden).toBe(true);

    history.replaceState(null, '', '/map/');
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(notice().hidden).toBe(false);
  });

  it('does nothing before a poll has answered', async () => {
    refresh.mockResolvedValue(null);
    await load();

    document.dispatchEvent(new CustomEvent('snowdesk:date-changed'));

    expect(notice().hidden).toBe(true);
  });
});

describe('when it asks', () => {
  it('asks once on load', async () => {
    await load();

    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it('asks again when the page returns to the foreground, after a minute', async () => {
    await load();
    dateSpy.mockReturnValue(1_000_000 + 61_000);

    document.dispatchEvent(new Event('visibilitychange'));
    await Promise.resolve();

    expect(refresh).toHaveBeenCalledTimes(2);
  });

  it('does not ask twice inside a minute', async () => {
    await load();
    dateSpy.mockReturnValue(1_000_000 + 10_000);

    document.dispatchEvent(new Event('visibilitychange'));
    window.dispatchEvent(new Event('pageshow'));
    await Promise.resolve();

    expect(refresh).toHaveBeenCalledTimes(1);
  });
});

describe('Reload', () => {
  it('reloads the shell from the network on a click, and only then', async () => {
    await load();
    expect(reload).not.toHaveBeenCalled();

    document.querySelector('[data-stale-day-reload]').click();

    expect(reload).toHaveBeenCalledTimes(1);
  });
});
