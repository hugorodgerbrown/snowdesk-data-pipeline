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
 * release without a drag, because a release IS a user action. So the three
 * cases here are the rule in full: nothing is announced or written on boot,
 * a real interaction announces and writes normally — including for today,
 * which now has to write `?d=` because a bare querystring means "no day
 * chosen" and a reload must not silently blank the map — and a back step
 * onto a dateless URL returns to the unchosen state instead of committing
 * the derived date by the back door.
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
 * @returns {Promise<{commits: Array<Object>, repaints: Array<?string>}>}
 *   Every `snowdesk:date-changed` detail and every repaint's date key.
 */
async function loadScrubber() {
  const repaints = [];
  globalThis.COUNTRY_STATE = { ch: true, fr: false, at: false, it: false };
  globalThis.MAP_READY_PROMISE = Promise.resolve();
  globalThis.MAP_STRINGS = { 'season-unavailable': 'Season data unavailable' };
  globalThis.getSeasonRatings = () => Promise.resolve(RATINGS);
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
  delete globalThis.repaintRegionsForDate;
  delete globalThis.writeUrlDateParam;
});

describe('boot with no ?d=', () => {
  it('announces no date, so nothing downstream paints one', async () => {
    const { commits } = await loadScrubber();

    expect(commits).toEqual([]);
  });

  it('leaves the URL exactly as the visitor arrived at it', async () => {
    await loadScrubber();

    expect(location.search).toBe('');
  });

  it('repaints nothing, so the choropleth stays uncoloured', async () => {
    // The commit is what used to repaint. Asserted separately because a
    // repaint without the event would still colour the map — the failure
    // this ticket is about is what is ON SCREEN, not what was announced.
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
  it('returns to the unchosen state rather than committing a date', async () => {
    // The old handler fell back to the effective last date here, which
    // reached the same invented-date bug by a different route: navigating
    // back OUT of a chosen day landed on a day the map picked itself.
    const { commits } = await loadScrubber();
    releaseAt(TODAY_PCT);

    history.replaceState(null, '', '/');
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(commits.at(-1)).toEqual({ date: null, source: 'scrubber' });
  });

  it('clears the choropleth by repainting for no date', async () => {
    const { commits, repaints } = await loadScrubber();
    releaseAt(TODAY_PCT);

    history.replaceState(null, '', '/');
    window.dispatchEvent(new PopStateEvent('popstate'));

    expect(repaints.at(-1)).toBeNull();
    expect(commits.at(-1).date).toBeNull();
  });
});
