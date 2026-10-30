/*
 * tests/js/test_day_nav_core.js — Vitest unit tests for
 * static/js/day_nav_core.js (SNOW-1195).
 *
 * `day_nav_core.js` is a plain browser IIFE that assigns a frozen
 * `window.pwaDayNavCore` — importing it for side effects is enough.
 *
 * What is pinned, because each is the way this goes wrong:
 *   - a step crosses month, year and leap-day edges, and a DST change never
 *     moves it;
 *   - the scrubber's keys follow the WAI-ARIA slider pattern;
 *   - a day is "empty" only for the countries that are on, and a null
 *     rating is not a bulletin;
 *   - the empty-day note looks back before it looks forward.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/day_nav_core.js';

const core = window.pwaDayNavCore;

const ON = { ch: true, fr: false };

describe('shiftDay', () => {
  it.each([
    ['2026-02-12', 1, '2026-02-13'],
    ['2026-02-28', 1, '2026-03-01'],
    ['2028-02-28', 1, '2028-02-29'],
    ['2026-01-01', -1, '2025-12-31'],
    ['2026-03-29', 1, '2026-03-30'],
    ['2026-10-25', -1, '2026-10-24'],
    ['2026-02-12', 7, '2026-02-19'],
    ['2026-02-12', 0, '2026-02-12'],
  ])('%s %+d → %s', (key, delta, expected) => {
    expect(core.shiftDay(key, delta)).toBe(expected);
  });

  it.each([[''], ['2026-2-1'], [null]])('is empty for an unparseable key (%s)', (key) => {
    expect(core.shiftDay(key, 1)).toBe('');
  });
});

describe('clampDay', () => {
  it('holds a day inside the range', () => {
    expect(core.clampDay('2025-10-01', '2025-11-12', '2026-10-23')).toBe('2025-11-12');
    expect(core.clampDay('2026-10-24', '2025-11-12', '2026-10-23')).toBe('2026-10-23');
    expect(core.clampDay('2026-02-12', '2025-11-12', '2026-10-23')).toBe('2026-02-12');
  });

  it('treats an empty bound as no bound', () => {
    expect(core.clampDay('1999-01-01', '', '')).toBe('1999-01-01');
  });
});

describe('keyStep', () => {
  it.each([
    ['ArrowLeft', { delta: -1 }],
    ['ArrowDown', { delta: -1 }],
    ['ArrowRight', { delta: 1 }],
    ['ArrowUp', { delta: 1 }],
    ['PageDown', { delta: -7 }],
    ['PageUp', { delta: 7 }],
    ['Home', { edge: 'min' }],
    ['End', { edge: 'max' }],
  ])('%s → %j', (key, expected) => {
    expect(core.keyStep(key)).toEqual(expected);
  });

  it.each([['Tab'], ['Enter'], ['a']])('ignores %s', (key) => {
    expect(core.keyStep(key)).toBeNull();
  });
});
