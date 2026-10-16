/*
 * tests/js/test_sheet_detents_core.js — where a phone map sheet settles
 * (static/js/sheet_detents_core.js, SNOW-1097).
 *
 * Parsing the declared detents; the opening detent; the handle's cycle; the
 * dragged-height clamp; and the settle rule — nearest detent to the release
 * height projected by the flick velocity, a subset of detents, and the drag
 * down that closes.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/sheet_detents_core.js';

const core = window.pwaSheetDetentsCore;

const ALL = ['peek', 'half', 'full'];
const HEIGHTS = { peek: 120, half: 400, full: 760 };

describe('parseDetents', () => {
  it('returns the declared detents in rising order', () => {
    expect(core.parseDetents('full, peek,half')).toEqual(ALL);
  });

  it('drops unknown names and duplicates', () => {
    expect(core.parseDetents('half,half,banana,full')).toEqual(['half', 'full']);
  });

  it('returns nothing for an absent declaration', () => {
    expect(core.parseDetents(null)).toEqual([]);
    expect(core.parseDetents('')).toEqual([]);
  });
});

describe('initialDetent', () => {
  it('honours a declared request', () => {
    expect(core.initialDetent(ALL, 'half')).toBe('half');
  });

  it('falls back to the lowest declared detent', () => {
    expect(core.initialDetent(['half', 'full'], 'peek')).toBe('half');
    expect(core.initialDetent(ALL, '')).toBe('peek');
  });

  it('is null with nothing declared', () => {
    expect(core.initialDetent([], 'peek')).toBeNull();
  });
});

describe('nextDetent', () => {
  it('steps up and wraps from the top back to the lowest', () => {
    expect(core.nextDetent(ALL, 'peek')).toBe('half');
    expect(core.nextDetent(ALL, 'half')).toBe('full');
    expect(core.nextDetent(ALL, 'full')).toBe('peek');
  });

  it('cycles within a subset', () => {
    expect(core.nextDetent(['half', 'full'], 'full')).toBe('half');
  });

  it('starts at the lowest from no detent', () => {
    expect(core.nextDetent(ALL, null)).toBe('peek');
  });
});

describe('clampHeight', () => {
  it('keeps a drag between nothing and the full height', () => {
    expect(core.clampHeight(-20, 760)).toBe(0);
    expect(core.clampHeight(900, 760)).toBe(760);
    expect(core.clampHeight(300, 760)).toBe(300);
  });

  it('reads a non-number as nothing', () => {
    expect(core.clampHeight(Number.NaN, 760)).toBe(0);
  });
});

describe('settle', () => {
  it('lands a slow drag on the nearest detent', () => {
    expect(core.settle({ detents: ALL, heights: HEIGHTS, height: 230, velocity: 0 })).toBe('peek');
    expect(core.settle({ detents: ALL, heights: HEIGHTS, height: 300, velocity: 0 })).toBe('half');
    expect(core.settle({ detents: ALL, heights: HEIGHTS, height: 640, velocity: 0 })).toBe('full');
  });

  it('carries a quick upward flick past a detent it was short of', () => {
    // 200 px is nearer peek, but 2 px/ms projects 300 px further up.
    expect(core.settle({ detents: ALL, heights: HEIGHTS, height: 200, velocity: 2 })).toBe('half');
  });

  it('carries a quick downward flick back to a lower detent', () => {
    // 640 px is nearer full, but -1 px/ms projects it back to 490.
    expect(core.settle({ detents: ALL, heights: HEIGHTS, height: 640, velocity: -1 })).toBe('half');
  });

  it('only ever settles on a declared detent', () => {
    expect(
      core.settle({ detents: ['half', 'full'], heights: HEIGHTS, height: 130, velocity: 0.5 }),
    ).toBe('half');
  });

  it('closes on a drag below half the lowest detent', () => {
    expect(core.settle({ detents: ALL, heights: HEIGHTS, height: 50, velocity: 0 })).toBe('close');
  });

  it('closes on a downward flick from the lowest detent', () => {
    expect(core.settle({ detents: ALL, heights: HEIGHTS, height: 110, velocity: -1 })).toBe(
      'close',
    );
  });

  it('measures the close threshold against the lowest DECLARED detent', () => {
    // 150 px is above half of peek but below half of half (200).
    expect(
      core.settle({ detents: ['half', 'full'], heights: HEIGHTS, height: 150, velocity: 0 }),
    ).toBe('close');
  });

  it('treats a missing velocity as a slow release', () => {
    expect(core.settle({ detents: ALL, heights: HEIGHTS, height: 390 })).toBe('half');
  });

  it('is null with nothing declared', () => {
    expect(core.settle({ detents: [], heights: HEIGHTS, height: 300, velocity: 0 })).toBeNull();
  });
});
