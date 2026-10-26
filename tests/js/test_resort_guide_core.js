/*
 * tests/js/test_resort_guide_core.js — Vitest unit tests for
 * static/js/resort_guide_core.js (SNOW-1231).
 *
 * The pure half of the "Find a resort" walkthrough: step order, when the
 * map shows a step done, what to draw for a state of the map, where the
 * bar sits beside a sheet, and the sign-in link back to the resort.
 */

import { beforeAll, describe, expect, it } from 'vitest';

let core;

beforeAll(async () => {
  delete self.pwaResortGuideCore;
  await import('../../static/js/resort_guide_core.js');
  core = self.pwaResortGuideCore;
});

const state = (overrides = {}) => ({
  searchOpen: false,
  resortOpen: false,
  regionOpen: false,
  onGuideDay: true,
  ...overrides,
});

describe('steps', () => {
  it('runs search, resort, region', () => {
    expect(core.STEPS).toEqual(['search', 'resort', 'region']);
    expect(core.stepNumber('resort')).toBe(2);
    expect(core.stepNumber('nope')).toBe(0);
    expect(core.nextStep('search')).toBe('resort');
    expect(core.nextStep('region')).toBeNull();
    expect(core.nextStep('nope')).toBeNull();
  });

  it('finishes search when a resort sheet opens, and resort when the region sheet does', () => {
    expect(core.isStepDone('search', state({ searchOpen: true }))).toBe(false);
    expect(core.isStepDone('search', state({ resortOpen: true }))).toBe(true);
    expect(core.isStepDone('resort', state({ resortOpen: true }))).toBe(false);
    expect(core.isStepDone('resort', state({ regionOpen: true }))).toBe(true);
  });

  it('never finishes the last step by itself: Finish does', () => {
    expect(core.isStepDone('region', state({ regionOpen: true }))).toBe(false);
  });
});

describe('surface', () => {
  it('draws nothing unless running', () => {
    expect(core.surface('waiting', 'search', state({ searchOpen: true })).show).toBe('none');
    expect(core.surface('ended', 'region', state({ regionOpen: true })).show).toBe('none');
  });

  it('leads search on the first step, and the bar over each sheet after', () => {
    expect(core.surface('running', 'search', state({ searchOpen: true })).show).toBe('lead');
    expect(core.surface('running', 'resort', state({ resortOpen: true })).show).toBe('bar');
    expect(core.surface('running', 'region', state({ regionOpen: true })).show).toBe('bar');
  });

  it('pauses when what the step is about is closed', () => {
    expect(core.surface('running', 'search', state())).toEqual({ show: 'pill', pill: 'paused' });
    expect(core.surface('running', 'resort', state({ regionOpen: true })))
      .toEqual({ show: 'pill', pill: 'paused' });
    expect(core.surface('running', 'region', state({ resortOpen: true })))
      .toEqual({ show: 'pill', pill: 'paused' });
  });

  it('says the map left the guide\'s day before anything else', () => {
    expect(core.surface('running', 'resort', state({ resortOpen: true, onGuideDay: false })))
      .toEqual({ show: 'pill', pill: 'off-day' });
  });
});

describe('barPosition', () => {
  const base = { map: { top: 100, left: 0 }, floor: 60, height: 120, gap: 12 };

  it('sits under the top controls with no sheet', () => {
    expect(core.barPosition({ ...base, phone: true, sheet: null }))
      .toEqual({ top: 60, bottom: null, left: null });
  });

  it('rides on top of a phone sheet with room above it', () => {
    expect(core.barPosition({ ...base, phone: true, sheet: { top: 500, right: 390 } }))
      .toEqual({ top: 268, bottom: null, left: null });
  });

  it('lies over a full phone sheet, at the top or the foot as asked', () => {
    const sheet = { top: 120, right: 390 };
    expect(core.barPosition({ ...base, phone: true, sheet, overSheet: 'top' }))
      .toEqual({ top: 60, bottom: null, left: null });
    expect(core.barPosition({ ...base, phone: true, sheet, overSheet: 'bottom' }))
      .toEqual({ top: null, bottom: 12, left: null });
  });

  it('stands beside a desktop column, level with its top', () => {
    expect(core.barPosition({ ...base, phone: false, sheet: { top: 190, right: 400 } }))
      .toEqual({ top: 90, bottom: null, left: 412 });
    expect(core.barPosition({ ...base, phone: false, sheet: { top: 110, right: 400 } }).top)
      .toBe(60);
  });
});

describe('signInHref', () => {
  it('comes back to the map with the resort open', () => {
    expect(core.signInHref('/accounts/sign-in/', '/map/', 'st-moritz'))
      .toBe('/accounts/sign-in/?next=%2Fmap%2F%3Fresort%3Dst-moritz');
  });

  it('is the bare sign-in page without a resort', () => {
    expect(core.signInHref('/accounts/sign-in/', '/map/', '')).toBe('/accounts/sign-in/');
  });
});
