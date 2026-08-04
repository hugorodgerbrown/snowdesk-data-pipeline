/*
 * tests/js/test_choropleth_core.js — the collapsed choropleth paint
 * (SNOW-623).
 *
 * Three routines in `map.js` became one. They were not interchangeable,
 * and the whole risk of the collapse is that it silently picks one
 * semantic for all three — so these tests assert that BOTH original
 * behaviours are still reachable, and that each is the one its call site
 * needs.
 *
 * `clearMissing: true`  — `repaintRegionsForDate`, on a date change.
 * `clearMissing: false` — `paintNewCountry` and `paintTodayRatings`, on a
 *                          country's geometry/ratings arriving.
 */

import { describe, expect, it, vi } from 'vitest';

import '../../static/js/choropleth_core.js';

const core = self.pwaChoroplethCore;

/** Mirrors `map.js`'s own lookup. */
const INT_TO_RATING = [
  'no_rating',
  'low',
  'moderate',
  'considerable',
  'high',
  'very_high',
];

/**
 * Build deps over a fixed region set, recording every paint.
 *
 * @param {string[]} regionIDs
 * @returns {{deps: object, painted: Map<string, string>, setRating: object}}
 */
function harness(regionIDs) {
  const featureById = {};
  regionIDs.forEach((id, i) => {
    featureById[id] = { id: i + 1 };
  });
  const painted = new Map();
  const setRating = vi.fn((featureId, rating) => painted.set(featureId, rating));
  return {
    deps: { featureById, intToRating: INT_TO_RATING, setRating },
    painted,
    setRating,
  };
}

describe('clearMissing: true — the date-change semantic', () => {
  it('paints a region the frame omits back to no_rating', () => {
    const { deps, painted } = harness(['CH-1', 'CH-2']);

    core.paintRatingsFrame(deps, { 'CH-1': 3 }, { clearMissing: true });

    expect(painted.get(1)).toBe('considerable');
    // Without this, scrubbing to a day CH-2 has no rating for would leave
    // the previous day's colour showing on it.
    expect(painted.get(2)).toBe('no_rating');
  });

  it('touches every known region, even for an empty frame', () => {
    const { deps, setRating, painted } = harness(['CH-1', 'CH-2', 'CH-3']);

    const count = core.paintRatingsFrame(deps, {}, { clearMissing: true });

    expect(count).toBe(3);
    expect(setRating).toHaveBeenCalledTimes(3);
    expect([...painted.values()]).toEqual(['no_rating', 'no_rating', 'no_rating']);
  });

  it('treats a null frame as "clear everything"', () => {
    const { deps, painted } = harness(['CH-1']);

    core.paintRatingsFrame(deps, null, { clearMissing: true });

    expect(painted.get(1)).toBe('no_rating');
  });
});

describe('clearMissing: false — the country-load semantic', () => {
  it('leaves a region the frame omits untouched', () => {
    const { deps, setRating, painted } = harness(['CH-1', 'AT-1']);

    core.paintRatingsFrame(deps, { 'AT-1': 2 }, { clearMissing: false });

    expect(painted.get(2)).toBe('moderate');
    // This is the whole point: a per-country frame names only its own
    // regions, so clearing the rest would wipe every country already
    // painted.
    expect(setRating).toHaveBeenCalledTimes(1);
    expect(painted.has(1)).toBe(false);
  });

  it('skips a rating for a region whose geometry has not loaded', () => {
    const { deps, setRating } = harness(['CH-1']);

    // Ratings and GeoJSON arrive independently; there is nothing on the
    // map to paint for FR-9 yet, and the country-load path repaints once
    // there is.
    const count = core.paintRatingsFrame(
      deps,
      { 'CH-1': 1, 'FR-9': 4 },
      { clearMissing: false },
    );

    expect(count).toBe(1);
    expect(setRating).toHaveBeenCalledTimes(1);
  });

  it('paints nothing for an empty frame', () => {
    const { deps, setRating } = harness(['CH-1', 'CH-2']);

    expect(core.paintRatingsFrame(deps, {}, { clearMissing: false })).toBe(0);
    expect(setRating).not.toHaveBeenCalled();
  });
});

describe('the two semantics are genuinely different', () => {
  it('produces different results for the same frame', () => {
    const cleared = harness(['CH-1', 'CH-2']);
    const kept = harness(['CH-1', 'CH-2']);
    const frame = { 'CH-1': 5 };

    core.paintRatingsFrame(cleared.deps, frame, { clearMissing: true });
    core.paintRatingsFrame(kept.deps, frame, { clearMissing: false });

    // If a future change collapsed these onto one behaviour, this is the
    // assertion that would catch it.
    expect(cleared.setRating).toHaveBeenCalledTimes(2);
    expect(kept.setRating).toHaveBeenCalledTimes(1);
  });

  it('defaults to not clearing when the option is omitted', () => {
    const { deps, setRating } = harness(['CH-1', 'CH-2']);

    // Callers are expected to pass it explicitly — the module header says
    // why — but an omitted flag must fail safe. Not clearing leaves a
    // stale colour; clearing by accident would wipe other countries, which
    // is the worse of the two.
    core.paintRatingsFrame(deps, { 'CH-1': 1 }, undefined);

    expect(setRating).toHaveBeenCalledTimes(1);
  });
});

describe('rating lookup', () => {
  it('maps every int in the scale', () => {
    const { deps, painted } = harness(['r0', 'r1', 'r2', 'r3', 'r4', 'r5']);

    core.paintRatingsFrame(
      deps,
      { r0: 0, r1: 1, r2: 2, r3: 3, r4: 4, r5: 5 },
      { clearMissing: false },
    );

    expect([...painted.values()]).toEqual(INT_TO_RATING);
  });

  it('falls back to no_rating for an int outside the scale', () => {
    const { deps, painted } = harness(['CH-1']);

    core.paintRatingsFrame(deps, { 'CH-1': 99 }, { clearMissing: false });

    expect(painted.get(1)).toBe('no_rating');
  });

  it('paints rating 0 as no_rating, not as a falsy hole', () => {
    // `INT_TO_RATING[0]` is 'no_rating', which is truthy — so the two
    // original routines' different null checks (`== null` vs `||`) agreed.
    // Pinning that, since a future scale change could break it.
    const { deps, painted } = harness(['CH-1']);

    core.paintRatingsFrame(deps, { 'CH-1': 0 }, { clearMissing: true });

    expect(painted.get(1)).toBe('no_rating');
  });
});
