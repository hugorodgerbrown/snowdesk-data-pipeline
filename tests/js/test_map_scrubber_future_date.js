/*
 * tests/js/test_map_scrubber_future_date.js — the scrubber's boot path can
 * reach tomorrow, once tomorrow has been published (SNOW-927).
 *
 * The map's selectable range was data-driven at the floor and wall-clock at
 * the ceiling: the ratings payload is the whole archive so the calendar pages
 * back years, while `isSelectableDate` refused anything past `Date.now()`.
 * Providers publish twice a day and the evening issue forecasts the NEXT day
 * (`target_day_for_valid_from`), so from about 16:00 the payload carries a day
 * the UI would not open — the one a person packing for the morning wants.
 *
 * Two things make this worth a file of its own rather than another case in
 * `test_map_scrubber_no_boot_snap.js`, which is a guard and stays untouched:
 *
 *   1. Boot asks the selectability question TWICE, against two ceilings. The
 *      `?d=` test runs synchronously — the thumb and `aria-valuenow` have to
 *      be positioned before the ratings fetch resolves — so it accepts
 *      optimistically as far as the ceiling could possibly move, and the
 *      deferred block re-asks once the payload has actually arrived. Both
 *      phases need covering, and the second one is only observable as a
 *      commit that lands on today after appearing to accept tomorrow.
 *   2. The ceiling is capped a few days out, so a mis-dated provider row
 *      widens the range by days rather than years.
 *
 * The ceiling is a RANGE BOUND and never a default. Boot with no `?d=` still
 * announces today and nothing else — that is SNOW-793's rule and
 * `test_map_scrubber_no_boot_snap.js` owns it.
 *
 * `map_scrubber.js` is loaded on its own against a stubbed scope, the idiom
 * that file established.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/scrubber_core.js';
import '../../static/js/calendar_core.js';

// A ten-day season with today halfway along it, so tomorrow is comfortably
// inside the track and has a real thumb position.
const SEASON_START = '2026-01-01';
const SEASON_END = '2026-01-11';
const TODAY = '2026-01-06';
const TODAY_PCT = 50;
const TOMORROW = '2026-01-07';
// Past the MAX_FORWARD_DAYS cap (today + 3 = 2026-01-09), so it is refused
// by the synchronous phase and never reaches the deferred one.
const WAY_AHEAD = '2026-02-15';

/** Today only — the payload before the evening issue lands. */
const RATINGS_TODAY = { [TODAY]: { 'CH-4115': 2 } };
/** Today and tomorrow — the payload from about 16:00. */
const RATINGS_TOMORROW = {
  [TODAY]: { 'CH-4115': 2 },
  [TOMORROW]: { 'CH-4115': 3 },
};

function buildFixture() {
  document.body.innerHTML = `
    <div id="season-scrubber"
         data-today="${TODAY}"
         data-today-pct="${TODAY_PCT}"
         data-season-start="${SEASON_START}"
         data-season-end="${SEASON_END}"
         data-state="loading">
      <div class="season-scrubber-track">
        <div class="season-scrubber-thumb"></div>
      </div>
      <div class="season-scrubber-loading"></div>
    </div>`;
  const track = document.querySelector('.season-scrubber-track');
  track.getBoundingClientRect = () => ({
    left: 0, top: 0, right: 100, bottom: 10, width: 100, height: 10, x: 0, y: 0,
  });
}

/**
 * Load the scrubber against a stubbed scope with the ratings fetch resolved.
 *
 * @param {Object} ratings The season-ratings payload to resolve with.
 * @returns {Promise<{commits: Array<Object>}>} Every `snowdesk:date-changed`
 *   detail the scrubber announced, oldest first.
 */
async function loadScrubber(ratings) {
  globalThis.COUNTRY_STATE = { ch: true, fr: false, at: false, it: false };
  globalThis.MAP_READY_PROMISE = Promise.resolve();
  globalThis.MAP_STRINGS = { 'season-unavailable': 'Season data unavailable' };
  globalThis.getSeasonRatings = () => Promise.resolve(ratings);
  globalThis.readUrlDateParam = () => {
    const d = new URL(location.href).searchParams.get('d');
    return d && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
  };
  globalThis.repaintRegionsForDate = () => {};
  globalThis.writeUrlDateParam = (dateKey) => {
    history.replaceState(null, '', location.pathname + '?d=' + dateKey + location.hash);
  };

  const commits = [];
  document.addEventListener('snowdesk:date-changed', (e) => { commits.push(e.detail); });

  vi.resetModules();
  await import('../../static/js/map_scrubber.js');
  // Both the ratings fetch and the deferred boot commit settle in
  // microtasks, so the second phase has run by the time this resolves.
  await new Promise((r) => setTimeout(r, 20));
  return { commits };
}

/** The date on the last commit the scrubber announced. */
const lastCommit = (commits) => commits[commits.length - 1].date;

beforeEach(() => {
  history.replaceState(null, '', '/');
  buildFixture();
});

afterEach(() => {
  document.body.innerHTML = '';
  history.replaceState(null, '', '/');
  delete globalThis.COUNTRY_STATE;
  delete globalThis.MAP_READY_PROMISE;
  delete globalThis.MAP_STRINGS;
  delete globalThis.getSeasonRatings;
  delete globalThis.readUrlDateParam;
  delete globalThis.repaintRegionsForDate;
  delete globalThis.writeUrlDateParam;
});

describe('booting on ?d=<tomorrow>', () => {
  it('commits tomorrow when the payload carries it', async () => {
    // The ticket, end to end at this layer. Before SNOW-927 this URL was
    // refused and the map fell back to today — which mattered because a link
    // shared at 5pm for the morning is the main way anyone reaches this day,
    // and a cold load is the only kind such a link gets.
    history.replaceState(null, '', '/?d=' + TOMORROW);

    const { commits } = await loadScrubber(RATINGS_TOMORROW);

    expect(lastCommit(commits)).toBe(TOMORROW);
  });

  it('falls back to today when the payload does not carry it', async () => {
    // Phase two doing its job. The synchronous test accepted this date
    // because it could not yet know better; the deferred one has the payload
    // and finds the day was never reachable, so the commit lands on today and
    // the thumb moves back with it.
    history.replaceState(null, '', '/?d=' + TOMORROW);

    const { commits } = await loadScrubber(RATINGS_TODAY);

    expect(lastCommit(commits)).toBe(TODAY);
  });

  it('refuses a date past the cap outright', async () => {
    // Phase one, which is the only phase this reaches: beyond
    // MAX_FORWARD_DAYS the date cannot become reachable however the payload
    // resolves, so there is nothing to defer and boot takes its ordinary
    // no-valid-?d= path.
    history.replaceState(null, '', '/?d=' + WAY_AHEAD);

    const { commits } = await loadScrubber(RATINGS_TOMORROW);

    expect(lastCommit(commits)).toBe(TODAY);
  });

  it('leaves today reachable when the payload stops short of it', async () => {
    // The off-season shape, and the regression that would matter most: a
    // ceiling taken naively from the data would sit BELOW today and take
    // today off the range entirely. `latestKnownDate` returns the later of
    // the two, so today still commits.
    history.replaceState(null, '', '/?d=' + TODAY);

    const { commits } = await loadScrubber({ '2025-12-30': { 'CH-4115': 1 } });

    expect(lastCommit(commits)).toBe(TODAY);
  });
});

describe('stepping back onto ?d=<tomorrow>', () => {
  it('honours it, rather than dropping to today', async () => {
    // popstate shares the selectability test but not boot's two phases: it
    // commits synchronously, and by the time anyone can press back the
    // payload has resolved. A URL the scrubber wrote must survive the button
    // that returns to it.
    const { commits } = await loadScrubber(RATINGS_TOMORROW);

    history.replaceState(null, '', '/?d=' + TOMORROW);
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(lastCommit(commits)).toBe(TOMORROW);
  });
});
