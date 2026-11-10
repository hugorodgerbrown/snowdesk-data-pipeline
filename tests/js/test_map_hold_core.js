/*
 * tests/js/test_map_hold_core.js — Vitest unit tests for
 * static/js/map_hold_core.js (SNOW-1485).
 *
 * `map_hold_core.js` is a plain browser IIFE that assigns a frozen
 * `window.pwaMapHoldCore` — importing it for side effects is enough. Time
 * is Vitest's fake clock, which drives both the detector's default timers
 * and its default `now`.
 *
 * What is pinned, because each is the way a hold goes wrong:
 *   - a finger held still fires at 500 ms, and not before;
 *   - a move past 8 px, a second finger, a lift before 500 ms and a map
 *     drag or zoom each cancel, so pan and pinch stay the map's;
 *   - a right-click fires at once;
 *   - the synthetic contextmenu a touch screen sends for a long touch is
 *     swallowed, so one hold never fires twice;
 *   - the click after a touch hold is taken once, and a later tap is not.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/map_hold_core.js';

const core = window.pwaMapHoldCore;

let onHold;
let detector;

beforeEach(() => {
  vi.useFakeTimers();
  onHold = vi.fn();
  detector = core.createDetector({ onHold });
});

afterEach(() => {
  detector.dispose();
  vi.useRealTimers();
});

describe('a touch hold', () => {
  it('fires at 500 ms when the finger stays still', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    vi.advanceTimersByTime(499);
    expect(onHold).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onHold).toHaveBeenCalledOnce();
    expect(onHold).toHaveBeenCalledWith({ x: 100, y: 200 }, 'touch');
  });

  it('allows a drift within 8 px', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    detector.touchMove([{ x: 105, y: 206 }]);
    vi.advanceTimersByTime(500);
    expect(onHold).toHaveBeenCalledWith({ x: 100, y: 200 }, 'touch');
  });

  it('cancels on a move past 8 px', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    detector.touchMove([{ x: 109, y: 200 }]);
    vi.advanceTimersByTime(1000);
    expect(onHold).not.toHaveBeenCalled();
  });

  it('cancels on a second finger', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    detector.touchStart([{ x: 100, y: 200 }, { x: 160, y: 240 }]);
    vi.advanceTimersByTime(1000);
    expect(onHold).not.toHaveBeenCalled();
  });

  it('cancels when a move reports two fingers', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    detector.touchMove([{ x: 100, y: 200 }, { x: 160, y: 240 }]);
    vi.advanceTimersByTime(1000);
    expect(onHold).not.toHaveBeenCalled();
  });

  it('cancels on a touchend before 500 ms', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    vi.advanceTimersByTime(300);
    detector.touchEnd();
    vi.advanceTimersByTime(1000);
    expect(onHold).not.toHaveBeenCalled();
  });

  it.each(['drag', 'zoom'])('cancels on a %s start', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    vi.advanceTimersByTime(200);
    detector.cancel();
    vi.advanceTimersByTime(1000);
    expect(onHold).not.toHaveBeenCalled();
  });
});

describe('a contextmenu', () => {
  it('fires at once for a mouse right-click', () => {
    expect(detector.contextMenu({ x: 40, y: 50 })).toBe(true);
    expect(onHold).toHaveBeenCalledWith({ x: 40, y: 50 }, 'mouse');
  });

  it('is swallowed while a finger is down, so a long touch fires once', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    vi.advanceTimersByTime(450);
    expect(detector.contextMenu({ x: 100, y: 200 })).toBe(false);
    vi.advanceTimersByTime(50);
    expect(detector.contextMenu({ x: 100, y: 200 })).toBe(false);
    detector.touchEnd();
    expect(detector.contextMenu({ x: 100, y: 200 })).toBe(false);
    expect(onHold).toHaveBeenCalledOnce();
    expect(onHold).toHaveBeenCalledWith({ x: 100, y: 200 }, 'touch');
  });

  it('counts again once the touch is well past', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    detector.touchEnd();
    vi.advanceTimersByTime(core.SYNTHETIC_MS);
    expect(detector.contextMenu({ x: 1, y: 2 })).toBe(true);
  });
});

describe('the click after a hold', () => {
  it('is taken once after a touch hold', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    vi.advanceTimersByTime(500);
    detector.touchEnd();
    expect(detector.takeClick()).toBe(true);
    expect(detector.takeClick()).toBe(false);
  });

  it('is not taken for a plain tap', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    vi.advanceTimersByTime(100);
    detector.touchEnd();
    expect(detector.takeClick()).toBe(false);
  });

  it('expires when the browser sends no click after the lift', () => {
    detector.touchStart([{ x: 100, y: 200 }]);
    vi.advanceTimersByTime(500);
    detector.touchEnd();
    vi.advanceTimersByTime(core.CLICK_GRACE_MS + 1);
    expect(detector.takeClick()).toBe(false);
  });

  it('is not taken after a right-click', () => {
    detector.contextMenu({ x: 1, y: 2 });
    expect(detector.takeClick()).toBe(false);
  });
});

it('the core is frozen', () => {
  expect(Object.isFrozen(core)).toBe(true);
  expect(core.HOLD_MS).toBe(500);
  expect(core.TOLERANCE_PX).toBe(8);
});
