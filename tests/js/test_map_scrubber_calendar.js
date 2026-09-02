/*
 * tests/js/test_map_scrubber_calendar.js — the scrubber's calendar popup
 * (SNOW-792).
 *
 * The grid maths is covered in `test_calendar_core.js`; this is the DOM
 * half — what the popup renders, what it refuses to offer, and the one
 * thing that makes it worth having: that picking a day arrives at the map
 * through the scrubber's own commit point rather than a second one.
 *
 * So both modules are loaded together against a stubbed scope, and several
 * assertions below read the address bar. That is deliberate: `?d=` is where
 * the chosen day actually lives (SNOW-660), and a picker that renders a
 * selection without moving it would look right and do nothing.
 *
 * `map_scrubber.js` and `map_scrubber_calendar.js` are loaded on their own
 * rather than through the whole bundle — every cross-file binding they read
 * is stubbed below, which makes what these two surfaces actually depend on
 * legible (the idiom is `test_map_scrubber_no_boot_snap.js`'s).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/scrubber_core.js';
import '../../static/js/calendar_core.js';

// A season that starts and ends MID-MONTH, as a real one does — the
// scrubber's bounds are narrowed to the actual data (see `_base_map_context`),
// so both boundary months are half outside the window.
const SEASON_START = '2026-01-05';
const SEASON_END = '2026-03-20';
const TODAY = '2026-02-10';
const TODAY_PCT = 50;

// February has a gap in it: the 12th carries no ratings, so the map cannot
// paint it and the calendar must not offer it.
const RATED = ['2026-01-05', '2026-02-10', '2026-02-11', '2026-02-13', '2026-03-20'];
const RATINGS = Object.fromEntries(RATED.map((d) => [d, { 'CH-4115': 2 }]));

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
];

function buildFixture() {
  document.body.innerHTML = `
    <div id="season-scrubber"
         data-today="${TODAY}"
         data-today-pct="${TODAY_PCT}"
         data-season-start="${SEASON_START}"
         data-season-end="${SEASON_END}"
         data-state="ready">
      <div class="season-scrubber-track">
        <div class="season-scrubber-thumb"></div>
      </div>
      <div class="season-scrubber-loading"></div>
      <button id="scrubber-calendar-toggle" aria-expanded="false"></button>
      <div id="scrubber-calendar"
           data-state="closed"
           data-season-start="${SEASON_START}"
           data-season-end="${SEASON_END}"
           data-today="${TODAY}"
           data-months="${MONTHS.join('|')}"
           hidden>
        <div class="scrubber-calendar-header">
          <button id="scrubber-calendar-prev"></button>
          <span id="scrubber-calendar-month"></span>
          <button id="scrubber-calendar-next"></button>
        </div>
        <div id="scrubber-calendar-grid"></div>
      </div>
    </div>`;
  const track = document.querySelector('.season-scrubber-track');
  track.getBoundingClientRect = () => ({
    left: 0, top: 0, right: 100, bottom: 10, width: 100, height: 10, x: 0, y: 0,
  });
}

/**
 * Load the scrubber and its calendar against a stubbed scope.
 *
 * @returns {Promise<{registered: Object, commits: Array<Object>}>}
 *   The overlay-registry entry this surface registered, and every
 *   `snowdesk:date-changed` detail the scrubber announced.
 */
async function loadCalendar() {
  globalThis.COUNTRY_STATE = { ch: true, fr: false, at: false, it: false };
  globalThis.MAP_READY_PROMISE = Promise.resolve();
  globalThis.MAP_STRINGS = { 'season-unavailable': 'Season data unavailable' };
  globalThis.getSeasonRatings = () => Promise.resolve(RATINGS);
  globalThis.readUrlDateParam = () => {
    const d = new URL(location.href).searchParams.get('d');
    return d && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
  };
  globalThis.repaintRegionsForDate = () => {};
  globalThis.writeUrlDateParam = (dateKey) => {
    history.replaceState(null, '', location.pathname + '?d=' + dateKey + location.hash);
  };

  const registry = {};
  window.pwaMapOverlays = {
    register: (name, controller) => { registry[name] = controller; },
    opening: vi.fn(),
  };

  // Every `snowdesk:date-changed` this test's scrubber announces — plus one
  // per scrubber loaded by an EARLIER test in this file, because
  // `vi.resetModules()` re-imports the module and its
  // `document.addEventListener('snowdesk:scrub-to', …)` outlives the DOM the
  // fixture replaces. That is an artefact of re-importing a parse-time IIFE,
  // not of the surface (a browser loads it once), so the assertions below
  // read the LAST commit and the address bar rather than the array's length.
  const commits = [];
  document.addEventListener('snowdesk:date-changed', (e) => { commits.push(e.detail); });

  vi.resetModules();
  await import('../../static/js/map_scrubber.js');
  await import('../../static/js/map_scrubber_calendar.js');
  // Both modules resolve the ratings fetch in a microtask.
  await new Promise((r) => setTimeout(r, 20));
  return { registered: registry['scrubber-calendar'], commits };
}

/** Open the popup by pressing its toggle. */
function openPopup() {
  document.getElementById('scrubber-calendar-toggle').click();
}

/** Every day button currently in the grid. */
function dayButtons() {
  return [...document.querySelectorAll('#scrubber-calendar-grid button[data-date]')];
}

/** The day button for one date key, or undefined. */
function dayButton(dateKey) {
  return dayButtons().find((b) => b.dataset.date === dateKey);
}

/** The header's month label. */
function monthLabel() {
  return document.getElementById('scrubber-calendar-month').textContent;
}

beforeEach(() => {
  history.replaceState(null, '', '/');
  buildFixture();
});

afterEach(() => {
  document.body.innerHTML = '';
  history.replaceState(null, '', '/');
  delete window.pwaMapOverlays;
  delete globalThis.COUNTRY_STATE;
  delete globalThis.MAP_READY_PROMISE;
  delete globalThis.MAP_STRINGS;
  delete globalThis.getSeasonRatings;
  delete globalThis.readUrlDateParam;
  delete globalThis.repaintRegionsForDate;
  delete globalThis.writeUrlDateParam;
});

describe('opening and closing', () => {
  it('starts shut', async () => {
    await loadCalendar();

    expect(document.getElementById('scrubber-calendar').hidden).toBe(true);
  });

  it('opens on the toggle and reports it on the toggle', async () => {
    await loadCalendar();

    openPopup();

    expect(document.getElementById('scrubber-calendar').hidden).toBe(false);
    expect(document.getElementById('scrubber-calendar-toggle').getAttribute('aria-expanded')).toBe('true');
  });

  it('closes on a second press', async () => {
    await loadCalendar();

    openPopup();
    openPopup();

    expect(document.getElementById('scrubber-calendar').hidden).toBe(true);
  });

  it('closes on a click away, but not on a click inside it', async () => {
    await loadCalendar();
    openPopup();

    document.getElementById('scrubber-calendar-month').dispatchEvent(
      new MouseEvent('click', { bubbles: true })
    );
    expect(document.getElementById('scrubber-calendar').hidden).toBe(false);

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(document.getElementById('scrubber-calendar').hidden).toBe(true);
  });

  it('closes on Escape', async () => {
    await loadCalendar();
    openPopup();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    expect(document.getElementById('scrubber-calendar').hidden).toBe(true);
  });

  it('lifts the pill while it is open, so it clears the bottom-right stack', async () => {
    // The popup is a child of #season-scrubber and cannot escape its
    // stacking context; the CSS keys off this attribute. Asserted here
    // because the symptom of losing it — a popup drawn UNDER the roundels —
    // is invisible to every other test in this file.
    await loadCalendar();
    const scrubber = document.getElementById('season-scrubber');

    openPopup();
    expect(scrubber.dataset.calendar).toBe('open');

    openPopup();
    expect(scrubber.dataset.calendar).toBeUndefined();
  });

  it('registers with the map-overlay registry', async () => {
    const { registered } = await loadCalendar();

    expect(registered).toBeTruthy();
    expect(registered.isOpen()).toBe(false);
    openPopup();
    expect(registered.isOpen()).toBe(true);
    registered.close();
    expect(document.getElementById('scrubber-calendar').hidden).toBe(true);
  });

  it('announces itself to the registry before it is revealed', async () => {
    await loadCalendar();

    openPopup();

    expect(window.pwaMapOverlays.opening).toHaveBeenCalledWith('scrubber-calendar');
  });
});

describe('the month it opens on', () => {
  it("opens on today's month when no day has been chosen", async () => {
    await loadCalendar();

    openPopup();

    expect(monthLabel()).toBe('February 2026');
  });

  it('opens on the month of the day currently showing', async () => {
    history.replaceState(null, '', '/?d=2026-03-20');
    await loadCalendar();

    openPopup();

    expect(monthLabel()).toBe('March 2026');
  });

  it('marks the day currently showing', async () => {
    history.replaceState(null, '', '/?d=2026-02-11');
    await loadCalendar();

    openPopup();

    expect(dayButton('2026-02-11').classList.contains('is-selected')).toBe(true);
    expect(dayButton('2026-02-13').classList.contains('is-selected')).toBe(false);
  });
});

describe('which days it offers', () => {
  it('disables an in-season day the ratings cache has no frame for', async () => {
    // The imprecision this control exists to fix: a click that lands on a
    // day the map cannot paint would silently end up somewhere else.
    await loadCalendar();
    openPopup();

    expect(dayButton('2026-02-11').disabled).toBe(false);
    expect(dayButton('2026-02-12').disabled).toBe(true);
  });

  it('disables the days before the season starts in its first month', async () => {
    history.replaceState(null, '', '/?d=2026-01-05');
    await loadCalendar();
    openPopup();

    expect(dayButton('2026-01-04').disabled).toBe(true);
    expect(dayButton('2026-01-05').disabled).toBe(false);
  });
});

describe('month navigation', () => {
  it('steps back and forward', async () => {
    await loadCalendar();
    openPopup();

    document.getElementById('scrubber-calendar-prev').click();
    expect(monthLabel()).toBe('January 2026');

    document.getElementById('scrubber-calendar-next').click();
    expect(monthLabel()).toBe('February 2026');
  });

  it('stops at the season bounds rather than walking into an empty month', async () => {
    await loadCalendar();
    openPopup();

    document.getElementById('scrubber-calendar-prev').click();
    expect(document.getElementById('scrubber-calendar-prev').disabled).toBe(true);
    document.getElementById('scrubber-calendar-prev').click();
    expect(monthLabel()).toBe('January 2026');

    document.getElementById('scrubber-calendar-next').click();
    document.getElementById('scrubber-calendar-next').click();
    expect(monthLabel()).toBe('March 2026');
    expect(document.getElementById('scrubber-calendar-next').disabled).toBe(true);
  });
});

describe('picking a day', () => {
  it('commits it through the scrubber, so the URL and the paint move together', async () => {
    const { commits } = await loadCalendar();
    openPopup();

    dayButton('2026-02-13').click();

    expect(commits.at(-1).date).toBe('2026-02-13');
    // `scrubber`, not `calendar`: the popup dispatches `snowdesk:scrub-to`
    // and the scrubber's own `commitDate` is what announces the change, so
    // there is exactly one place a date is applied.
    expect(commits.at(-1).source).toBe('scrubber');
    expect(location.search).toBe('?d=2026-02-13');
  });

  it('moves the scrubber thumb to the day it picked', async () => {
    await loadCalendar();
    openPopup();
    document.getElementById('scrubber-calendar-prev').click();

    dayButton('2026-01-05').click();

    // Season start — 0% along the track.
    expect(document.querySelector('.season-scrubber-thumb').style.left).toBe('0%');
  });

  it('closes once a day is picked', async () => {
    await loadCalendar();
    openPopup();

    dayButton('2026-02-13').click();

    expect(document.getElementById('scrubber-calendar').hidden).toBe(true);
  });

  it('does nothing when the day cannot be painted', async () => {
    const { commits } = await loadCalendar();
    openPopup();

    dayButton('2026-02-12').click();

    // Nothing at all was announced — a disabled day dispatches no event, so
    // the stale-instance caveat above does not apply here.
    expect(commits).toEqual([]);
    expect(location.search).toBe('');
    expect(document.getElementById('scrubber-calendar').hidden).toBe(false);
  });
});

describe('the date moving underneath it', () => {
  it('follows a scrub while open, so the marker never lies', async () => {
    await loadCalendar();
    openPopup();

    history.replaceState(null, '', '/?d=2026-02-11');
    document.dispatchEvent(
      new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-02-11', source: 'scrubber' } })
    );

    expect(dayButton('2026-02-11').classList.contains('is-selected')).toBe(true);
  });
});
