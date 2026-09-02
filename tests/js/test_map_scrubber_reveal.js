/*
 * tests/js/test_map_scrubber_reveal.js — Vitest unit tests for
 * static/js/map_scrubber_reveal.js (SNOW-794).
 *
 * The module reads #season-scrubber and returns early if it is absent, so
 * the fixture must exist BEFORE the module is imported. Each test re-imports
 * via vi.resetModules() so the IIFE runs fresh against its own fixture.
 *
 * Only state is asserted — `data-in-season` on the scrubber. What that
 * attribute DOES is a CSS rule (`display: none`), and jsdom has no layout to
 * observe it with; the hiding itself is covered in the browser.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const SEASON_START = '2026-02-01';
const SEASON_END = '2026-04-30';
const TODAY = '2026-03-15';

/**
 * @param {Object} [opts]
 * @param {string} [opts.today] The server-rendered today key.
 * @param {string} [opts.inSeason] The server-rendered starting state.
 */
function buildFixture({ today = TODAY, inSeason = 'true' } = {}) {
  document.body.innerHTML = `
    <div id="season-scrubber"
         data-season-start="${SEASON_START}"
         data-season-end="${SEASON_END}"
         data-today="${today}"
         data-in-season="${inSeason}"></div>`;
}

async function loadModule() {
  vi.resetModules();
  await import('../../static/js/map_scrubber_reveal.js');
}

const scrubber = () => document.getElementById('season-scrubber');

/** Announce a committed date the way map_scrubber.js's commitDate does. */
function changeDate(date) {
  document.dispatchEvent(
    new CustomEvent('snowdesk:date-changed', { detail: { date, source: 'test' } }),
  );
}

afterEach(() => {
  document.body.innerHTML = '';
});

describe('revealing the scrubber for the shown day', () => {
  beforeEach(buildFixture);

  it('leaves the server-rendered state alone until a date is committed', async () => {
    await loadModule();
    // No flash-and-correct: the server already decided, and the module has
    // nothing to say until the day actually moves.
    expect(scrubber().dataset.inSeason).toBe('true');
  });

  it('hides for a day after the season ends', async () => {
    await loadModule();
    changeDate('2026-07-15');
    expect(scrubber().dataset.inSeason).toBe('false');
  });

  it('hides for a day before the season starts', async () => {
    await loadModule();
    changeDate('2026-01-31');
    expect(scrubber().dataset.inSeason).toBe('false');
  });

  it('shows again when the day comes back inside the season', async () => {
    await loadModule();
    changeDate('2026-07-15');
    expect(scrubber().dataset.inSeason).toBe('false');
    changeDate('2026-03-01');
    expect(scrubber().dataset.inSeason).toBe('true');
  });

  it('treats both season bounds as inclusive', async () => {
    await loadModule();
    changeDate(SEASON_START);
    expect(scrubber().dataset.inSeason).toBe('true');
    changeDate(SEASON_END);
    expect(scrubber().dataset.inSeason).toBe('true');
  });
});

describe('when no day is chosen', () => {
  it('falls back to today, which shows the scrubber mid-season', async () => {
    buildFixture({ today: TODAY });
    await loadModule();
    // SNOW-660: `date: null` is the unchosen state — a cold boot, or a step
    // back out of a chosen day. Today is where the thumb would rest.
    changeDate(null);
    expect(scrubber().dataset.inSeason).toBe('true');
  });

  it('falls back to today, which hides the scrubber off-season', async () => {
    buildFixture({ today: '2026-09-02', inSeason: 'false' });
    await loadModule();
    changeDate(null);
    // September: the season is over and there is nothing to scrub through.
    expect(scrubber().dataset.inSeason).toBe('false');
  });
});

describe('missing data', () => {
  it('hides rather than guessing when the season bounds are absent', async () => {
    document.body.innerHTML = '<div id="season-scrubber" data-today="2026-03-15"></div>';
    await loadModule();
    changeDate('2026-03-15');
    // No season means no season scrubber — the safe reading.
    expect(scrubber().dataset.inSeason).toBe('false');
  });

  it('does not throw when the scrubber is absent from the page', async () => {
    document.body.innerHTML = '<div id="something-else"></div>';
    await expect(loadModule()).resolves.not.toThrow();
    expect(() => changeDate('2026-03-15')).not.toThrow();
  });
});
