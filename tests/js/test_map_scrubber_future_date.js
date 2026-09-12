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

/**
 * Drag-and-release the thumb to `pct` along the track.
 *
 * The fixture's track is 100px wide (see `buildFixture`), so `clientX` is the
 * percentage. Same helper as `test_map_scrubber_no_boot_snap.js`.
 *
 * @param {number} pct
 * @returns {void}
 */
function releaseAt(pct) {
  const track = document.querySelector('.season-scrubber-track');
  track.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, clientX: pct }));
  document.dispatchEvent(new MouseEvent('pointerup', { bubbles: true }));
}

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

  it('leaves the days up to today reachable when the payload stops short', async () => {
    // The off-season shape: the archive stopped in December, today is the
    // 6th of January, and the days in between must stay reachable. The date
    // asked for is deliberately BETWEEN the two — asking for today itself
    // would prove nothing, since the boot fallback commits today whenever a
    // date is refused.
    //
    // Worth knowing what this does NOT pin. It passes even with
    // `latestKnownDate`'s "prefer the later of the two" guard removed,
    // because `ceilingMsFrom` re-clamps with `Math.max(ms, todayMs)` and
    // catches it — the second lock doing its job. So this covers the
    // scrubber's own behaviour and the depth of the defence, while the guard
    // itself is pinned a layer down (test_calendar_core.js, "prefers today
    // when the archive ends earlier") and a layer across, where there is no
    // second lock (test_map_calendar.js, "stops at today when the payload
    // reaches no further"). Removing that guard fails both of those.
    history.replaceState(null, '', '/?d=2026-01-05');

    const { commits } = await loadScrubber({ '2025-12-30': { 'CH-4115': 1 } });

    expect(lastCommit(commits)).toBe('2026-01-05');
  });
});

describe('when another country&apos;s ratings arrive', () => {
  it('re-reads the ceiling, rather than staying on the Swiss payload', async () => {
    // `getSeasonRatings` fetches `?country=ch` and nothing else
    // (map_shared.js), and `map.js` merges every other country into that same
    // cache object afterwards — adding whole new DATE KEYS, not just regions.
    // Without a re-read the ceiling is Switzerland's for the life of the
    // page, so someone following only France would never reach the day this
    // ticket is about.
    const cache = { ...RATINGS_TODAY };
    const { commits } = await loadScrubber(cache);

    // Exactly what the merge does: a new date written into the live object.
    cache[TOMORROW] = { 'FR-1234': 3 };
    document.dispatchEvent(new CustomEvent('snowdesk:country-ratings-loaded', {
      detail: { code: 'fr' },
    }));

    history.replaceState(null, '', '/?d=' + TOMORROW);
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(lastCommit(commits)).toBe(TOMORROW);
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

describe('a boot ?d= the Swiss payload cannot answer for yet', () => {
  it('is held and applied once the country that has it arrives', async () => {
    // The race is structural, not rare: `getSeasonRatings` resolves with
    // Switzerland alone, while `map.js` loads every other country the
    // basemap covers separately and unawaited. A link to a day only France
    // published therefore reaches the boot check before France's data does.
    // Refusing it there strands `?d=<tomorrow>` in the address bar over a map
    // showing today — the scrubber declining a URL it will itself write,
    // which is the defect this ticket set out to remove.
    history.replaceState(null, '', '/?d=' + TOMORROW);
    const cache = { ...RATINGS_TODAY };

    const { commits } = await loadScrubber(cache);
    // Held rather than discarded; today is what shows meanwhile.
    expect(lastCommit(commits)).toBe(TODAY);

    cache[TOMORROW] = { 'FR-1234': 3 };
    document.dispatchEvent(new CustomEvent('snowdesk:country-ratings-loaded', {
      detail: { code: 'fr' },
    }));

    expect(lastCommit(commits)).toBe(TOMORROW);
  });

  it('gives way to a day the visitor picked in the meantime', async () => {
    // The hazard the hold introduces. Someone who scrubs in the second
    // between boot and the merge has chosen a day, and a late merge applying
    // the URL's date over it would move the map under them. A non-silent
    // commit is exactly "the visitor chose one", so it retires the hold.
    history.replaceState(null, '', '/?d=' + TOMORROW);
    const cache = { ...RATINGS_TODAY };

    const { commits } = await loadScrubber(cache);
    releaseAt(50);
    const chosen = lastCommit(commits);
    expect(chosen).not.toBe(TOMORROW);

    cache[TOMORROW] = { 'FR-1234': 3 };
    document.dispatchEvent(new CustomEvent('snowdesk:country-ratings-loaded', {
      detail: { code: 'fr' },
    }));

    expect(lastCommit(commits)).toBe(chosen);
  });

  it('can be dragged to, once the merge has brought the day in', async () => {
    // `snapToNearestDataDay` reads `sortedDates`, which is
    // `Object.keys(...)` taken once when the Swiss fetch resolved. Left
    // un-rebuilt, a drag onto the newly reachable day snaps back to the
    // nearest SWISS day — so the picker would be offering a date the
    // scrubber physically could not land on.
    const cache = { ...RATINGS_TODAY };
    const { commits } = await loadScrubber(cache);

    cache[TOMORROW] = { 'FR-1234': 3 };
    document.dispatchEvent(new CustomEvent('snowdesk:country-ratings-loaded', {
      detail: { code: 'fr' },
    }));

    // The ten-day season runs 2026-01-01..2026-01-11, so the 7th — tomorrow
    // — sits at 60% along the track.
    releaseAt(60);

    expect(lastCommit(commits)).toBe(TOMORROW);
  });
});
