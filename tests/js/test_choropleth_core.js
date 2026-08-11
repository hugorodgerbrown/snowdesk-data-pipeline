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

describe('compositeOverBackdrop', () => {
  it('returns the foreground unchanged at full alpha', () => {
    // The point of the whole exercise: an opaque fill painted with this
    // colour is the colour it names, not the colour the basemap makes of it.
    expect(core.compositeOverBackdrop('#ff9900', 1)).toBe('#ff9900');
  });

  it('returns the backdrop at zero alpha', () => {
    expect(core.compositeOverBackdrop('#ff9900', 0)).toBe(core.BACKDROP_COLOUR);
  });

  it('blends each channel independently', () => {
    // #000000 at 0.5 over #f2f0ec → each channel halved and rounded.
    // 0xf2/2 = 121 = 0x79, 0xf0/2 = 120 = 0x78, 0xec/2 = 118 = 0x76.
    expect(core.compositeOverBackdrop('#000000', 0.5)).toBe('#797876');
  });

  it('pads a channel that blends below 0x10', () => {
    // Regression guard for the obvious hex-join bug: a channel of 9 must
    // render '09', not '9', or the whole string shifts and MapLibre gets a
    // 5-digit colour it will reject at style-validation time.
    expect(core.compositeOverBackdrop('#000000', 0.99)).toBe('#020202');
  });

  it('accepts the 3-digit shorthand', () => {
    expect(core.compositeOverBackdrop('#f00', 1)).toBe('#ff0000');
  });

  it('tolerates a missing leading hash', () => {
    expect(core.compositeOverBackdrop('ff0000', 1)).toBe('#ff0000');
  });

  it('throws on a colour it cannot parse', () => {
    // Loudly, not silently: a swallowed parse failure here would ship a
    // wrong danger colour, which is the exact class of bug this module was
    // extended to remove.
    expect(() => core.compositeOverBackdrop('rebeccapurple', 0.55)).toThrow();
    expect(() => core.compositeOverBackdrop('#ff99', 0.55)).toThrow();
  });

  it('keeps the danger scale distinguishable at the resting weight', () => {
    // The five EAWS colours as map.js paints them, blended at the resting
    // weight. If two of them ever collapsed onto the same output the map
    // would be lying about the danger level, so pin that they don't.
    const RATINGS = ['#ccff66', '#ffff00', '#ff9900', '#ff0000', '#a500a5'];
    const blended = RATINGS.map((c) => core.compositeOverBackdrop(c, 0.55));

    expect(new Set(blended).size).toBe(RATINGS.length);
    for (const colour of blended) expect(colour).toMatch(/^#[0-9a-f]{6}$/);
  });
});

describe('repaintDateForStyleSwap', () => {
  it('prefers the committed date over the URL param', () => {
    // The scrubber can commit a date silently while leaving ?d= pointing
    // at the day the visitor deep-linked to and has since scrubbed away
    // from. What is on screen wins.
    expect(core.repaintDateForStyleSwap('2026-05-18', '2026-04-09'))
      .toBe('2026-05-18');
  });

  it('uses the committed date when there is no URL param', () => {
    // The regression this exists for. `?d=` is absent on the ordinary
    // visit — the scrubber strips it for today and never writes one for
    // the out-of-season snap — and the old code did nothing at all here,
    // so every region went grey the moment the basemap changed.
    expect(core.repaintDateForStyleSwap('2026-05-18', null)).toBe('2026-05-18');
  });

  it('falls back to the URL param before anything has committed', () => {
    expect(core.repaintDateForStyleSwap(null, '2026-04-09')).toBe('2026-04-09');
  });

  it('returns null when neither is known', () => {
    // Signals "no date" to the caller. SNOW-660: the caller now paints
    // NOTHING on that answer rather than falling back to a boot frame —
    // neither known means no day has been asked for, and a style swap has
    // already wiped feature-state, so an uncoloured map is the correct one.
    expect(core.repaintDateForStyleSwap(null, null)).toBeNull();
  });

  it('treats an empty string as absent rather than as a date', () => {
    expect(core.repaintDateForStyleSwap('', '2026-04-09')).toBe('2026-04-09');
    expect(core.repaintDateForStyleSwap('', '')).toBeNull();
  });
});
