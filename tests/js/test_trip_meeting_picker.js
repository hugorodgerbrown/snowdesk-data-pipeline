/*
 * tests/js/test_trip_meeting_picker.js — the meeting pin's arithmetic
 * (SNOW-820) and its live address (SNOW-1214).
 *
 * The picker writes the map's centre into two hidden inputs and asks for
 * the spot's three word address each time the map settles. Everything
 * worth asserting about that is pure: what a coordinate rounds to, what
 * happens to a value outside the WGS-84 range, where the quick picks go,
 * and which answer the debounced lookup lets through. The map itself is
 * WebGL and belongs nowhere near jsdom.
 *
 * `trip_map.js` is imported first because the picker reads its frozen core
 * off `self` at its own top level, exactly as the page loads them.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_markers_core.js';
import '../../static/js/route_legs_core.js';
import '../../static/js/route_drawing_core.js';
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

describe('routeEnds', () => {
  it('answers the first and last points as [lon, lat]', () => {
    const payload = {
      route: { geometry: { coordinates: [[7.0, 46.0, 1500], [7.1, 46.1, 2000], [7.2, 46.2, 1800]] } },
    };

    expect(core.routeEnds(payload)).toEqual({ start: [7.0, 46.0], end: [7.2, 46.2] });
  });

  it('answers null for a route with no points', () => {
    expect(core.routeEnds({ route: { geometry: { coordinates: [] } } })).toBeNull();
    expect(core.routeEnds(null)).toBeNull();
  });
});

describe('boundsAroundPin', () => {
  it('is centred on the pin and still holds the whole route', () => {
    const route = [[7.0, 46.0], [7.2, 46.1]];

    expect(core.boundsAroundPin(route, 46.0, 7.0)).toEqual([[6.8, 45.9], [7.2, 46.1]]);
  });

  it('answers null with no route box', () => {
    expect(core.boundsAroundPin(null, 46.0, 7.0)).toBeNull();
  });
});

describe('createAddressLookup', () => {
  let states;

  /** A fetch that answers `body` (or rejects with `error`) once called. */
  function answering(body, { ok = true, error = null } = {}) {
    return vi.fn(() => (error
      ? Promise.reject(error)
      : Promise.resolve({ ok, status: ok ? 200 : 429, json: () => Promise.resolve(body) })));
  }

  function lookup(fetchImpl) {
    return core.createAddressLookup({
      url: '/trips/what3words/',
      fetch: fetchImpl,
      delay: 400,
      onState: (update) => states.push(update),
    });
  }

  beforeEach(() => {
    states = [];
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('drops a pending or in-flight answer once cancelled, as the map starts moving', async () => {
    let resolve;
    const fetchImpl = vi.fn(() => new Promise((done) => { resolve = done; }));
    const subject = lookup(fetchImpl);

    subject.request(46.08, 7.31);
    await vi.advanceTimersByTimeAsync(400);
    subject.cancel();
    resolve({ ok: true, status: 200, json: () => Promise.resolve({ words: 'old.square.here' }) });
    await vi.advanceTimersByTimeAsync(0);

    expect(states).toEqual([{ state: 'finding' }]);

    subject.request(46.09, 7.32);
    subject.cancel();
    await vi.advanceTimersByTimeAsync(400);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it('says it is finding at once and asks only once the map has settled', async () => {
    const fetchImpl = answering({ words: 'filled.count.soap', url: 'https://w3w.co/filled.count.soap' });
    const subject = lookup(fetchImpl);

    subject.request(46.08, 7.31);
    subject.request(46.09, 7.32);

    expect(states).toEqual([{ state: 'finding' }, { state: 'finding' }]);
    expect(fetchImpl).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(400);

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0][0]).toBe('/trips/what3words/?lat=46.09&lon=7.32');
    expect(states.at(-1)).toEqual({
      state: 'found',
      words: 'filled.count.soap',
      url: 'https://w3w.co/filled.count.soap',
    });
  });

  it('never lets an answer about an earlier spot through', async () => {
    let release;
    const slow = vi.fn(() => new Promise((resolve) => { release = resolve; }));
    const subject = lookup(slow);

    subject.request(46.08, 7.31);
    await vi.advanceTimersByTimeAsync(400);
    subject.request(46.09, 7.32);
    release({ ok: true, json: () => Promise.resolve({ words: 'stale.old.words' }) });
    await vi.advanceTimersByTimeAsync(0);

    expect(states.some((update) => update.words === 'stale.old.words')).toBe(false);
  });

  it('fails to the save-time line on an error, a bad status or no words', async () => {
    for (const fetchImpl of [
      answering(null, { error: new TypeError('offline') }),
      answering({ error: 'rate_limit_exceeded' }, { ok: false }),
      answering({ words: null, url: null }),
    ]) {
      states = [];
      lookup(fetchImpl).request(46.08, 7.31);
      await vi.advanceTimersByTimeAsync(400);

      expect(states.at(-1)).toEqual({ state: 'failed' });
    }
  });

  it('does not ask at all while the browser is offline', async () => {
    const fetchImpl = answering({ words: 'filled.count.soap' });
    const online = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);

    lookup(fetchImpl).request(46.08, 7.31);
    await vi.advanceTimersByTimeAsync(400);

    expect(fetchImpl).not.toHaveBeenCalled();
    expect(states.at(-1)).toEqual({ state: 'failed' });
    online.mockRestore();
  });
});
