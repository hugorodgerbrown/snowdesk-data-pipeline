/*
 * tests/js/test_route_rail_two_debug.js — rail two's staff debug rail
 * (static/js/route_rail_two.js).
 *
 * The partial renders `[data-route-rail-debug]` for staff only; with it
 * in the page rail two fetches the open route's rows from the staff
 * terrain table (`/_route-terrain/<uuid>/?format=json`) and fills one
 * field per figure behind the wedge under the cursor: where the segment
 * is, its two heights, the ground's aspect and angle, the track's
 * direction, the track's angle over the readout's 50 m window and over
 * the table's 125 m one (with the segments that one was summed over), the
 * bank in words, and how far the track points from the fall line.
 *
 * It lives apart from test_route_rail_two.js because the module reads the
 * element once, at load: that file's page has no debug rail, this one's
 * has. jsdom lays nothing out, so the lane falls back to 600 px.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import '../../static/js/i18n_strings.js';
import '../../static/js/route_cursor_core.js';
import '../../static/js/elevation_profile_core.js';
import '../../static/js/route_slope_core.js';
import '../../static/js/route_rail_core.js';
import '../../static/js/bank_ribbon_core.js';
import '../../static/js/route_rail_two_core.js';

const FIELDS = [
  'sample', 'km', 'heights', 'aspect', 'angle', 'bearing', 'track', 'smoothed', 'bank', 'fall',
];

document.body.innerHTML = `
  <section id="route-rail">
    <div data-route-rail-two hidden>
      <p data-route-rail-two-title></p>
      <div>
        <button type="button" data-route-rail-two-zoom="out"></button>
        <button type="button" data-route-rail-two-zoom="in"></button>
        <button type="button" data-route-rail-two-close></button>
      </div>
      <p data-route-rail-two-figures></p>
      <div>
        <svg data-route-rail-two-lane role="slider" tabindex="0"></svg>
        <div data-route-rail-two-legs hidden></div>
      </div>
      <div data-route-rail-two-readout-box>
        <div data-route-rail-two-readout></div>
      </div>
      <dl
        data-route-rail-debug
        data-url-template="/_route-terrain/00000000-0000-0000-0000-000000000000/?format=json"
      >
        ${FIELDS.map((field) => `<dd data-route-rail-debug-field="${field}">–</dd>`).join('')}
      </dl>
    </div>
  </section>
`;

await import('../../static/js/route_rail_two.js');

const two = window.pwaRouteRailTwo;
const UUID = '7f16de92-f3e3-4458-ad85-89aa7a573bba';

/** 100 samples of 25 m over a level track: leg 1 is 0–39, leg 2 40–99. */
const N = 100;
const SPAN_M = 2500;
const LEGS = [
  { i: 1, from: 0, to: 39, climbing: true },
  { i: 2, from: 40, to: 99, climbing: false },
];
const ANGLES = Array.from({ length: N }, () => 32);
const BANKS = Array.from({ length: N }, () => -20);

/**
 * @returns {Array<Array<number>>} A straight track losing 2 m a point,
 *   the points 19.3 m apart: a steady 5.9° descent.
 */
function track() {
  return Array.from({ length: 101 }, (_, i) => [7.4 + i / 4000, 46.1, 3000 - i * 2]);
}

/**
 * One staff-table row per sample, as `terrain_detail` writes them.
 *
 * @param {Object<number, object>} [overrides] Keys replacing a row's, by index.
 * @returns {Array<object>}
 */
function tableRows(overrides = {}) {
  return Array.from({ length: N }, (_, i) => {
    const leg = LEGS.find((l) => i >= l.from && i <= l.to);
    return {
      i,
      from_m: i * 25,
      length_m: 25,
      ele_from_m: 3000 - i * 2,
      ele_to_m: 2998 - i * 2,
      angle_deg: 32,
      aspect_deg: 226.9,
      bearing_deg: 327.5,
      track_gradient_deg: -4.6,
      track_gradient_rejected: false,
      track_gradient_from: Math.max(leg.from, i - 2),
      track_gradient_to: Math.min(leg.to, i + 2),
      roll_deg: -46.8,
      fall_line: 'crossing',
      unknown: null,
      ...(overrides[i] || {}),
    };
  });
}

/** @returns {Promise<void>} Once the pending fetch has settled. */
const settled = () => new Promise((resolve) => { setTimeout(resolve, 0); });

/** @returns {Object<string, string>} Each debug field's text, by name. */
function fields() {
  return Object.fromEntries(FIELDS.map((field) => [
    field,
    document.querySelector(`[data-route-rail-debug-field="${field}"]`).textContent,
  ]));
}

/**
 * Attach rail two to a fresh cursor, the staff table answering `rows`.
 *
 * @param {{rows?: ?Array<object>, uuid?: ?string, ok?: boolean}} [options]
 * @returns {Promise<{cursor: object, fetchSpy: Function}>}
 */
async function attach({ rows = tableRows(), uuid = UUID, ok = true } = {}) {
  const fetchSpy = vi.fn(() => Promise.resolve({ ok, json: () => Promise.resolve({ rows }) }));
  vi.stubGlobal('fetch', fetchSpy);
  const cursor = self.pwaRouteCursorCore.createRouteCursor(N);
  two.attach({
    cursor,
    slope: { angles: ANGLES, banks: BANKS, passages: [] },
    profile: self.pwaElevationProfileCore.readProfile(track()),
    legs: LEGS,
    sampleCount: N,
    spanM: SPAN_M,
    uuid,
  });
  await settled();
  return { cursor, fetchSpy };
}

beforeEach(() => {
  vi.spyOn(Date, 'now').mockImplementation(() => 1e6);
});

afterEach(() => {
  two.detach();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('the staff debug rail', () => {
  it('fetches the open route\'s rows from the staff terrain table', async () => {
    const { fetchSpy } = await attach();

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    expect(fetchSpy.mock.calls[0][0]).toBe(`/_route-terrain/${UUID}/?format=json`);
  });

  it('fills every figure behind the wedge under the cursor', async () => {
    const { cursor } = await attach();
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(60);

    expect(fields()).toEqual({
      sample: '60 · leg 2 #21',
      km: '1.500 km → 1.525 km',
      heights: '2880 → 2878 m',
      aspect: '226.9° SW',
      angle: '32°',
      bearing: '327.5° NW',
      // The track loses 2 m every 19.3 m: the readout's own figure, not
      // the table's.
      track: '-5.9°',
      smoothed: '-4.6° · #19..23',
      bank: 'Falling 46.8° to skier’s left',
      // 327.5° against 226.9°: 100.6° off the fall line.
      fall: '101°',
    });
  });

  it('says where the 50 m window is cut short at the leg\'s ends', async () => {
    const { cursor } = await attach();
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(40);
    expect(fields().track).toBe('-5.9° · 37.5 m');
    expect(fields().smoothed).toBe('-4.6° · #1..3');

    cursor.setIndex(99);
    expect(fields().track).toBe('-5.9° · 37.5 m');
    expect(fields().smoothed).toBe('-4.6° · #58..60');
  });

  it('names a segment of another leg by its leg, and any that contributed nothing', async () => {
    const rows = tableRows({
      // A window the table let run back into leg 1, one of its segments
      // rejected and one with no height.
      41: { track_gradient_from: 39, track_gradient_to: 43 },
      42: { track_gradient_rejected: true },
      43: { ele_to_m: null },
    });
    const { cursor } = await attach({ rows });
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(41);

    expect(fields().smoothed).toBe('-4.6° · L1#40..#4 less #3, #4');
  });

  it('reads the bank to the right, and level, from its sign', async () => {
    const rows = tableRows({ 60: { roll_deg: 0.8 }, 61: { roll_deg: 0 } });
    const { cursor } = await attach({ rows });
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(60);
    expect(fields().bank).toBe('Falling 0.8° to skier’s right');
    cursor.setIndex(61);
    expect(fields().bank).toBe('Level (0°)');
  });

  it('reads "–" in every field with no cursor, and again once the leg closes', async () => {
    const { cursor } = await attach();
    const blank = Object.fromEntries(FIELDS.map((field) => [field, '–']));

    cursor.openLeg(LEGS[1]);
    expect(fields()).toEqual(blank);

    cursor.setIndex(60);
    expect(fields().sample).not.toBe('–');

    cursor.closeLeg();
    expect(fields()).toEqual(blank);
  });

  it('keeps what the rail itself knows when the table does not answer', async () => {
    const { cursor } = await attach({ ok: false });
    cursor.openLeg(LEGS[1]);

    cursor.setIndex(60);

    expect(fields()).toEqual({
      sample: '60 · leg 2 #21',
      // The segment's share of the route's length.
      km: '1.500 km → 1.525 km',
      heights: '–',
      aspect: '–',
      angle: '32°',
      bearing: '–',
      track: '-5.9°',
      smoothed: '–',
      // The whole-degree bank the map is sent.
      bank: 'Falling 20° to skier’s left',
      fall: '–',
    });
  });

  it('asks the table nothing for a route with no uuid', async () => {
    const { cursor, fetchSpy } = await attach({ uuid: null });
    cursor.openLeg(LEGS[1]);
    cursor.setIndex(60);

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(fields().smoothed).toBe('–');
    expect(fields().sample).toBe('60 · leg 2 #21');
  });
});
