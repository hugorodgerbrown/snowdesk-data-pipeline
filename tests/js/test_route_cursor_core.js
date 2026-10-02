/*
 * tests/js/test_route_cursor_core.js — the cursor shared by the map, the
 * rail and the point card (static/js/route_cursor_core.js, SNOW-1016).
 *
 * Pure state: index arithmetic, clamping at the route's ends, and when
 * subscribers are called. The case most worth holding is SNOW-1065's
 * exclusivity — a leg or a point, never both: opening a leg clears the
 * point and placing a point closes the leg, so the map can never show a
 * highlighted leg and a point card reading somewhere else at once.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/route_cursor_core.js';

const { createRouteCursor } = self.pwaRouteCursorCore;

/** A route of 100 segments: indices 0 to 99. */
let cursor;

beforeEach(() => {
  cursor = createRouteCursor(100);
});

describe('createRouteCursor', () => {
  it('starts with no cursor and no leg', () => {
    expect(cursor.state()).toEqual({ index: null, openLeg: null });
  });

  it.each([0, -1, 2.5, NaN, undefined])('rejects a segment count of %s', (count) => {
    expect(() => createRouteCursor(count)).toThrow(RangeError);
  });
});

describe('setIndex', () => {
  it('holds an index inside the route', () => {
    cursor.setIndex(42);
    expect(cursor.state().index).toBe(42);
  });

  it('clamps to both ends of the route', () => {
    cursor.setIndex(-5);
    expect(cursor.state().index).toBe(0);
    cursor.setIndex(250);
    expect(cursor.state().index).toBe(99);
  });

  it('rounds a fractional index to the nearest segment', () => {
    cursor.setIndex(41.6);
    expect(cursor.state().index).toBe(42);
  });

  it('clears the cursor on null', () => {
    cursor.setIndex(42);
    cursor.setIndex(null);
    expect(cursor.state().index).toBeNull();
  });

  it.each([NaN, Infinity, '42', undefined])('throws on %s rather than clamping it', (value) => {
    expect(() => cursor.setIndex(value)).toThrow(TypeError);
  });
});

describe('an open leg', () => {
  const leg = { i: 2, from: 30, to: 59, climbing: false };

  it('clears the point when it opens (SNOW-1065)', () => {
    cursor.setIndex(45);
    cursor.openLeg(leg);
    expect(cursor.state()).toEqual({ index: null, openLeg: leg });
  });

  it('closes when a point is placed, anywhere on the route', () => {
    cursor.openLeg(leg);
    cursor.setIndex(80);
    expect(cursor.state()).toEqual({ index: 80, openLeg: null });
    cursor.openLeg(leg);
    cursor.setIndex(45);
    expect(cursor.state()).toEqual({ index: 45, openLeg: null });
  });

  it('stays open when the point is cleared', () => {
    cursor.openLeg(leg);
    cursor.setIndex(null);
    expect(cursor.state().openLeg).toEqual(leg);
  });

  it('switches to another leg', () => {
    cursor.openLeg(leg);
    cursor.openLeg({ i: 3, from: 60, to: 99, climbing: true });
    expect(cursor.state().openLeg.i).toBe(3);
  });

  it('leaves the cleared point cleared when it closes', () => {
    cursor.openLeg(leg);
    cursor.closeLeg();
    expect(cursor.state()).toEqual({ index: null, openLeg: null });
  });

  it('hands back the leg with the properties it was given', () => {
    cursor.openLeg(leg);
    expect(cursor.state().openLeg).toEqual(leg);
  });

  it.each([
    [{ from: -1, to: 10 }, RangeError],
    [{ from: 90, to: 100 }, RangeError],
    [{ from: 20, to: 10 }, RangeError],
    [{ from: NaN, to: 10 }, TypeError],
    [null, TypeError],
  ])('rejects the leg %j', (bad, error) => {
    expect(() => cursor.openLeg(bad)).toThrow(error);
  });
});

describe('no selection (SNOW-1052)', () => {
  it('has no select or clearSelection and no selection key', () => {
    expect(cursor.select).toBeUndefined();
    expect(cursor.clearSelection).toBeUndefined();
    cursor.setIndex(12);
    expect('selection' in cursor.state()).toBe(false);
  });
});

describe('subscribe', () => {
  it('is called once per change with the new state', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);

    cursor.setIndex(42);

    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith({ index: 42, openLeg: null });
  });

  it('is not called on subscription', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);
    expect(fn).not.toHaveBeenCalled();
  });

  it('is not called for a set that changes nothing', () => {
    cursor.openLeg({ from: 30, to: 59 });
    const fn = vi.fn();
    cursor.subscribe(fn);

    cursor.openLeg({ from: 30, to: 59, i: 2 }); // the same leg, rebuilt
    cursor.setIndex(250); // clamps to 99 and closes the leg: a change
    cursor.setIndex(300); // clamps to 99 again, which is not
    cursor.setIndex(99.2);

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('is not called for clearing what is already clear', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);

    cursor.setIndex(null);
    cursor.closeLeg();

    expect(fn).not.toHaveBeenCalled();
  });

  it('stops being called once unsubscribed', () => {
    const fn = vi.fn();
    const unsubscribe = cursor.subscribe(fn);
    unsubscribe();

    cursor.setIndex(42);

    expect(fn).not.toHaveBeenCalled();
  });

  it('publishes a frozen state', () => {
    cursor.setIndex(42);
    expect(Object.isFrozen(cursor.state())).toBe(true);
  });
});
