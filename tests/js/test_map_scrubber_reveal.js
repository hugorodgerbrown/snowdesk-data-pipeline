/*
 * tests/js/test_map_scrubber_reveal.js — Vitest unit tests for
 * static/js/map_scrubber_reveal.js (SNOW-794, SNOW-1431).
 *
 * The module reads #season-scrubber and returns early if it is absent, so
 * the fixture must exist BEFORE the module is imported. Each test re-imports
 * via vi.resetModules() so the IIFE runs fresh against its own fixture.
 *
 * Only state is asserted — `data-on-track` on the scrubber. What that
 * attribute DOES is a CSS rule (`display: none`), and jsdom has no layout to
 * observe it with; the hiding itself is covered in the browser.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The track: the latest bulletin run's first day through today.
const TRACK_START = '2026-02-01';
const TRACK_END = '2026-04-30';
const TODAY = '2026-04-30';

/**
 * @param {Object} [opts]
 * @param {string} [opts.today] The server-rendered today key.
 * @param {string} [opts.onTrack] The server-rendered starting state.
 */
function buildFixture({ today = TODAY, onTrack = 'true' } = {}) {
  document.body.innerHTML = `
    <div id="season-scrubber"
         data-season-start="${TRACK_START}"
         data-season-end="${TRACK_END}"
         data-today="${today}"
         data-on-track="${onTrack}"></div>`;
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
    expect(scrubber().dataset.onTrack).toBe('true');
  });

  it('hides for a calendar pick before the run started', async () => {
    await loadModule();
    changeDate('2026-01-31');
    expect(scrubber().dataset.onTrack).toBe('false');
  });

  it('shows again on a pick inside the window', async () => {
    await loadModule();
    changeDate('2025-12-15');
    expect(scrubber().dataset.onTrack).toBe('false');
    changeDate('2026-03-01');
    expect(scrubber().dataset.onTrack).toBe('true');
  });

  it('treats both track bounds as inclusive', async () => {
    await loadModule();
    changeDate(TRACK_START);
    expect(scrubber().dataset.onTrack).toBe('true');
    changeDate(TRACK_END);
    expect(scrubber().dataset.onTrack).toBe('true');
  });
});

describe('when no day is chosen', () => {
  it('falls back to today, which is always on the track', async () => {
    buildFixture({ today: TODAY, onTrack: 'false' });
    await loadModule();
    // SNOW-660: `date: null` is the unchosen state — a cold boot, or a step
    // back out of a chosen day. Today is where the thumb would rest, and
    // since SNOW-1431 the track runs through today.
    changeDate(null);
    expect(scrubber().dataset.onTrack).toBe('true');
  });
});

describe('missing data', () => {
  it('hides rather than guessing when the track bounds are absent', async () => {
    document.body.innerHTML = '<div id="season-scrubber" data-today="2026-03-15"></div>';
    await loadModule();
    changeDate('2026-03-15');
    // No track means nowhere for the thumb to sit — the safe reading.
    expect(scrubber().dataset.onTrack).toBe('false');
  });

  it('does not throw when the scrubber is absent from the page', async () => {
    document.body.innerHTML = '<div id="something-else"></div>';
    await expect(loadModule()).resolves.not.toThrow();
    expect(() => changeDate('2026-03-15')).not.toThrow();
  });
});
