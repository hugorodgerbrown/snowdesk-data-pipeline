/*
 * tests/js/test_route_cursor_core.js — the cursor shared by the map, the
 * rail and the point card (static/js/route_cursor_core.js, SNOW-1016).
 *
 * Pure state: index arithmetic, clamping at the route's ends, and when
 * subscribers are called. The cursor holds a point and nothing else:
 * leg selection was removed on 2026-10-02, and the API that opened a leg
 * went with it.
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
  it('starts with no point', () => {
    expect(cursor.state()).toEqual({ index: null });
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

describe('no selection (SNOW-1052)', () => {
  it('has no select or clearSelection and no selection key', () => {
    expect(cursor.select).toBeUndefined();
    expect(cursor.clearSelection).toBeUndefined();
    cursor.setIndex(12);
    expect('selection' in cursor.state()).toBe(false);
  });

  it('has no leg to open, and no openLeg key (2026-10-02)', () => {
    expect(cursor.openLeg).toBeUndefined();
    expect(cursor.closeLeg).toBeUndefined();
    cursor.setIndex(12);
    expect('openLeg' in cursor.state()).toBe(false);
  });
});

describe('subscribe', () => {
  it('is called once per change with the new state', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);

    cursor.setIndex(42);

    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith({ index: 42 });
  });

  it('is not called on subscription', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);
    expect(fn).not.toHaveBeenCalled();
  });

  it('is not called for a set that changes nothing', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);

    cursor.setIndex(250); // clamps to 99: a change
    cursor.setIndex(300); // clamps to 99 again, which is not
    cursor.setIndex(99.2);

    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('is not called for clearing what is already clear', () => {
    const fn = vi.fn();
    cursor.subscribe(fn);

    cursor.setIndex(null);

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
