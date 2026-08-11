/*
 * tests/js/test_map_timelapse_url_sync.js — playback puts the day it lands
 * on into the URL (SNOW-660).
 *
 * SNOW-660's rule is that the moment a day is chosen — by the scrubber, by
 * playback, or by a `?d=` link — the choropleth paints and the URL says so.
 * The timelapse was the one surface that painted without saying so: it
 * commits `snowdesk:date-changed` per frame and never touched the address
 * bar, so a day reached by playing the season was on screen with a bare
 * querystring behind it — which, now that bare means "no day asked for",
 * reloads into a blank map.
 *
 * The rate is the whole design here, so it is asserted directly. Writing per
 * frame is not an option: `history.replaceState` is throttled by Safari at
 * roughly 100 calls per 30s and THROWS on the next one, and playback runs at
 * five frames a second — a per-frame write would take down the running timer
 * inside half a minute. So the write happens where playback SETTLES: a stop
 * (the boundary, or the user pressing it), and the two skip jumps, which are
 * single discrete actions.
 *
 * The module is loaded on its own against a stubbed scope; every cross-file
 * binding it reads is listed in `loadTimelapse`.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/scrubber_core.js';

const DATES = ['2026-02-09', '2026-02-10', '2026-02-11'];
const RATINGS = Object.fromEntries(DATES.map((d, i) => [d, { 'CH-4115': i + 1 }]));

function buildFixture() {
  document.body.innerHTML = `
    <div id="season-scrubber"
         data-season-start="${DATES[0]}"
         data-season-end="${DATES[DATES.length - 1]}"
         aria-valuenow="0">
      <div class="season-scrubber-track"><div class="season-scrubber-thumb"></div></div>
    </div>
    <button id="scrubber-play" data-state="stopped"></button>
    <button id="scrubber-reverse" data-state="stopped"></button>
    <button id="scrubber-skip-start"></button>
    <button id="scrubber-skip-end"></button>`;
}

/**
 * Load the timelapse against a stubbed scope.
 *
 * @returns {Promise<{urlWrites: string[], painted: string[]}>} Every date
 *   handed to the shared URL writer, and every date painted.
 */
async function loadTimelapse() {
  const urlWrites = [];
  const painted = [];
  globalThis.MAP = { isStyleLoaded: () => true };
  globalThis.IS_PLAYING = false;
  globalThis.MAP_STRINGS = {
    'timelapse-play': 'Play season timelapse',
    'timelapse-play-reverse': 'Play season timelapse in reverse',
    'timelapse-stop': 'Stop season timelapse',
    'timelapse-stop-reverse': 'Stop reverse timelapse',
  };
  globalThis.getSeasonRatings = () => Promise.resolve(RATINGS);
  globalThis.repaintRegionsForDate = (dateKey) => { painted.push(dateKey); };
  // The shared writer from map_shared.js. Recorded rather than performed:
  // the count is half the assertion, and a real replaceState would hide a
  // repeat write behind an identical URL.
  globalThis.writeUrlDateParam = (dateKey) => { urlWrites.push(dateKey); };

  vi.resetModules();
  await import('../../static/js/map_timelapse.js');
  return { urlWrites, painted };
}

beforeEach(() => {
  vi.useFakeTimers();
  buildFixture();
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = '';
  delete globalThis.MAP;
  delete globalThis.IS_PLAYING;
  delete globalThis.MAP_STRINGS;
  delete globalThis.getSeasonRatings;
  delete globalThis.repaintRegionsForDate;
  delete globalThis.writeUrlDateParam;
});

/** Press play and let the season run to its end (200ms per frame). */
async function playToTheEnd() {
  document.getElementById('scrubber-play').click();
  // start() awaits getSeasonRatings before arming the timer.
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(200 * (DATES.length + 1));
}

describe('playback and the URL', () => {
  it('writes ?d= once, for the frame it settled on', async () => {
    const { urlWrites, painted } = await loadTimelapse();

    await playToTheEnd();

    // Every frame painted; exactly one URL write, naming the last of them.
    expect(painted).toEqual(DATES);
    expect(urlWrites).toEqual([DATES[DATES.length - 1]]);
  });

  it('does not write one per frame', async () => {
    // The assertion that keeps this off Safari's replaceState throttle,
    // which throws rather than degrading — a per-frame write would end
    // playback with an exception partway through a season.
    const { urlWrites, painted } = await loadTimelapse();

    await playToTheEnd();

    expect(painted.length).toBeGreaterThan(urlWrites.length);
    expect(urlWrites.length).toBe(1);
  });

  it('writes the frame the user stopped on, mid-season', async () => {
    const { urlWrites } = await loadTimelapse();
    document.getElementById('scrubber-play').click();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(200);

    document.getElementById('scrubber-play').click();

    expect(urlWrites).toEqual([DATES[1]]);
  });

  it('writes the day a skip-to-end jump lands on', async () => {
    // One discrete user action, one settled day — so it writes immediately,
    // exactly as a scrubber commit does.
    const { urlWrites } = await loadTimelapse();

    document.getElementById('scrubber-skip-end').click();

    expect(urlWrites).toEqual([DATES[DATES.length - 1]]);
  });

  it('does not write when a scrub stops playback', async () => {
    // The scrubber has already written its own, later date. A write here
    // would replace it with the frame the scrub interrupted.
    const { urlWrites } = await loadTimelapse();
    document.getElementById('scrubber-play').click();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(200);

    document.dispatchEvent(new CustomEvent('snowdesk:date-changed', {
      detail: { date: DATES[0], source: 'scrubber' },
    }));

    expect(urlWrites).toEqual([]);
  });
});
