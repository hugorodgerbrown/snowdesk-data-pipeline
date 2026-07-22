/*
 * tests/js/test_scrubber_core.js — Vitest unit tests for
 * static/js/scrubber_core.js (SNOW-496).
 *
 * This is genuinely NEW coverage, not a like-for-like port: the Playwright
 * files these functions were extracted from (test_scrubber_reverse.py,
 * test_timelapse_popup.py) never exercised the transport math itself —
 * MapLibre's `map.on('load')` never fires in headless Chromium, so
 * `timelapseInit`'s `start()` always returned early at the
 * `MAP.isStyleLoaded()` guard, and those two files were DOM-structure /
 * event-dispatch / no-JS-error smoke tests only. Extracting the pure
 * functions into scrubber_core.js is what makes this math testable at all;
 * both Playwright files are left otherwise intact (nothing here duplicates
 * an assertion they were already making).
 *
 * `scrubber_core.js` is a plain browser IIFE that assigns a frozen
 * `window.pwaScrubberCore` — importing it for side effects is enough.
 */

import { describe, expect, it, vi } from 'vitest';

import '../../static/js/scrubber_core.js';

const core = window.pwaScrubberCore;

// A one-season-day span for percentage/date-key round-trip tests.
const SEASON_START_MS = Date.parse('2025-11-01T00:00:00Z');
const SEASON_END_MS = Date.parse('2026-05-01T00:00:00Z');
const SEASON_SPAN_MS = SEASON_END_MS - SEASON_START_MS;

describe('pctToDateKey', () => {
  it('maps 0% to the season start date', () => {
    expect(core.pctToDateKey(0, SEASON_START_MS, SEASON_SPAN_MS)).toBe('2025-11-01');
  });

  it('maps 100% to the season end date', () => {
    expect(core.pctToDateKey(100, SEASON_START_MS, SEASON_SPAN_MS)).toBe('2026-05-01');
  });

  it('snaps to UTC midnight regardless of the fractional offset', () => {
    // 50% of a 181-day span lands mid-day; the result must still be a
    // clean YYYY-MM-DD with no time component.
    const key = core.pctToDateKey(50, SEASON_START_MS, SEASON_SPAN_MS);
    expect(key).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe('dateKeyToPct', () => {
  it('maps the season start date to 0%', () => {
    expect(core.dateKeyToPct('2025-11-01', SEASON_START_MS, SEASON_SPAN_MS, 50)).toBe(0);
  });

  it('maps the season end date to 100%', () => {
    expect(core.dateKeyToPct('2026-05-01', SEASON_START_MS, SEASON_SPAN_MS, 50)).toBe(100);
  });

  it('clamps a date before the season start to 0%', () => {
    expect(core.dateKeyToPct('2020-01-01', SEASON_START_MS, SEASON_SPAN_MS, 50)).toBe(0);
  });

  it('clamps a date after the season end to 100%', () => {
    expect(core.dateKeyToPct('2030-01-01', SEASON_START_MS, SEASON_SPAN_MS, 50)).toBe(100);
  });

  it('returns the fallback for an unparseable date key', () => {
    expect(core.dateKeyToPct('not-a-date', SEASON_START_MS, SEASON_SPAN_MS, 42)).toBe(42);
  });

  it('returns the fallback when the season span is non-positive', () => {
    expect(core.dateKeyToPct('2025-12-01', SEASON_START_MS, 0, 42)).toBe(42);
    expect(core.dateKeyToPct('2025-12-01', SEASON_START_MS, -10, 42)).toBe(42);
  });

  it('round-trips with pctToDateKey', () => {
    const pct = core.dateKeyToPct('2026-01-15', SEASON_START_MS, SEASON_SPAN_MS, 0);
    const key = core.pctToDateKey(pct, SEASON_START_MS, SEASON_SPAN_MS);
    expect(key).toBe('2026-01-15');
  });
});

describe('snapToNearestDataDay', () => {
  const sortedDates = ['2026-01-01', '2026-01-05', '2026-01-10'];

  it('returns the exact match when the date key is already a data day', () => {
    expect(core.snapToNearestDataDay('2026-01-05', sortedDates)).toBe('2026-01-05');
  });

  it('snaps to the nearest earlier or later data day', () => {
    expect(core.snapToNearestDataDay('2026-01-03', sortedDates)).toBe('2026-01-01');
    expect(core.snapToNearestDataDay('2026-01-04', sortedDates)).toBe('2026-01-05');
  });

  it('returns the date key unchanged when sortedDates is empty or null', () => {
    expect(core.snapToNearestDataDay('2026-01-03', [])).toBe('2026-01-03');
    expect(core.snapToNearestDataDay('2026-01-03', null)).toBe('2026-01-03');
  });
});

describe('nearestFrameIndex', () => {
  const sortedDates = ['2025-11-01', '2026-01-15', '2026-05-01'];

  it('returns 0 at the season start (aria-valuenow 0)', () => {
    expect(core.nearestFrameIndex(sortedDates, 0, SEASON_START_MS, SEASON_SPAN_MS)).toBe(0);
  });

  it('returns the last index at the season end (aria-valuenow 100)', () => {
    expect(core.nearestFrameIndex(sortedDates, 100, SEASON_START_MS, SEASON_SPAN_MS)).toBe(2);
  });

  it('returns the nearest middle index for a mid-season position', () => {
    const midPct = core.dateKeyToPct('2026-01-15', SEASON_START_MS, SEASON_SPAN_MS, 0);
    expect(core.nearestFrameIndex(sortedDates, midPct, SEASON_START_MS, SEASON_SPAN_MS)).toBe(1);
  });

  it('returns 0 when sortedDates is empty or null', () => {
    expect(core.nearestFrameIndex([], 50, SEASON_START_MS, SEASON_SPAN_MS)).toBe(0);
    expect(core.nearestFrameIndex(null, 50, SEASON_START_MS, SEASON_SPAN_MS)).toBe(0);
  });

  it('returns 0 when aria-valuenow or the season span is not finite/positive', () => {
    expect(core.nearestFrameIndex(sortedDates, NaN, SEASON_START_MS, SEASON_SPAN_MS)).toBe(0);
    expect(core.nearestFrameIndex(sortedDates, 50, SEASON_START_MS, 0)).toBe(0);
  });
});

describe('nextFrame', () => {
  it('advances forward and is not done mid-sequence', () => {
    expect(core.nextFrame(2, 1, 5)).toEqual({ frameIdx: 3, done: false });
  });

  it('is done once the forward step runs off the end', () => {
    expect(core.nextFrame(4, 1, 5)).toEqual({ frameIdx: 5, done: true });
  });

  it('advances in reverse and is not done mid-sequence', () => {
    expect(core.nextFrame(2, -1, 5)).toEqual({ frameIdx: 1, done: false });
  });

  it('is done once the reverse step runs off the start', () => {
    expect(core.nextFrame(0, -1, 5)).toEqual({ frameIdx: -1, done: true });
  });
});

describe('deriveEffectiveTodayKey', () => {
  const countryState = { ch: true, fr: false, at: false, it: false };

  it('returns the latest date containing a region for an active country', () => {
    const dates = ['2026-01-01', '2026-01-02', '2026-01-03'];
    const cache = {
      '2026-01-01': { 'CH-4115': 2 },
      '2026-01-02': { 'FR-01': 1 },
      '2026-01-03': { 'FR-02': 1 },
    };
    expect(core.deriveEffectiveTodayKey(dates, cache, countryState, 'fallback')).toBe('2026-01-01');
  });

  it('falls back to the last date when no date matches an active country', () => {
    const dates = ['2026-01-01', '2026-01-02'];
    const cache = {
      '2026-01-01': { 'FR-01': 1 },
      '2026-01-02': { 'FR-02': 1 },
    };
    expect(core.deriveEffectiveTodayKey(dates, cache, countryState, 'fallback')).toBe('2026-01-02');
  });

  it('returns fallbackKey when dates is empty or null', () => {
    expect(core.deriveEffectiveTodayKey([], {}, countryState, 'fallback')).toBe('fallback');
    expect(core.deriveEffectiveTodayKey(null, {}, countryState, 'fallback')).toBe('fallback');
  });

  it('warns once per unexpected prefix and still falls back to the last date', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const dates = ['2026-01-01', '2026-01-02'];
    const cache = {
      '2026-01-01': { 'LI-01': 1 },
      '2026-01-02': { 'LI-02': 1 },
    };
    const result = core.deriveEffectiveTodayKey(dates, cache, countryState, 'fallback');
    expect(result).toBe('2026-01-02');
    expect(warnSpy).toHaveBeenCalledTimes(1);
    warnSpy.mockRestore();
  });
});

describe('isInSeason', () => {
  it('is true for the season boundaries inclusive', () => {
    expect(core.isInSeason('2025-11-01', SEASON_START_MS, SEASON_END_MS)).toBe(true);
    expect(core.isInSeason('2026-05-01', SEASON_START_MS, SEASON_END_MS)).toBe(true);
  });

  it('is true for a date strictly inside the season', () => {
    expect(core.isInSeason('2026-01-15', SEASON_START_MS, SEASON_END_MS)).toBe(true);
  });

  it('is false for a date outside the season', () => {
    expect(core.isInSeason('2025-10-31', SEASON_START_MS, SEASON_END_MS)).toBe(false);
    expect(core.isInSeason('2026-05-02', SEASON_START_MS, SEASON_END_MS)).toBe(false);
  });

  it('is false for an unparseable date key', () => {
    expect(core.isInSeason('not-a-date', SEASON_START_MS, SEASON_END_MS)).toBe(false);
  });
});

describe('intToKey', () => {
  const ratingTable = ['no_rating', 'low', 'moderate', 'considerable', 'high', 'very_high'];

  it('looks up a valid index', () => {
    expect(core.intToKey(3, ratingTable)).toBe('considerable');
  });

  it('returns no_rating for null/undefined', () => {
    expect(core.intToKey(null, ratingTable)).toBe('no_rating');
    expect(core.intToKey(undefined, ratingTable)).toBe('no_rating');
  });

  it('returns no_rating for an out-of-range index', () => {
    expect(core.intToKey(-1, ratingTable)).toBe('no_rating');
    expect(core.intToKey(99, ratingTable)).toBe('no_rating');
  });
});
