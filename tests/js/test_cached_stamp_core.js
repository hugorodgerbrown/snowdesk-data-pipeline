/*
 * tests/js/test_cached_stamp_core.js — Vitest tests for
 * static/js/cached_stamp_core.js (SNOW-1139).
 *
 * The one formatter behind every "cached at" stamp and every download's
 * freshness. The module is imported by tests/js/setup.js for every suite, as
 * base.html loads it for every page; it is re-imported here only to make the
 * dependency explicit. ``now`` is injected throughout, so nothing here reads
 * the clock.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/cached_stamp_core.js';

const core = window.pwaCachedStampCore;

/** A local-time instant in October 2026, as an ISO string. */
function localIso(day, hours, minutes) {
  return new Date(2026, 9, day, hours, minutes).toISOString();
}

const NOW = new Date(2026, 9, 20, 15, 0);

describe('formatStamp', () => {
  it('is HH:MM alone when the instant is today', () => {
    expect(core.formatStamp(localIso(20, 7, 5), { now: NOW })).toBe('07:05');
  });

  it('carries the day when the instant is yesterday', () => {
    const stamp = core.formatStamp(localIso(19, 23, 40), { now: NOW, locale: 'en-GB' });
    expect(stamp).toBe('19 Oct 23:40');
  });

  it('carries the day for a cache from last week too', () => {
    const stamp = core.formatStamp(localIso(12, 9, 0), { now: NOW, locale: 'en-GB' });
    expect(stamp).toBe('12 Oct 09:00');
  });

  it('is empty for a missing or unparseable value', () => {
    expect(core.formatStamp(null, { now: NOW })).toBe('');
    expect(core.formatStamp('', { now: NOW })).toBe('');
    expect(core.formatStamp('not a date', { now: NOW })).toBe('');
    expect(core.formatStamp(42, { now: NOW })).toBe('');
  });
});

describe('formatDay', () => {
  it('formats the local day in the requested language', () => {
    expect(core.formatDay(localIso(18, 10, 0), { locale: 'en-GB' })).toBe('18 Oct');
  });

  it('falls back to the page language', () => {
    document.documentElement.lang = 'en-GB';
    try {
      expect(core.formatDay(localIso(18, 10, 0))).toBe('18 Oct');
    } finally {
      document.documentElement.lang = '';
    }
  });

  it('is empty for an unparseable value', () => {
    expect(core.formatDay('nope')).toBe('');
  });
});

describe('isBeforeToday', () => {
  it('is false for an instant earlier today', () => {
    expect(core.isBeforeToday(localIso(20, 0, 1), NOW)).toBe(false);
  });

  it('is true for yesterday, however late', () => {
    expect(core.isBeforeToday(localIso(19, 23, 59), NOW)).toBe(true);
  });

  it('is false for a future instant, which proves no staleness', () => {
    expect(core.isBeforeToday(localIso(21, 8, 0), NOW)).toBe(false);
  });

  it('is false for a missing or unparseable value', () => {
    expect(core.isBeforeToday(undefined, NOW)).toBe(false);
    expect(core.isBeforeToday('garbage', NOW)).toBe(false);
  });

  it('defaults to the current time', () => {
    expect(core.isBeforeToday(new Date().toISOString())).toBe(false);
    expect(core.isBeforeToday('2001-01-01T00:00:00Z')).toBe(true);
  });
});
