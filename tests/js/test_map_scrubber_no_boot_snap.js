/*
 * tests/js/test_map_scrubber_no_boot_snap.js — the scrubber commits no date
 * the visitor did not ask for (SNOW-660).
 *
 * The season-ratings fetch used to end in a silent `commitDate(effective,
 * {silent: true})` whenever the URL carried no `?d=` — literally "the map
 * seeds the last date carrying ratings". That one call is what painted a day
 * nobody had chosen onto a cold boot: the choropleth coloured itself for a
 * date the visitor never named and nothing on screen reported, which off
 * season meant a single coloured polygon and no explanation.
 *
 * The commit is gone; the derived date stays as the snap target for a
 * release without a drag, because a release IS a user action.
 *
 * SNOW-793 then gave boot a default again — TODAY, and the distinction from
 * LAST_RATED below is the entire point of this file. `effectiveTodayKey` is
 * a day the map derives from its own data; today is a day the visitor can
 * be told, and #map-date-ribbon tells them. So the rule is now: boot
 * announces TODAY and writes nothing, a real interaction announces and
 * writes normally — including for today, which still has to write `?d=`
 * because a chosen day must survive a reload even when it happens to be the
 * default — and a back step onto a dateless URL lands on today too, because
 * that URL means today whichever way it is reached.
 *
 * LAST_RATED must never be committed by any of them. That is the assertion
 * this file was built for and it is unchanged.
 *
 * `map_scrubber.js` is loaded on its own against a stubbed scope rather than
 * through the whole bundle: every binding it reads across files is listed
 * below, which makes what this surface actually depends on legible.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/scrubber_core.js';

// A ten-day season with today exactly halfway along it, so a pointer at 50%
// of the track lands on today — the date decision 1 is about.
const SEASON_START = '2026-01-01';
const SEASON_END = '2026-01-11';
const TODAY = '2026-01-06';
const TODAY_PCT = 50;
// The last day carrying ratings, and deliberately NOT today: a boot-time
// snap would commit this one, so its absence from the URL is the assertion.
const LAST_RATED = '2026-01-09';
// 2026-01-09 is day 8 of the ten-day span, so the thumb sits at 80% — and
// with a 100px track (see buildFixture) that is also the pointer x. Used to
// leave a day OTHER than today committed before a back step, so "landed on
// today" cannot be satisfied by simply never having moved.
const LAST_RATED_PCT = 80;

/** Two rated days, both inside the season window. */
const RATINGS = {
  [TODAY]: { 'CH-4115': 2 },
  [LAST_RATED]: { 'CH-4115': 3 },
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
  // jsdom gives every element a zero-sized box, which would make the
  // drag maths divide by zero. A 100px track means clientX IS the
  // percentage, so the pointer position below reads as the date it picks.
  const track = document.querySelector('.season-scrubber-track');
  track.getBoundingClientRect = () => ({
    left: 0, top: 0, right: 100, bottom: 10, width: 100, height: 10, x: 0, y: 0,
  });
}

/**
 * Load the scrubber against a stubbed scope, with the season-ratings fetch
 * already resolved.
 *
 * @param {Function} [seasonRatings] Stands in for `getSeasonRatings`.
 *   Defaults to a resolved two-day season.
 * @returns {Promise<{commits: Array<Object>, repaints: Array<?string>}>}
 *   Every `snowdesk:date-changed` detail and every repaint's date key.
 */
async function loadScrubber(seasonRatings) {
  const repaints = [];
  globalThis.COUNTRY_STATE = { ch: true, fr: false, at: false, it: false };
  globalThis.MAP_READY_PROMISE = Promise.resolve();
  globalThis.MAP_STRINGS = { 'season-unavailable': 'Season data unavailable' };
  // Overridable so a test can make the season fetch hang or fail — the
  // scrubber pulls it at init to leave its loading state, and the SNOW-793
  // default must not be hostage to it.
  globalThis.getSeasonRatings = seasonRatings || (() => Promise.resolve(RATINGS));
  globalThis.readUrlDateParam = () => {
    const d = new URL(location.href).searchParams.get('d');
    return d && /^\d{4}-\d{2}-\d{2}$/.test(d) ? d : null;
  };
  globalThis.repaintRegionsForDate = (dateKey) => { repaints.push(dateKey); };
  // The shared URL writer (map_shared.js). Stubbed with the same
  // replaceState it performs, so the assertions below read the address bar
  // rather than a spy — what a visitor's reload sees is the point.
  globalThis.writeUrlDateParam = (dateKey) => {
    history.replaceState(null, '', location.pathname + '?d=' + dateKey + location.hash);
  };

  const commits = [];
  document.addEventListener('snowdesk:date-changed', (e) => { commits.push(e.detail); });

  vi.resetModules();
  await import('../../static/js/map_scrubber.js');
  // The ratings fetch resolves in a microtask; anything the module was going
  // to commit off the back of it has happened by the time this settles.
  await new Promise((r) => setTimeout(r, 20));
  return { commits, repaints };
}

/** Drag-and-release the thumb to `pct` along the track. */
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
  delete globalThis.readDisplayDate;
  delete globalThis.repaintRegionsForDate;
  delete globalThis.writeUrlDateParam;
});

describe('boot with no ?d=', () => {
  it('announces today, and specifically not the last rated day', async () => {
    // SNOW-793. Both halves matter: the first is the feature, the second is
    // SNOW-660's bug, and a boot that reached for `effectiveTodayKey` would
    // satisfy neither this nor the ribbon that has to name what is painted.
    const { commits } = await loadScrubber();

    expect(commits).toEqual([{ date: TODAY, source: 'scrubber' }]);
    expect(commits.map((c) => c.date)).not.toContain(LAST_RATED);
  });

  it('leaves the URL exactly as the visitor arrived at it', async () => {
    // The default is not a choice, so it is not written. A bare URL means
    // today; writing `?d=` here would pin a shared link to the day it was
    // copied.
    await loadScrubber();

    expect(location.search).toBe('');
  });

  it('announces today even if the season ratings never arrive', async () => {
    // The `?d=` branch defers its commit until the season payload resolves,
    // because it repaints from it. The default must NOT: its whole output is
    // the thumb and the announcement that puts today into #map-date-ribbon,
    // and map.js's own single-date boot leg paints the choropleth
    // separately. Deferring would leave the ribbon reading "No date
    // selected" over an already-coloured map for the length of that fetch —
    // and, on a fetch that never settles, permanently.
    const { commits } = await loadScrubber(() => new Promise(() => {}));

    expect(commits).toEqual([{ date: TODAY, source: 'scrubber' }]);
  });

  it('announces today even if the season ratings fail outright', async () => {
    // Same property at the other failure mode. The scrubber goes to its
    // error state here (SNOW-234) and the map has no colours to show, but
    // the date readout is still answerable and must still be answered.
    const { commits } = await loadScrubber(() => Promise.reject(new Error('nope')));

    expect(commits).toEqual([{ date: TODAY, source: 'scrubber' }]);
    expect(document.getElementById('season-scrubber').dataset.state).toBe('error');
  });

  it('repaints nothing itself, leaving the boot frame to map.js', async () => {
    // The division of labour, pinned. `commitDate`'s repaint is a no-op
    // while `ratingsCache` is null, which on a cold boot it is — so the
    // colours on screen are map.js's single-date boot frame, and there is
    // no second, later paint from the season payload racing it.
    const { repaints } = await loadScrubber();

    expect(repaints).toEqual([]);
  });

  it('still reaches the ready state, so the transport controls appear', async () => {
    // The snap was removed from the ratings callback, not the callback
    // itself: the scrubber must still leave its loading placeholder.
    await loadScrubber();

    expect(document.getElementById('season-scrubber').dataset.state).toBe('ready');
  });
});

describe('boot with no ?d= and no readable data-today', () => {
  it('announces nothing, leaving SNOW-660s uncoloured map', async () => {
    // The fallback. With no day knowable the honest answer is still no day
    // — the alternative is the map deriving one from its own data, which is
    // what both tickets exist to prevent.
    document.getElementById('season-scrubber').removeAttribute('data-today');

    const { commits, repaints } = await loadScrubber();

    expect(commits).toEqual([]);
    expect(repaints).toEqual([]);
  });
});

describe('a commit the visitor makes', () => {
  it('writes ?d= even when the day is today', async () => {
    // Decision 1. `?d=` used to be stripped for today, on the reasoning that
    // the bare URL was canonical for "now" — but bare now means "no day
    // asked for", so stripping it would make a reload blank the map the
    // visitor had just chosen to look at.
    const { commits } = await loadScrubber();

    releaseAt(TODAY_PCT);

    expect(new URLSearchParams(location.search).get('d')).toBe(TODAY);
    expect(commits.at(-1)).toEqual({ date: TODAY, source: 'scrubber' });
  });

  it('snaps to the nearest rated day rather than the raw pointer date', async () => {
    // Releasing at the far end of the track picks the season's last DAY
    // (2026-01-11), which carries no ratings; the commit lands on the last
    // rated one instead. Ratings-aware snapping is what the removed boot
    // commit was impersonating — it is still here, still doing its job, and
    // now only ever in answer to an interaction.
    const { commits } = await loadScrubber();

    releaseAt(100);

    expect(commits.at(-1).date).toBe(LAST_RATED);
  });
});

describe('a back step onto a URL with no ?d=', () => {
  it('lands on today, the same day a fresh load of that URL shows', async () => {
    // The original handler fell back to the effective last date here, which
    // reached the invented-date bug by a different route: navigating back
    // OUT of a chosen day landed on a day the map picked itself. SNOW-660
    // answered that with the unchosen state; SNOW-793 answers it with
    // today, which keeps the stronger property — one URL, one map. Two
    // routes to the bare URL rendering two different maps is its own bug.
    const { commits } = await loadScrubber();
    releaseAt(LAST_RATED_PCT);

    history.replaceState(null, '', '/');
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(commits.at(-1)).toEqual({ date: TODAY, source: 'scrubber' });
  });

  it('repaints the choropleth for today rather than clearing it', async () => {
    // Asserted separately from the announcement because a commit without a
    // repaint would leave the previous day's colours on screen under a
    // ribbon reading today — the two disagreeing is worse than either.
    const { commits, repaints } = await loadScrubber();
    releaseAt(LAST_RATED_PCT);

    history.replaceState(null, '', '/');
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(repaints.at(-1)).toBe(TODAY);
    expect(commits.at(-1).date).toBe(TODAY);
  });

  it('does not write the restored URL back to history', async () => {
    // A browser-restored URL is already the address bar's content; writing
    // it again would push a second entry and make Back a no-op.
    await loadScrubber();
    releaseAt(LAST_RATED_PCT);

    history.replaceState(null, '', '/');
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(location.search).toBe('');
  });
});
