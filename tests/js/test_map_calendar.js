/*
 * tests/js/test_map_calendar.js — the map's date picker (SNOW-792).
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
 * `map_scrubber.js` and `map_calendar.js` are loaded on their own
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
// Today is TWO MONTHS PAST the season end — the off-season case, which is
// when the map has only weather to show and when a picker fenced to the
// season could not reach the day the visitor is actually looking at.
const TODAY = '2026-05-14';
const TODAY_PCT = 100;

// February has a gap in it: the 12th carries no ratings. The grid does not
// mark that at all — the day is offered like any other.
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
      <button id="map-calendar-toggle" aria-expanded="false"></button>
    </div>
    <div id="map-calendar"
           data-state="closed"
           data-season-start="${SEASON_START}"
           data-season-end="${SEASON_END}"
           data-today="${TODAY}"
           data-months="${MONTHS.join('|')}"
           hidden>
        <div class="map-calendar-header">
          <button id="map-calendar-prev"></button>
          <span id="map-calendar-month"></span>
          <button id="map-calendar-next"></button>
        </div>
        <div id="map-calendar-grid"></div>
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
  await import('../../static/js/map_calendar.js');
  // Both modules resolve the ratings fetch in a microtask.
  await new Promise((r) => setTimeout(r, 20));
  return { registered: registry['map-calendar'], commits };
}

/** Open the popup by pressing its toggle. */
function openPopup() {
  document.getElementById('map-calendar-toggle').click();
}

/** Every day button currently in the grid. */
function dayButtons() {
  return [...document.querySelectorAll('#map-calendar-grid button[data-date]')];
}

/** The day button for one date key, or undefined. */
function dayButton(dateKey) {
  return dayButtons().find((b) => b.dataset.date === dateKey);
}

/** The header's month label. */
function monthLabel() {
  return document.getElementById('map-calendar-month').textContent;
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

    expect(document.getElementById('map-calendar').hidden).toBe(true);
  });

  it('opens on the toggle and reports it on the toggle', async () => {
    await loadCalendar();

    openPopup();

    expect(document.getElementById('map-calendar').hidden).toBe(false);
    expect(document.getElementById('map-calendar-toggle').getAttribute('aria-expanded')).toBe('true');
  });

  it('closes on a second press', async () => {
    await loadCalendar();

    openPopup();
    openPopup();

    expect(document.getElementById('map-calendar').hidden).toBe(true);
  });

  it('closes on a click away, but not on a click inside it', async () => {
    await loadCalendar();
    openPopup();

    document.getElementById('map-calendar-month').dispatchEvent(
      new MouseEvent('click', { bubbles: true })
    );
    expect(document.getElementById('map-calendar').hidden).toBe(false);

    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(document.getElementById('map-calendar').hidden).toBe(true);
  });

  it('closes on Escape', async () => {
    await loadCalendar();
    openPopup();

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));

    expect(document.getElementById('map-calendar').hidden).toBe(true);
  });

  it('registers with the map-overlay registry', async () => {
    const { registered } = await loadCalendar();

    expect(registered).toBeTruthy();
    expect(registered.isOpen()).toBe(false);
    openPopup();
    expect(registered.isOpen()).toBe(true);
    registered.close();
    expect(document.getElementById('map-calendar').hidden).toBe(true);
  });

  it('announces itself to the registry before it is revealed', async () => {
    await loadCalendar();

    openPopup();

    expect(window.pwaMapOverlays.opening).toHaveBeenCalledWith('map-calendar');
  });
});

describe('the month it opens on', () => {
  it("opens on today's month when no day has been chosen", async () => {
    // Today is out of season, and it still opens there — the picker's home
    // is the present, not the last month a bulletin was issued in.
    await loadCalendar();

    openPopup();

    expect(monthLabel()).toBe('May 2026');
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
  it('refuses the future, and only the future', async () => {
    // The one hard stop: nothing on the map can answer for a day that has
    // not happened.
    await loadCalendar();
    openPopup();

    expect(dayButton(TODAY).disabled).toBe(false);
    expect(dayButton('2026-05-15').disabled).toBe(true);
    expect(dayButton('2026-05-31').disabled).toBe(true);
  });

  it('offers days outside the season, because the map has weather for them', async () => {
    // April is past the season end and before today. Every day in it is
    // pickable, and none of them is marked in-season.
    history.replaceState(null, '', '/?d=2026-03-20');
    await loadCalendar();
    openPopup();
    document.getElementById('map-calendar-next').click();

    expect(monthLabel()).toBe('April 2026');
    const days = dayButtons();
    expect(days.length).toBe(30);
    expect(days.every((b) => !b.disabled)).toBe(true);
    expect(days.every((b) => !b.classList.contains('in-season'))).toBe(true);
    // The season tint is the only per-day mark there is.
    expect(days.every((b) => b.className === 'map-calendar-day')).toBe(true);
  });

  it('offers an in-season day with no bulletin, and does not mark it out', async () => {
    // A gap in the archive is an operator's concern; a visitor who lands on
    // 12 February sees an uncoloured map, which says it once. The grid
    // carries no per-day mark distinguishing it from the 11th.
    history.replaceState(null, '', '/?d=2026-02-11');
    await loadCalendar();
    openPopup();

    // The 13th, not the 11th: the 11th is the day being shown and so
    // carries `is-selected`, which would make the two differ for a reason
    // that has nothing to do with bulletins.
    const gap = dayButton('2026-02-12');
    const rated = dayButton('2026-02-13');
    expect(gap.disabled).toBe(false);
    expect(gap.className).toBe(rated.className);
  });

  it('highlights the season without fencing it', async () => {
    // The season closes on 20 March. The 21st loses the highlight and keeps
    // the click — which is the whole distinction between the two.
    //
    // Asserted at the season's END rather than its start because the paging
    // floor and the season start coincide in this fixture, so the day before
    // the season is out of RANGE and would prove nothing about the season.
    history.replaceState(null, '', '/?d=2026-03-20');
    await loadCalendar();
    openPopup();

    expect(dayButton('2026-03-20').classList.contains('in-season')).toBe(true);
    expect(dayButton('2026-03-21').classList.contains('in-season')).toBe(false);
    expect(dayButton('2026-03-21').disabled).toBe(false);
  });
});

describe('month navigation', () => {
  it('steps back and forward', async () => {
    history.replaceState(null, '', '/?d=2026-02-11');
    await loadCalendar();
    openPopup();

    document.getElementById('map-calendar-prev').click();
    expect(monthLabel()).toBe('January 2026');

    document.getElementById('map-calendar-next').click();
    expect(monthLabel()).toBe('February 2026');
  });

  it('reaches every month between the season end and today', async () => {
    // The season ends in March and today is in May. April is the month a
    // season-fenced picker could not reach at all.
    history.replaceState(null, '', '/?d=2026-01-05');
    await loadCalendar();
    openPopup();

    const seen = [monthLabel()];
    for (let i = 0; i < 4; i++) {
      document.getElementById('map-calendar-next').click();
      seen.push(monthLabel());
    }
    expect(seen).toEqual([
      'January 2026', 'February 2026', 'March 2026', 'April 2026', 'May 2026',
    ]);
  });

  it('stops at today going forward and at the archive floor going back', async () => {
    await loadCalendar();
    openPopup();

    // Opens on today's month, so forward is already spent.
    expect(document.getElementById('map-calendar-next').disabled).toBe(true);
    document.getElementById('map-calendar-next').click();
    expect(monthLabel()).toBe('May 2026');

    // The floor is the earliest rated day (2026-01-05), not the year dot.
    for (let i = 0; i < 6; i++) document.getElementById('map-calendar-prev').click();
    expect(monthLabel()).toBe('January 2026');
    expect(document.getElementById('map-calendar-prev').disabled).toBe(true);
  });
});

describe('picking a day', () => {
  it('commits it through the scrubber, so the URL and the paint move together', async () => {
    history.replaceState(null, '', '/?d=2026-02-11');
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
    history.replaceState(null, '', '/?d=2026-01-20');
    await loadCalendar();
    openPopup();

    dayButton('2026-01-05').click();

    // Season start — 0% along the track.
    expect(document.querySelector('.season-scrubber-thumb').style.left).toBe('0%');
  });

  it('closes once a day is picked', async () => {
    history.replaceState(null, '', '/?d=2026-02-11');
    await loadCalendar();
    openPopup();

    dayButton('2026-02-13').click();

    expect(document.getElementById('map-calendar').hidden).toBe(true);
  });

  it('does nothing when the day is in the future', async () => {
    const { commits } = await loadCalendar();
    openPopup();
    // SNOW-793: boot commits today when the URL names no day, so `commits`
    // is not empty by the time the popup opens. What "does nothing" means
    // is that the CLICK adds nothing — a disabled day dispatches no event —
    // so the baseline is taken here rather than assumed to be zero. The
    // stale-instance caveat above still does not apply: nothing is
    // dispatched at all, so there is no second instance to go stale.
    const committedBeforeClick = commits.length;

    dayButton('2026-05-20').click();

    expect(commits.length).toBe(committedBeforeClick);
    // Unchanged by SNOW-793, and worth keeping for its own reason: the boot
    // commit is silent, so a bare URL stays bare. A future day must not
    // write one either.
    expect(location.search).toBe('');
    expect(document.getElementById('map-calendar').hidden).toBe(false);
  });

  it('commits a day outside the season, thumb clamped to the track end', async () => {
    // The scrubber's guard on the season window is gone: an out-of-season
    // date parks the thumb at whichever end it is past, which is the honest
    // reading of "the season ran out" rather than a refusal.
    const { commits } = await loadCalendar();
    openPopup();

    dayButton('2026-05-01').click();

    expect(commits.at(-1).date).toBe('2026-05-01');
    expect(location.search).toBe('?d=2026-05-01');
    expect(document.querySelector('.season-scrubber-thumb').style.left).toBe('100%');
  });
});

describe('the date moving underneath it', () => {
  it('follows a scrub while open, so the marker never lies', async () => {
    history.replaceState(null, '', '/?d=2026-02-10');
    await loadCalendar();
    openPopup();

    history.replaceState(null, '', '/?d=2026-02-11');
    document.dispatchEvent(
      new CustomEvent('snowdesk:date-changed', { detail: { date: '2026-02-11', source: 'scrubber' } })
    );

    expect(dayButton('2026-02-11').classList.contains('is-selected')).toBe(true);
  });
});
