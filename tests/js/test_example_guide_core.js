/*
 * tests/js/test_example_guide_core.js — the pure half of the example route
 * walkthrough (static/js/example_guide_core.js, SNOW-1230).
 *
 * Step order, the drag and zoom completion tests, the day's wording and
 * the ?example= parameter. The DOM half is test_map_example_guide.js.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/example_guide_core.js';

const core = self.pwaExampleGuideCore;

describe('steps', () => {
  it('runs intro, drag, zoom, colours', () => {
    expect(core.STEPS).toEqual(['intro', 'drag', 'zoom', 'colours']);
  });

  it('numbers each step from 1, and an unknown one 0', () => {
    expect(core.stepNumber('intro')).toBe(1);
    expect(core.stepNumber('colours')).toBe(4);
    expect(core.stepNumber('nope')).toBe(0);
  });

  it('names the next step, and none after the last', () => {
    expect(core.nextStep('intro')).toBe('drag');
    expect(core.nextStep('zoom')).toBe('colours');
    expect(core.nextStep('colours')).toBeNull();
    expect(core.nextStep('nope')).toBeNull();
  });
});

describe('createDragTracker', () => {
  it('does not count a tap, which visits one index', () => {
    const drag = core.createDragTracker(101);
    expect(drag.record(50)).toBe(false);
    expect(drag.span()).toBe(0);
  });

  it('completes once the point has spanned a fifth of the route', () => {
    const drag = core.createDragTracker(101);
    drag.record(10);
    expect(drag.record(29)).toBe(false);
    expect(drag.record(30)).toBe(true);
  });

  it('measures the widest stretch, so back and forth does not add up', () => {
    const drag = core.createDragTracker(101);
    for (let i = 0; i < 5; i += 1) {
      drag.record(40);
      drag.record(50);
    }
    expect(drag.span()).toBeCloseTo(0.1);
    expect(drag.record(45)).toBe(false);
  });

  it('ignores a cleared point', () => {
    const drag = core.createDragTracker(101);
    drag.record(null);
    expect(drag.span()).toBe(0);
  });

  it('starts again after reset', () => {
    const drag = core.createDragTracker(101);
    drag.record(0);
    drag.record(100);
    drag.reset();
    expect(drag.span()).toBe(0);
  });

  it('never completes on a route with one segment', () => {
    const drag = core.createDragTracker(1);
    expect(drag.record(0)).toBe(false);
  });
});

describe('zoomProgress', () => {
  it('fills from the starting zoom to the target', () => {
    expect(core.zoomProgress(11, 11, 14)).toBe(0);
    expect(core.zoomProgress(12.5, 11, 14)).toBeCloseTo(0.5);
    expect(core.zoomProgress(14, 11, 14)).toBe(1);
  });

  it('is full past the target and empty below the start', () => {
    expect(core.zoomProgress(15, 11, 14)).toBe(1);
    expect(core.zoomProgress(10, 11, 14)).toBe(0);
  });

  it('is empty when the step began at or past the target and the camera went back out', () => {
    expect(core.zoomProgress(13, 14.2, 14)).toBe(0);
  });
});

describe('formatting the day', () => {
  it('says the day in full for a sentence', () => {
    expect(core.formatDay('2026-04-10', 'en-GB')).toBe('10 April 2026');
  });

  it('says it short for a button', () => {
    expect(core.formatShortDay('2026-04-10', 'en-GB')).toBe('10 Apr');
  });

  it('passes through something that is not a day', () => {
    expect(core.formatDay('soon', 'en-GB')).toBe('soon');
    expect(core.formatDay('', 'en-GB')).toBe('');
  });

  it('falls back to the default locale for a bad tag', () => {
    expect(core.formatDay('2026-04-10', 'not a locale!!')).toMatch(/2026/);
  });
});

describe('the example parameter', () => {
  it('recognises the arrival', () => {
    expect(core.isExampleArrival('?route_share=abc&example=1&d=2026-04-10')).toBe(true);
    expect(core.isExampleArrival('?route_share=abc')).toBe(false);
    expect(core.isExampleArrival('?example=0')).toBe(false);
  });

  it('strips it and keeps everything else', () => {
    expect(core.withoutExampleParam('?example=1&d=2026-04-10')).toBe('?d=2026-04-10');
    expect(core.withoutExampleParam('?example=1')).toBe('');
  });
});
