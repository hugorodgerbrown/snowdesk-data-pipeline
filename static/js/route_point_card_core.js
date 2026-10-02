/*
 * static/js/route_point_card_core.js — the point card's pure half: the
 * words for one point on a saved route (SNOW-1064).
 *
 * The point card (templates/includes/_route_point_card.html) is pinned to
 * the map's top-left corner while a route is open. It holds the aspect
 * wheel (aspect_wheel_core.js) and two lines of words, and this module
 * is where the words come from. It says WHAT IT IS LIKE to be on the track
 * at that point, and nothing the map already shows: no degrees, no
 * headings, no aspects (docs/decisions/the-point-card-names-the-experience.md).
 *
 * ## The headline: the track's steepness and how it crosses the slope
 *
 * The track has its own five-step scale (`trackWord`), because on the EAWS
 * slope classes almost every skin track is "moderate": level under 5°,
 * gentle under 15°, moderate under 25°, steep under 35°, very steep from
 * 35°. The bounds are the aspect wheel's `TRACK_STEPS`, so the inner
 * ring's colour and the headline's word always name the same step.
 *
 * How it crosses the slope (`headline`) compares the track's heading with
 * the way the ground falls — the segment's aspect, its downhill direction.
 * d is the angle between the two, 0° straight down the fall line and 180°
 * straight up it:
 *
 *   d under 45°        — a fall line descent;
 *   d over 135°        — a fall line climb;
 *   anything between   — a traverse: rising, level or descending, by the
 *                        track's own gradient.
 *
 * A gradient that disagrees with the heading — climbing within 45° of
 * downhill, or descending within 45° of uphill — reads "…, turning": the
 * track turned inside the 25 m segment, so its heading and its rise were
 * measured on different halves of it. There is no middle band, so every
 * point reads as one or the other.
 *
 * The rule applies wherever an aspect exists, which is ground of 5° or
 * more. On flat or unsampled ground there is nothing to cross, and the
 * headline is the track alone: "Gentle descent", "Level track".
 *
 * THE ASPECT IS A SECTOR. The slope wire sends one of eight sectors per
 * segment (SNOW-976), not a bearing, so the aspect here is the sector's
 * centre and d carries up to 22.5° of that rounding. The 45° and 135°
 * edges are a reading of a 25 m segment's character, not a survey, and
 * the error is the same one the wheel's outer ring draws.
 *
 * ## Line two: the ground, in the EAWS words
 *
 * `groundWord` is the EAWS glossary's slope classes — moderate under 30°,
 * steep from 30°, very steep from 35°, extremely steep from 40° — with
 * flat under 5° added below them, the rule rail two's readout used.
 *
 * ## The gradient
 *
 * `segmentGradients` is the signed gradient along the track at each
 * segment: rise over run one stride (25 m) either side of its midpoint,
 * read off the heights rail one's profile draws — the terrain model's
 * since SNOW-1043 — and stopped at the leg's ends, so the first segment
 * down from a col is not averaged with the climb behind it. It moved here
 * from rail two with SNOW-1064, the gradient's only reader once rail two
 * went (SNOW-1065).
 *
 * No user-facing literal lives here: `reading` takes a strings object
 * read from the partial's `<template>` through `window.pwaStrings`, so
 * i18n-lint has nothing to find. Every function is pure; `reading` reads
 * `self.pwaAspectWheelCore` at call time for the wheel's state.
 *
 * Exports (frozen `self.pwaRoutePointCardCore`):
 *
 *   TRACK_BOUNDS                    — the track scale's upper bounds, degrees
 *   FALL_LINE_DEG                   — within this of the fall line, 45
 *   trackWord(gradient)             — the track's step, or null
 *   groundWord(angle)               — the ground's EAWS class, or null
 *   angleBetween(a, b)              — two bearings' separation, 0 to 180
 *   headingDeg(path)                — a segment path's bearing, first to last
 *   headline(heading, aspect, gradient, angle) — {key, steepness}
 *   segmentGradients(profile, sampleCount, spanM, legs?) — signed, per segment
 *   reading(input, strings)         — the card's words and the wheel's state
 */

// @ts-check

(function () {
  'use strict';

  /**
   * The track scale's exclusive upper bounds, in degrees of absolute
   * gradient: level, gentle, moderate, steep. Very steep has none.
   */
  const TRACK_BOUNDS = Object.freeze([5, 15, 25, 35]);

  /** The track scale's step names, one more than the bounds. */
  const TRACK_WORDS = Object.freeze(['level', 'gentle', 'moderate', 'steep', 'very-steep']);

  /** Within this many degrees of the fall line, the track is on it. */
  const FALL_LINE_DEG = 45;

  /** Ground under this is flat, and faces nowhere: the server's rule. */
  const FLAT_GROUND_DEG = 5;

  /** The EAWS slope-class edges above flat: steep, very, extremely. */
  const GROUND_STEEP_DEG = 30;
  const VERY_STEEP_DEG = 35;
  const EXTREMELY_STEEP_DEG = 40;

  /** A segment's length on the wire, metres: the sampler's stride. */
  const STRIDE_M = 25;

  /** Tolerance for comparing distances along the profile. */
  const EPSILON = 1e-6;

  /**
   * A headline: which pattern, and the track's step to fill it with.
   *
   * @typedef {{key: string, steepness: ?string}} Headline
   */

  /**
   * A finite number, or null.
   *
   * @param {*} value A candidate.
   * @returns {?number} The number, or null.
   */
  function finite(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }

  /**
   * Clamp a number into a closed range.
   *
   * @param {number} value The number.
   * @param {number} low The lowest value allowed.
   * @param {number} high The highest value allowed.
   * @returns {number} The clamped value.
   */
  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  /**
   * The track's step on its own scale.
   *
   * @param {*} gradient Degrees, signed or absolute.
   * @returns {?string} 'level', 'gentle', 'moderate', 'steep' or
   *   'very-steep'; null for an unknown gradient.
   */
  function trackWord(gradient) {
    const value = finite(gradient);
    if (value === null) return null;
    const steep = Math.abs(value);
    for (let i = 0; i < TRACK_BOUNDS.length; i += 1) {
      if (steep < TRACK_BOUNDS[i]) return TRACK_WORDS[i];
    }
    return TRACK_WORDS[TRACK_WORDS.length - 1];
  }

  /**
   * The ground's class, in the EAWS glossary's words.
   *
   *   < 5° 'flat' · < 30° 'moderate' · < 35° 'steep' · < 40° 'very-steep'
   *   · 40° and more 'extremely-steep'.
   *
   * @param {*} angle The slope angle, degrees.
   * @returns {?string} Null for an unknown angle.
   */
  function groundWord(angle) {
    const value = finite(angle);
    if (value === null) return null;
    if (value < FLAT_GROUND_DEG) return 'flat';
    if (value < GROUND_STEEP_DEG) return 'moderate';
    if (value < VERY_STEEP_DEG) return 'steep';
    return value < EXTREMELY_STEEP_DEG ? 'very-steep' : 'extremely-steep';
  }

  /**
   * The separation of two compass bearings.
   *
   * @param {number} a A bearing, degrees.
   * @param {number} b Another.
   * @returns {number} 0 to 180.
   */
  function angleBetween(a, b) {
    return Math.abs(((((a - b) % 360) + 540) % 360) - 180);
  }

  /**
   * A segment path's bearing, from its first point to its last.
   *
   * The chord, not one step: the headline describes the 25 m segment as
   * a whole, and the wheel's inner ring already lights its first and last
   * steps separately where it turns.
   *
   * @param {*} path The segment's `[lon, lat]` path.
   * @returns {?number} The bearing, or null with no length.
   */
  function headingDeg(path) {
    const wheel = self.pwaAspectWheelCore;
    if (!wheel || !Array.isArray(path)) return null;
    const points = path.filter((p) => Array.isArray(p) && finite(p[0]) !== null && finite(p[1]) !== null);
    if (points.length < 2) return null;
    const a = points[0];
    const b = points[points.length - 1];
    if (a[0] === b[0] && a[1] === b[1]) return null;
    return wheel.bearingDeg(a, b);
  }

  /**
   * The headline for one point: the track's step and how it crosses the
   * slope.
   *
   * @param {?number} heading The track's bearing, degrees; null with none.
   * @param {?number} aspect The way the ground falls, degrees; null where
   *   it faces nowhere (under 5°) or is unknown.
   * @param {?number} gradient The track's signed gradient, degrees.
   * @param {?number} angle The ground's angle, degrees.
   * @returns {Headline} `key` names the pattern; `steepness` the track's
   *   step, null for a pattern that carries none.
   */
  function headline(heading, aspect, gradient, angle) {
    const grade = finite(gradient);
    if (grade === null) return { key: 'no-height', steepness: null };
    const steepness = trackWord(grade);
    const level = steepness === 'level';
    const ground = finite(angle);
    const crossing = finite(heading) !== null && finite(aspect) !== null
      && ground !== null && ground >= FLAT_GROUND_DEG;
    if (!crossing) {
      if (level) return { key: 'level', steepness: null };
      return { key: grade > 0 ? 'climb' : 'descent', steepness: steepness };
    }
    if (level) return { key: 'level-traverse', steepness: null };
    const d = angleBetween(/** @type {number} */ (heading), /** @type {number} */ (aspect));
    const down = d < FALL_LINE_DEG;
    const up = d > 180 - FALL_LINE_DEG;
    if ((down && grade > 0) || (up && grade < 0)) {
      return { key: grade > 0 ? 'climb-turning' : 'descent-turning', steepness: steepness };
    }
    if (down) return { key: 'fall-descent', steepness: steepness };
    if (up) return { key: 'fall-climb', steepness: steepness };
    return { key: grade > 0 ? 'rising-traverse' : 'descending-traverse', steepness: steepness };
  }

  /**
   * The index of the profile run holding a distance, or -1 when the
   * distance falls in a gap or off either end.
   *
   * @param {Array<Array<{d: number, e: number}>>} runs The profile's runs.
   * @param {number} d A distance along the profile.
   * @returns {number} The run's index, or -1.
   */
  function runAt(runs, d) {
    for (let r = 0; r < runs.length; r += 1) {
      const run = runs[r];
      if (run.length && d >= run[0].d - EPSILON && d <= run[run.length - 1].d + EPSILON) {
        return r;
      }
    }
    return -1;
  }

  /**
   * A profile's height at a distance, linear between its points; null
   * outside every run.
   *
   * @param {Array<Array<{d: number, e: number}>>} runs The profile's runs.
   * @param {number} d A distance along the profile.
   * @returns {?number} The height, metres.
   */
  function heightAt(runs, d) {
    const r = runAt(runs, d);
    if (r < 0) return null;
    const run = runs[r];
    if (run.length === 1) return run[0].e;
    let lo = 0;
    let hi = run.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (run[mid].d <= d) lo = mid;
      else hi = mid;
    }
    const a = run[lo];
    const b = run[hi];
    const gap = b.d - a.d;
    return gap > 0 ? a.e + (b.e - a.e) * clamp((d - a.d) / gap, 0, 1) : a.e;
  }

  /**
   * The signed gradient along the track at each segment, in degrees:
   * positive where the track rises in its own direction (SNOW-1044).
   *
   * Rise over run between the heights one stride either side of the
   * segment's midpoint, clamped to the track's ends, read off
   * `readProfile`'s runs. The segment's midpoint sits on the profile by
   * share, as rail one places it. With `legs`, a segment is measured only
   * against ground in its own leg; a segment no leg holds keeps the whole
   * window. A window spanning an elevation gap is no reading: the gap is
   * kept as a gap rather than interpolated across.
   *
   * @param {?{runs: Array<Array<{d: number, e: number}>>, distanceM: number,
   *   hasElevation: boolean}} profile A `readProfile` result.
   * @param {number} sampleCount N, the length of `slope.angles`.
   * @param {number} spanM The route's length, rail one's `distance_m`.
   * @param {?Array<{from: number, to: number}>} [legs] The route's legs, in
   *   sample indices.
   * @returns {Array<?number>} N entries, null where a height is missing.
   */
  function segmentGradients(profile, sampleCount, spanM, legs) {
    const count = sampleCount > 0 ? Math.floor(sampleCount) : 0;
    /** @type {Array<?number>} */
    const out = new Array(count).fill(null);
    if (!profile || !profile.hasElevation || !(profile.distanceM > 0) || !count) return out;
    const distanceM = profile.distanceM;
    const half = STRIDE_M * (spanM > 0 ? distanceM / spanM : 1);
    /** @type {Array<?{from: number, to: number}>} */
    const owner = new Array(count).fill(null);
    (Array.isArray(legs) ? legs : []).forEach((leg) => {
      if (!leg || !Number.isInteger(leg.from) || !Number.isInteger(leg.to)) return;
      for (let s = Math.max(0, leg.from); s <= leg.to && s < count; s += 1) owner[s] = leg;
    });
    for (let i = 0; i < count; i += 1) {
      const mid = ((i + 0.5) / count) * distanceM;
      let a = Math.max(0, mid - half);
      let b = Math.min(distanceM, mid + half);
      const own = owner[i];
      if (own) {
        a = Math.max(a, (own.from / count) * distanceM);
        b = Math.min(b, ((own.to + 1) / count) * distanceM);
      }
      if (!(b - a > EPSILON)) continue;
      const run = runAt(profile.runs, a);
      if (run < 0 || run !== runAt(profile.runs, b)) continue;
      const ea = heightAt(profile.runs, a);
      const eb = heightAt(profile.runs, b);
      if (ea === null || eb === null) continue;
      out[i] = (Math.atan((eb - ea) / (b - a)) * 180) / Math.PI;
    }
    return out;
  }

  /**
   * A strings value with its placeholders filled.
   *
   * @param {Object<string, string>} strings The strings object.
   * @param {string} key The key.
   * @param {Object<string, string>} [values] The placeholders.
   * @returns {string} The text; the key itself when the strings lack it.
   */
  function fill(strings, key, values) {
    const template = strings[key] === undefined ? key : strings[key];
    const vars = values || {};
    return String(template).replace(/%\((\w+)\)s/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(vars, name) ? vars[name] : match
    ));
  }

  /**
   * The card's words for one point, and the wheel's state for it.
   *
   * @param {{index: number, paths: *, gradients: *, angles: *,
   *   aspects?: *}} input The point's segment and the route's arrays:
   *   `paths` from `segmentPaths`, `gradients` from `segmentGradients`,
   *   `angles` and `aspects` from the slope record.
   * @param {Object<string, string>} strings The strings object, read from
   *   the partial's `<template>`.
   * @returns {{headline: string, ground: string, label: string,
   *   state: *}} `label` is the wheel's accessible name: the two visible
   *   lines, joined.
   */
  function reading(input, strings) {
    const wheel = self.pwaAspectWheelCore;
    const state = wheel ? wheel.wheelState(input) : null;
    const index = input.index;
    const paths = Array.isArray(input.paths) ? input.paths : [];
    const angle = Array.isArray(input.angles) ? finite(input.angles[index]) : null;
    const terrain = state ? state.terrain : { kind: 'none' };
    const aspect = terrain && terrain.kind === 'faces' ? terrain.sector * 45 : null;
    const gradient = Array.isArray(input.gradients) ? finite(input.gradients[index]) : null;
    const head = headline(headingDeg(paths[index]), aspect, gradient, angle);
    const steepness = head.steepness ? fill(strings, `steepness-${head.steepness}`) : '';
    const headlineText = fill(strings, `headline-${head.key}`, { steepness: steepness });
    // A payload cached before SNOW-976 carries no `aspects`, and the wheel
    // draws it as unknown; the words agree rather than claim flat ground.
    const groundKey = terrain && (terrain.kind === 'unknown' || terrain.kind === 'none')
      ? null
      : groundWord(angle);
    const groundText = fill(strings, groundKey === null ? 'ground-unknown' : `ground-${groundKey}`);
    return {
      headline: headlineText,
      ground: groundText,
      label: fill(strings, 'label', { headline: headlineText, ground: groundText }),
      state: state,
    };
  }

  self.pwaRoutePointCardCore = Object.freeze({
    TRACK_BOUNDS: TRACK_BOUNDS,
    FALL_LINE_DEG: FALL_LINE_DEG,
    trackWord: trackWord,
    groundWord: groundWord,
    angleBetween: angleBetween,
    headingDeg: headingDeg,
    headline: headline,
    segmentGradients: segmentGradients,
    reading: reading,
  });
})();
