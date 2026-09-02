/*
 * tests/js/test_calendar_core.js — Vitest unit tests for
 * static/js/calendar_core.js (SNOW-792).
 *
 * `calendar_core.js` is a plain browser IIFE that assigns a frozen
 * `window.pwaCalendarCore` — importing it for side effects is enough.
 *
 * Two ranges run through every case here, and keeping them apart is the
 * point of most of it:
 *
 *   the REACHABLE range — [min, max], where max is today. The only thing
 *   the picker refuses is the future.
 *   the SEASON — a highlight inside it. Days outside the season are still
 *   selectable, because the map has weather for them.
 *
 * The season used throughout starts and ends mid-month, which is the real
 * shape of one: the scrubber's bounds are narrowed to the actual
 * RegionDayRating data (see `_base_map_context`). A window that happened to
 * align to month boundaries would hide every off-by-one in the clamping.
 *
 * `TODAY` is deliberately months AFTER the season ends — the off-season
 * case, which is when the season-as-a-fence bug was reachable and when the
 * weather layer is the only thing the map has to show.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/calendar_core.js';

const core = window.pwaCalendarCore;

const SEASON_START = '2025-11-12';
const SEASON_END = '2026-04-20';
// Out of season, as the site is for half the year.
const TODAY = '2026-09-02';
const RANGE_MIN = SEASON_START;

/** The date keys of every non-blank cell in a grid. */
const dayKeys = (cells) => cells.filter((c) => !c.blank).map((c) => c.dateKey);

/** The cell for one date key, or undefined. */
const cellFor = (cells, dateKey) => cells.find((c) => c.dateKey === dateKey);

describe('monthKeyOf', () => {
  it('takes the month off a date key', () => {
    expect(core.monthKeyOf('2026-02-16')).toBe('2026-02');
  });

  it('returns an empty string for anything that is not a date key', () => {
    expect(core.monthKeyOf('')).toBe('');
    expect(core.monthKeyOf('2026-02')).toBe('');
    expect(core.monthKeyOf('not a date')).toBe('');
    expect(core.monthKeyOf(null)).toBe('');
    expect(core.monthKeyOf(undefined)).toBe('');
  });
});

describe('shiftMonth', () => {
  it('steps forward inside a year', () => {
    expect(core.shiftMonth('2026-02', 1)).toBe('2026-03');
  });

  it('steps back inside a year', () => {
    expect(core.shiftMonth('2026-02', -1)).toBe('2026-01');
  });

  it('carries into the previous year going back from January', () => {
    expect(core.shiftMonth('2026-01', -1)).toBe('2025-12');
  });

  it('carries into the next year going forward from December', () => {
    expect(core.shiftMonth('2025-12', 1)).toBe('2026-01');
  });

  it('carries more than a whole year in either direction', () => {
    expect(core.shiftMonth('2026-02', -14)).toBe('2024-12');
    expect(core.shiftMonth('2026-02', 23)).toBe('2028-01');
  });

  it('returns an empty string for a malformed month key', () => {
    expect(core.shiftMonth('2026-2', 1)).toBe('');
    expect(core.shiftMonth('2026-02-16', 1)).toBe('');
  });
});

describe('clampMonthKey', () => {
  it('leaves a month inside the range alone', () => {
    expect(core.clampMonthKey('2026-01', RANGE_MIN, TODAY)).toBe('2026-01');
  });

  it('pulls a month before the range up to the first month', () => {
    expect(core.clampMonthKey('2025-08', RANGE_MIN, TODAY)).toBe('2025-11');
  });

  it('pulls a month past today back to this month', () => {
    expect(core.clampMonthKey('2026-12', RANGE_MIN, TODAY)).toBe('2026-09');
  });

  it('reaches the months between the season end and today', () => {
    // The season ends in April and today is September. Every month in
    // between has to be reachable — the map has weather for those days,
    // and clamping them back to April is the bug this replaced.
    for (const month of ['2026-05', '2026-06', '2026-07', '2026-08']) {
      expect(core.clampMonthKey(month, RANGE_MIN, TODAY)).toBe(month);
    }
  });

  it('keeps the partial first and last months, which are in the range', () => {
    expect(core.clampMonthKey('2025-11', RANGE_MIN, TODAY)).toBe('2025-11');
    expect(core.clampMonthKey('2026-09', RANGE_MIN, TODAY)).toBe('2026-09');
  });
});

describe('earliestKnownDate', () => {
  it('takes the earliest key in the ratings cache', () => {
    // The fallback here is LATER than every key, so the cache is what the
    // answer must come from. (Passing SEASON_START would prove nothing —
    // it is earlier than these, and the next case is what covers that.)
    const cache = { '2026-01-06': {}, '2025-12-30': {}, '2026-01-05': {} };
    expect(core.earliestKnownDate(cache, '2026-02-01')).toBe('2025-12-30');
  });

  it('prefers the season start when it is earlier', () => {
    // The archive can begin after the season window opens; the floor must
    // not cut the highlight off at its own first rated day.
    const cache = { '2025-12-30': {} };
    expect(core.earliestKnownDate(cache, SEASON_START)).toBe(SEASON_START);
  });

  it('falls back when the cache is missing or has no usable key', () => {
    expect(core.earliestKnownDate(null, SEASON_START)).toBe(SEASON_START);
    expect(core.earliestKnownDate({}, SEASON_START)).toBe(SEASON_START);
    expect(core.earliestKnownDate({ nonsense: {} }, SEASON_START)).toBe(SEASON_START);
  });
});

describe('buildMonthGrid', () => {
  const grid = (monthKey, opts) => core.buildMonthGrid(monthKey, opts || {});

  it('is always six weeks, whatever the month needs', () => {
    // Constant height: the panel is bottom-anchored, so a five-row month
    // next to a six-row one moved the popup's top edge on every step.
    // February 2026 is the extreme — 28 days starting on a Sunday.
    for (const month of ['2025-11', '2025-12', '2026-01', '2026-02', '2026-04', '2024-02']) {
      expect(grid(month).length, month).toBe(42);
    }
  });

  it('never needs a seventh week, even in the worst case', () => {
    // A 31-day month starting on a Sunday: six leading blanks + 31 days.
    // March 2025 starts on a Saturday; August 2026 starts on a Saturday.
    // 2026-03 starts on a Sunday and has 31 days — the maximum.
    const cells = grid('2026-03');
    expect(dayKeys(cells).length).toBe(31);
    expect(cells.length).toBe(42);
    expect(cells[cells.length - 1].blank).toBe(true);
  });

  it('holds every day of the month, in order', () => {
    const keys = dayKeys(grid('2026-02'));
    expect(keys.length).toBe(28);
    expect(keys[0]).toBe('2026-02-01');
    expect(keys[27]).toBe('2026-02-28');
  });

  it('handles a leap February', () => {
    expect(dayKeys(grid('2024-02')).length).toBe(29);
  });

  it('pads the first row so the grid is Monday-first', () => {
    // 1 February 2026 is a Sunday — the last column of a Monday-first
    // week, so six blanks precede it.
    const cells = grid('2026-02');
    expect(cells.slice(0, 6).every((c) => c.blank)).toBe(true);
    expect(cells[6].dateKey).toBe('2026-02-01');
  });

  it('leaves no leading blanks when the month starts on a Monday', () => {
    // 1 December 2025 is a Monday.
    expect(grid('2025-12')[0].dateKey).toBe('2025-12-01');
  });

  it('pads the tail with blanks', () => {
    const cells = grid('2026-02');
    expect(cells[cells.length - 1].blank).toBe(true);
  });

  it('refuses the future, and only the future', () => {
    const cells = grid('2026-09', { min: RANGE_MIN, max: TODAY });
    expect(cellFor(cells, '2026-09-02').selectable).toBe(true);
    expect(cellFor(cells, '2026-09-03').selectable).toBe(false);
    expect(cellFor(cells, '2026-09-30').selectable).toBe(false);
  });

  it('offers days outside the season, because the map has weather for them', () => {
    // The season ended in April; these are June days. Refusing them was the
    // bug — a picker fenced to the season cannot reach a day the weather
    // layer can answer for.
    const cells = grid('2026-06', {
      min: RANGE_MIN,
      max: TODAY,
      seasonStart: SEASON_START,
      seasonEnd: SEASON_END,
    });
    const days = cells.filter((c) => !c.blank);
    expect(days.every((c) => c.selectable)).toBe(true);
    expect(days.every((c) => !c.inSeason)).toBe(true);
  });

  it('offers in-season days whether or not a bulletin exists for them', () => {
    // A gap in the archive is a day with no choropleth, not a day the map
    // cannot show — and the grid does not distinguish the two. It carries
    // no per-day "has a bulletin" mark at all; landing on such a day shows
    // an uncoloured map, which is the same answer told once.
    const cells = grid('2026-01', {
      min: RANGE_MIN,
      max: TODAY,
      seasonStart: SEASON_START,
      seasonEnd: SEASON_END,
    });
    const days = cells.filter((c) => !c.blank && c.dateKey >= SEASON_START);
    expect(days.every((c) => c.selectable && c.inSeason)).toBe(true);
    expect(days.every((c) => !('hasRatings' in c))).toBe(true);
  });

  it('marks the season boundary days as in-season and the ones outside as not', () => {
    const cells = grid('2025-11', { seasonStart: SEASON_START, seasonEnd: SEASON_END });
    expect(cellFor(cells, '2025-11-11').inSeason).toBe(false);
    expect(cellFor(cells, '2025-11-12').inSeason).toBe(true);

    const april = grid('2026-04', { seasonStart: SEASON_START, seasonEnd: SEASON_END });
    expect(cellFor(april, '2026-04-20').inSeason).toBe(true);
    expect(cellFor(april, '2026-04-21').inSeason).toBe(false);
  });

  it('stays fully selectable when the ratings cache never resolves', () => {
    // The cache only supplies the paging floor now; losing the fetch must
    // not lock the picker.
    const cells = grid('2026-01', { min: RANGE_MIN, max: TODAY });
    expect(cells.filter((c) => !c.blank).every((c) => c.selectable)).toBe(true);
  });

  it('marks the selected day and today, and only those', () => {
    const cells = grid('2026-02', { selected: '2026-02-16', today: '2026-02-20' });
    expect(cells.filter((c) => c.selected).map((c) => c.dateKey)).toEqual(['2026-02-16']);
    expect(cells.filter((c) => c.isToday).map((c) => c.dateKey)).toEqual(['2026-02-20']);
  });

  it('marks nothing selected when no day has been chosen', () => {
    const cells = grid('2026-02', { selected: '' });
    expect(cells.some((c) => c.selected)).toBe(false);
  });

  it('returns an empty array for a malformed month key', () => {
    expect(grid('2026-2')).toEqual([]);
    expect(grid('')).toEqual([]);
  });

  it('does not shift a day across a DST boundary', () => {
    // European clocks go forward on the last Sunday of March. Building the
    // grid from local-time Dates would slide the days either side of it —
    // this is the bug the UTC-midnight arithmetic exists to prevent.
    const keys = dayKeys(grid('2026-03'));
    expect(keys.length).toBe(31);
    expect(keys[28]).toBe('2026-03-29');
    expect(keys[29]).toBe('2026-03-30');
  });
});
