/*
 * tests/js/test_trip_meeting_picker.js — the meeting pin's arithmetic
 * (SNOW-820).
 *
 * The picker's job is to keep a dragged marker and two number inputs
 * saying the same thing. Everything worth asserting about that is pure:
 * what a coordinate rounds to, what happens to a typed value outside the
 * WGS-84 range, and what the readout says. The map itself is WebGL and
 * belongs nowhere near jsdom.
 *
 * `trip_map.js` is imported first because the picker reads its frozen core
 * off `self` at its own top level, exactly as the page loads them.
 */

import { describe, expect, it } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_markers_core.js';
import '../../static/js/trip_map.js';
import '../../static/js/trip_meeting_picker.js';

const core = self.pwaTripMeetingPicker;

describe('roundCoordinate', () => {
  it('rounds to six decimal places', () => {
    // ~11 cm at the equator. Finer than anyone can point at, and short
    // enough that the number in the manual field stays readable.
    expect(core.roundCoordinate(46.08001234567)).toBe(46.080012);
  });

  it('accepts the string an input element actually holds', () => {
    // The manual fields are read via .value, which is always a string.
    expect(core.roundCoordinate('7.318197')).toBe(7.318197);
  });

  it('returns null rather than NaN for junk', () => {
    // A null propagates into "leave the pin alone"; a NaN would reach
    // setLngLat and put the marker nowhere at all.
    expect(core.roundCoordinate('')).toBeNull();
    expect(core.roundCoordinate('the car park')).toBeNull();
    expect(core.roundCoordinate(Infinity)).toBeNull();
    expect(core.roundCoordinate(undefined)).toBeNull();
  });
});

describe('clampLatitude / clampLongitude', () => {
  it('passes a real coordinate through untouched', () => {
    expect(core.clampLatitude(46.080012)).toBe(46.080012);
    expect(core.clampLongitude(7.318197)).toBe(7.318197);
  });

  it('pins a typo at the edge instead of wrapping it', () => {
    // Wrapping 200 to -160 would move the meeting point half a world away
    // and look deliberate. The edge is visibly wrong, which is the point.
    expect(core.clampLongitude(200)).toBe(180);
    expect(core.clampLongitude(-200)).toBe(-180);
    expect(core.clampLatitude(95)).toBe(90);
    expect(core.clampLatitude(-95)).toBe(-90);
  });

  it('returns null for junk, so the caller can decline to move the pin', () => {
    expect(core.clampLatitude('north a bit')).toBeNull();
    expect(core.clampLongitude(null)).toBeNull();
  });
});

describe('formatCoordinate', () => {
  it('reads back at full stored precision', () => {
    // Trailing zeros are kept: a readout that shortened 7.300000 to 7.3
    // would not match the value in the field beneath it.
    expect(core.formatCoordinate(46.08, 7.3)).toBe('46.080000, 7.300000');
  });

  it('is empty when either half is unusable', () => {
    expect(core.formatCoordinate(46.08, 'x')).toBe('');
    expect(core.formatCoordinate(undefined, 7.3)).toBe('');
  });
});

describe('readPayload', () => {
  it('parses the inline payload', () => {
    const el = document.createElement('script');
    el.type = 'application/json';
    el.id = 'trip-meeting-payload';
    el.textContent = JSON.stringify({ meeting: [7.4, 46.1], bounds: [1, 2, 3, 4] });
    document.body.appendChild(el);

    expect(core.readPayload(document).meeting).toEqual([7.4, 46.1]);

    document.body.removeChild(el);
  });

  it('returns null when absent or unparseable', () => {
    expect(core.readPayload(document)).toBeNull();

    const el = document.createElement('script');
    // `type` matters: jsdom EXECUTES a bare <script>, so a typeless one
    // holding malformed JSON throws out of the DOM rather than reaching
    // the parser this test is about.
    el.type = 'application/json';
    el.id = 'trip-meeting-payload';
    el.textContent = '{not json';
    document.body.appendChild(el);

    expect(core.readPayload(document)).toBeNull();

    document.body.removeChild(el);
  });
});
