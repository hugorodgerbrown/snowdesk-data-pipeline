/*
 * tests/js/test_calendar_core.js — Vitest unit tests for
 * static/js/calendar_core.js (SNOW-792).
 *
 * `calendar_core.js` is a plain browser IIFE that assigns a frozen
 * `window.pwaCalendarCore` — importing it for side effects is enough.
 *
 * The season window used throughout is the real shape of one: it starts
 * mid-month and ends mid-month, because the scrubber's bounds are narrowed
 * to the actual RegionDayRating data (see `_base_map_context`). A window
 * that happened to align to month boundaries would hide every off-by-one
 * in the clamping.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/calendar_core.js';

const core = window.pwaCalendarCore;

const SEASON_START = '2025-11-12';
const SEASON_END = '2026-04-20';

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
  it('leaves a month inside the window alone', () => {
    expect(core.clampMonthKey('2026-01', SEASON_START, SEASON_END)).toBe('2026-01');
  });

  it('pulls a month before the window up to the first month', () => {
    expect(core.clampMonthKey('2025-08', SEASON_START, SEASON_END)).toBe('2025-11');
  });

  it('pulls a month after the window back to the last month', () => {
    expect(core.clampMonthKey('2026-09', SEASON_START, SEASON_END)).toBe('2026-04');
  });

  it('keeps the partial first and last months, which are in the window', () => {
    // The season starts on the 12th and ends on the 20th, so both boundary
    // months are half outside it — but the MONTH is still reachable, and
    // buildMonthGrid is what disables the days either side.
    expect(core.clampMonthKey('2025-11', SEASON_START, SEASON_END)).toBe('2025-11');
    expect(core.clampMonthKey('2026-04', SEASON_START, SEASON_END)).toBe('2026-04');
  });
});

describe('selectableDates', () => {
  it('takes the date keys of the ratings cache', () => {
    const set = core.selectableDates(
      { '2026-01-05': {}, '2026-01-06': {} },
      SEASON_START,
      SEASON_END
    );
    expect([...set].sort()).toEqual(['2026-01-05', '2026-01-06']);
  });

  it('drops keys outside the season window', () => {
    // The cache is a merged, multi-country payload; nothing guarantees
    // every key in it belongs to the window this scrubber was rendered for.
    const set = core.selectableDates(
      { '2025-11-01': {}, '2026-01-05': {}, '2026-05-30': {} },
      SEASON_START,
      SEASON_END
    );
    expect([...set]).toEqual(['2026-01-05']);
  });

  it('keeps the exact boundary days', () => {
    const set = core.selectableDates(
      { [SEASON_START]: {}, [SEASON_END]: {} },
      SEASON_START,
      SEASON_END
    );
    expect(set.size).toBe(2);
  });

  it('returns an empty set for a missing cache', () => {
    expect(core.selectableDates(null, SEASON_START, SEASON_END).size).toBe(0);
    expect(core.selectableDates(undefined, SEASON_START, SEASON_END).size).toBe(0);
  });
});

describe('buildMonthGrid', () => {
  const grid = (monthKey, opts) => core.buildMonthGrid(monthKey, opts || {});

  it('returns whole weeks', () => {
    for (const month of ['2025-11', '2025-12', '2026-01', '2026-02', '2026-04']) {
      expect(grid(month).length % 7).toBe(0);
    }
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

  it('pads the last row out to a full week', () => {
    const cells = grid('2026-02');
    expect(cells[cells.length - 1].blank).toBe(true);
  });

  it('marks days outside the season window unselectable', () => {
    const cells = grid('2025-11', { seasonStart: SEASON_START, seasonEnd: SEASON_END });
    expect(cellFor(cells, '2025-11-11').selectable).toBe(false);
    expect(cellFor(cells, '2025-11-12').selectable).toBe(true);
  });

  it('marks days after the season end unselectable', () => {
    const cells = grid('2026-04', { seasonStart: SEASON_START, seasonEnd: SEASON_END });
    expect(cellFor(cells, '2026-04-20').selectable).toBe(true);
    expect(cellFor(cells, '2026-04-21').selectable).toBe(false);
  });

  it('marks in-season days with no data unselectable', () => {
    // The whole point of the control: a day the map cannot paint must not
    // be offerable, or the click lands silently somewhere else.
    const cells = grid('2026-01', {
      seasonStart: SEASON_START,
      seasonEnd: SEASON_END,
      available: new Set(['2026-01-05']),
    });
    expect(cellFor(cells, '2026-01-05').selectable).toBe(true);
    expect(cellFor(cells, '2026-01-06').selectable).toBe(false);
  });

  it('treats an absent data set as "do not filter"', () => {
    const cells = grid('2026-01', { seasonStart: SEASON_START, seasonEnd: SEASON_END });
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
