/*
 * tests/js/test_map_region_pick_core.js — "Download a region"'s pure half
 * (static/js/map_region_pick_core.js, SNOW-1414).
 *
 * What the bar says for a chosen region, and what the reader is told after
 * a region download is asked to start.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/map_region_pick_core.js';

const core = self.pwaRegionPickCore;
const S = core.STATES;

describe('barState', () => {
  it('asks for a tap before any region is chosen', () => {
    expect(core.barState(null, null, null)).toEqual({
      state: S.CHOOSING,
      regionId: null,
      name: '',
      mb: '',
      canDownload: false,
    });
  });

  it('can download a region with a priced size, terrain included', () => {
    const bar = core.barState('CH-2101', 'Aletsch', { mb: 72.4, terrainMb: 11, tooBig: false });
    expect(bar).toEqual({
      state: S.READY,
      regionId: 'CH-2101',
      name: 'Aletsch',
      mb: '83',
      canDownload: true,
    });
  });

  it('cannot download a region with no download summary', () => {
    const bar = core.barState('CH-9999', 'Nowhere', null);
    expect(bar.state).toBe(S.UNAVAILABLE);
    expect(bar.canDownload).toBe(false);
    expect(bar.mb).toBe('');
  });

  it('cannot download a region over this device ceiling, and still says its size', () => {
    const bar = core.barState('CH-2101', 'Aletsch', { mb: 900, terrainMb: 0, tooBig: true });
    expect(bar.state).toBe(S.TOO_BIG);
    expect(bar.canDownload).toBe(false);
    expect(bar.mb).toBe('900');
  });

  it('names an unnamed region by its id', () => {
    expect(core.barState('CH-2101', '', { mb: 5, terrainMb: 0, tooBig: false }).name).toBe('CH-2101');
  });
});

describe('formatMb', () => {
  it('writes whole megabytes from 10 up and one decimal below', () => {
    expect(core.formatMb(72.4)).toBe('72');
    expect(core.formatMb(9.94)).toBe('9.9');
  });

  it('never writes 0', () => {
    expect(core.formatMb(0)).toBe('0.1');
    expect(core.formatMb(0.01)).toBe('0.1');
    expect(core.formatMb(Number.NaN)).toBe('0.1');
  });
});

describe('refusalKind', () => {
  it('says nothing about a run that started', () => {
    expect(core.refusalKind({ started: true, reason: '' })).toBeNull();
  });

  it('says nothing after a sign-in redirect', () => {
    expect(core.refusalKind({ started: false, reason: 'signin' })).toBeNull();
  });

  it('names a run already in flight', () => {
    expect(core.refusalKind({ started: false, reason: 'busy' })).toBe('busy');
  });

  it('names a region already on this device', () => {
    expect(core.refusalKind({ started: false, reason: 'done' })).toBe('already');
    expect(core.refusalKind({ started: false, reason: 'incomplete' })).toBe('already');
  });

  it('falls back to the generic refusal for anything else', () => {
    expect(core.refusalKind({ started: false, reason: 'unknown-region' })).toBe('failed');
    expect(core.refusalKind({ started: false, reason: 'offline' })).toBe('failed');
    expect(core.refusalKind(null)).toBe('failed');
  });
});
