/*
 * tests/js/test_route_cursor_core.js — the cursor shared by the map and
 * both rails (static/js/route_cursor_core.js, SNOW-1016).
 *
 * Pure state: index arithmetic, clamping at the route's and the open leg's
 * ends, the selection, and when subscribers are called. The case most worth
 * holding is the leg clamp — a drag off the end of rail two must not move
 * the cursor on rail one or the map outside the open leg, and nothing but a
 * test would notice it doing so on a leg that ends near the route's end.
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
  it('starts with no cursor, no leg and no selection', () => {
    expect(cursor.state()).toEqual({ index: null, openLeg: null, selection: null });
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

  it('clamps the cursor to both ends of the leg', () => {
    cursor.openLeg(leg);
    cursor.setIndex(10);
    expect(cursor.state().index).toBe(30);
    cursor.setIndex(80);
    expect(cursor.state().index).toBe(59);
  });

  it('pulls an out-of-range cursor into the leg when it opens', () => {
    cursor.setIndex(90);
    cursor.openLeg(leg);
    expect(cursor.state().index).toBe(59);
  });

  it('leaves a cleared cursor cleared when it opens', () => {
    cursor.openLeg(leg);
    expect(cursor.state().index).toBeNull();
  });

  it('keeps the cursor where it was when the leg closes', () => {
    cursor.openLeg(leg);
    cursor.setIndex(45);
    cursor.closeLeg();
    expect(cursor.state().index).toBe(45);
    cursor.setIndex(80);
    expect(cursor.state().index).toBe(80);
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

describe('the selection', () => {
  it('is set, replaced and cleared', () => {
    cursor.select({ kind: 'band', from: 10, to: 14 });
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 10, to: 14 });

    cursor.select({ kind: 'passage', from: 40, to: 43 });
    expect(cursor.state().selection).toEqual({ kind: 'passage', from: 40, to: 43 });

    cursor.clearSelection();
    expect(cursor.state().selection).toBeNull();
  });

  it('is put in order and clamped to the route', () => {
    cursor.select({ kind: 'band', from: 120, to: 95 });
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 95, to: 99 });
  });

  it('is not clamped to the open leg', () => {
    cursor.openLeg({ from: 30, to: 59 });
    cursor.select({ kind: 'passage', from: 55, to: 70 });
    expect(cursor.state().selection).toEqual({ kind: 'passage', from: 55, to: 70 });
  });

  it('survives opening and closing a leg', () => {
    cursor.select({ kind: 'band', from: 10, to: 14 });
    cursor.openLeg({ from: 30, to: 59 });
    cursor.closeLeg();
    expect(cursor.state().selection).toEqual({ kind: 'band', from: 10, to: 14 });
  });
});

describe('subscribe', () => {
  it('is called once per change with the new state', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);

    cursor.setIndex(42);

    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith({ index: 42, openLeg: null, selection: null });
  });

  it('is not called on subscription', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);
    expect(fn).not.toHaveBeenCalled();
  });

  it('is not called for a set that changes nothing', () => {
    cursor.setIndex(42);
    cursor.openLeg({ from: 30, to: 59 });
    cursor.select({ kind: 'band', from: 10, to: 14 });
    const fn = vi.fn();
    cursor.subscribe(fn);

    cursor.setIndex(42);
    cursor.setIndex(42.2);
    cursor.openLeg({ from: 30, to: 59, i: 2 });
    cursor.select({ kind: 'band', from: 14, to: 10 });
    cursor.setIndex(90); // clamps to 59, which is a change
    cursor.setIndex(95); // clamps to 59 again, which is not

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('is not called for clearing what is already clear', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);

    cursor.setIndex(null);
    cursor.closeLeg();
    cursor.clearSelection();

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
