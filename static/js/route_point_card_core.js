/*
 * static/js/route_point_card_core.js — the point card's pure half: the
 * words for one point on a saved route (SNOW-1064; headings since
 * SNOW-1069; the Track and Slope lines since SNOW-1094).
 *
 * The point header (templates/includes/_route_point_card.html) is the
 * route panel's header while a point is placed. It holds the aspect wheel
 * (aspect_wheel_core.js) and two lines of words, labelled Skier's track
 * and Terrain slope in the partial (Track and Slope until 2026-10-19), and this module is where the words come from
 * (docs/decisions/the-point-card-names-the-experience.md).
 *
 * ## The Skier's track line: heading · kind · band
 *
 * "NW → W · Descent · 5–15°". The heading, the kind and the track's band
 * of degrees, joined by the strings' `join` and `with-band` (templates,
 * because the strings reader trims, so a bare " · " would lose its
 * spaces):
 *
 *   HEADING    — the compass point the segment heads in, or the first and
 *                last steps' points joined by an arrow where it turns:
 *                the inner ring's own sectors (`headingSectors`), so the
 *                words name exactly what the ring lights (SNOW-1069).
 *   KIND       — ascent, descent, traverse or fall line (`kindOf`). Up or
 *                down is the wheel's centre mark, not a word, so the kind
 *                names only how the track lies on the slope. A track with
 *                no kind is level, and reads "Level" alone.
 *   BAND       — the range of the track's step on its own scale
 *                (`trackBand`), never the measured gradient. Level under
 *                5°, then by direction: descending, 5 / 15 / 25 / 35°;
 *                climbing, 5 / 10 / 15 / 20°, because skin tracks live
 *                under 20°. The bounds are the aspect wheel's
 *                `TRACK_BOUNDS`, so the inner ring's colour, the chevrons
 *                and the band always name the same step.
 *
 * The track carried a word for its step until the usability review
 * (2026-10-19): gentle, moderate, steep, very steep. They were the Slope
 * line's EAWS words on other bounds, so "Moderate" over "Moderate" named
 * two ranges and "Steep fall line" could sit over a "Moderate" slope. The
 * band says the step without borrowing a word. `trackWord` still names
 * the steps, for the level test and the wheel's tests.
 *
 * How the track lies on the slope (`headline`) compares its chord with the
 * way the ground falls — the segment's aspect, its downhill direction. d
 * is the angle between the two, 0° straight down the fall line and 180°
 * straight up it: under 45° or over 135° is the fall line, anything
 * between a traverse. A gradient that disagrees with the heading — climbing
 * within 45° of downhill — has turned inside the segment, and reads as the
 * plain ascent or descent.
 *
 * ## Turns: the segment's two ends (SNOW-1069, SNOW-1094)
 *
 * One chord cannot say that a segment turned, and on a skin track the
 * kick turns are 50 m apart: a 25 m segment often holds one. A segment
 * whose first and last steps head into different compass sectors — the
 * ring lighting two sectors, adjoining or not — turned. A wobble that
 * stays inside one sector (359° to 1°) did not.
 *
 * A turning segment has no kind of its own (SNOW-1094). The two compass
 * points already say it turned, and the Slope line's "left, then right"
 * already says it crossed the fall line, so "turn" and "switchback"
 * repeated the card. It is the FALL LINE where it descends with either
 * end in the ground's own sector, otherwise an ASCENT or DESCENT by its
 * gradient's sign; a level turn has no kind.
 *
 * Its chord says nothing reliable about the side the ground falls — a
 * bend's chord can point down the slope while both ends traverse it — so
 * the Slope line takes the side from the two ends (`turnOf`): one side
 * where they agree, "left, then right" where they differ. Reading the
 * chord's side left one bend near Les Tsamos with a side on one route
 * and none on another, because the two routes split it into segments
 * with different chords. The sides need an aspect, which is ground of 5°
 * or more.
 *
 * THE ASPECT IS A SECTOR. The slope wire sends one of eight sectors per
 * segment (SNOW-976), not a bearing, so the aspect here is the sector's
 * centre and d carries up to 22.5° of that rounding. The 45° and 135°
 * edges, and where a turn crosses the fall line, are a reading of a 25 m
 * segment's character, not a survey, and the error is the same one the
 * wheel's outer ring draws.
 *
 * ## The Terrain slope line: the ground, in the EAWS words
 *
 * `groundWord` is the EAWS glossary's slope classes — moderate under 30°,
 * steep from 30°, very steep from 35°, extremely steep from 40° — with
 * flat under 5° added below them, the rule rail two's readout used. The
 * label says "Slope", so the words do not: "Steep", not "Steep slope".
 * The class's band follows it, "Steep · 30–35°" (`groundBand`), as the
 * track's follows its kind, so the two scales read side by side. A
 * traverse adds the side the ground falls away to, and a turning segment
 * its ends' side or sides, except on the fall line itself.
 *
 * ## The accessible name
 *
 * The wheel's label is both lines with their labels, "Skier's track: …;
 * Terrain slope: …", read rather than drawn: the heading's arrow is "to"
 * (`label-heading-pair`), the parts are joined by `label-join`, and a band
 * is "30 to 35 degrees" (`label-band-*`), so a screen reader never says
 * "right arrow", "dot" or "en dash".
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
 *   TRACK_BOUNDS                    — the track scale's upper bounds, {climb, descent}
 *   FALL_LINE_DEG                   — within this of the fall line, 45
 *   trackWord(gradient)             — the track's step, by direction, or null
 *   trackBand(gradient)             — that step's {low, high} degrees, or null
 *   groundWord(angle)               — the ground's EAWS class, or null
 *   groundBand(angle)               — that class's {low, high} degrees, or null
 *   angleBetween(a, b)              — two bearings' separation, 0 to 180
 *   headingDeg(path)                — a segment path's bearing, first to last
 *   fallSide(heading, aspect)       — 'left' or 'right', the way the ground falls
 *   headline(heading, aspect, gradient, angle) — {key, steepness, side}
 *   turnOf(path, aspect)            — the sides the ground falls at both ends
 *   kindOf(head, gradient, track, fall) — the Track line's kind, or null
 *   segmentGradients(profile, sampleCount, spanM, legs?) — signed, per segment
 *   reading(input, strings)         — the card's words and the wheel's state
 */

// @ts-check

(function () {
  'use strict';

  /**
   * The track scale's exclusive upper bounds, in degrees of absolute
   * gradient: level, gentle, steady, stiff; very stiff has none. A climb
   * has its own, because skin tracks live under 20°. A copy of the aspect
   * wheel's `TRACK_BOUNDS`, which a test holds equal, so this module's
   * words need no wheel to read.
   */
  const TRACK_BOUNDS = Object.freeze({
    climb: Object.freeze([5, 10, 15, 20]),
    descent: Object.freeze([5, 15, 25, 35]),
  });

  /** The track scale's step names, one more than the bounds. */
  const TRACK_WORDS = Object.freeze(['level', 'gentle', 'steady', 'stiff', 'very-stiff']);

  /** Within this many degrees of the fall line, the track is on it. */
  const FALL_LINE_DEG = 45;

  /** Ground under this is flat, and faces nowhere: the server's rule. */
  const FLAT_GROUND_DEG = 5;

  /** The EAWS slope-class edges above flat: steep, very, extremely. */
  const GROUND_STEEP_DEG = 30;
  const VERY_STEEP_DEG = 35;
  const EXTREMELY_STEEP_DEG = 40;

  /** The ground's class edges, one fewer than `GROUND_WORDS`. */
  const GROUND_BOUNDS = Object.freeze([FLAT_GROUND_DEG, GROUND_STEEP_DEG, VERY_STEEP_DEG, EXTREMELY_STEEP_DEG]);

  /** The ground's classes, flat first. */
  const GROUND_WORDS = Object.freeze(['flat', 'moderate', 'steep', 'very-steep', 'extremely-steep']);

  /** A segment's length on the wire, metres: the sampler's stride. */
  const STRIDE_M = 25;

  /** Tolerance for comparing distances along the profile. */
  const EPSILON = 1e-6;

  /**
   * A headline: which pattern, the track's step to fill it with, and on
   * a traverse the side the ground falls away to.
   *
   * @typedef {{key: string, steepness: ?string, side: ?string}} Headline
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
   * The bounds a signed gradient is read on: the climb's where it rises,
   * the descent's otherwise.
   *
   * @param {number} gradient Degrees, signed.
   * @returns {ReadonlyArray<number>} The upper bounds.
   */
  function trackBounds(gradient) {
    return gradient > 0 ? TRACK_BOUNDS.climb : TRACK_BOUNDS.descent;
  }

  /**
   * A value's step on a scale of exclusive upper bounds.
   *
   * @param {number} value The value, already absolute.
   * @param {ReadonlyArray<number>} bounds The upper bounds.
   * @returns {number} 0 to `bounds.length`.
   */
  function stepOf(value, bounds) {
    const step = bounds.findIndex((bound) => value < bound);
    return step === -1 ? bounds.length : step;
  }

  /**
   * The track's step on its own scale, read by direction.
   *
   * @param {*} gradient Degrees, signed: positive climbs.
   * @returns {?string} 'level', 'gentle', 'steady', 'stiff' or
   *   'very-stiff'; null for an unknown gradient.
   */
  function trackWord(gradient) {
    const value = finite(gradient);
    if (value === null) return null;
    return TRACK_WORDS[stepOf(Math.abs(value), trackBounds(value))];
  }

  /**
   * A step's band of degrees: its lower and upper bounds, either null
   * where the scale is open at that end.
   *
   * @typedef {{low: ?number, high: ?number}} Band
   */

  /**
   * The band of degrees the track's step covers (2026-10-19).
   *
   * The step's range, not the measured gradient, so the band can never
   * disagree with the word or the ring's colour.
   *
   * @param {*} gradient Degrees, signed: positive climbs.
   * @returns {?Band} Null for an unknown gradient.
   */
  function trackBand(gradient) {
    const value = finite(gradient);
    if (value === null) return null;
    const bounds = trackBounds(value);
    const step = stepOf(Math.abs(value), bounds);
    return { low: step > 0 ? bounds[step - 1] : null, high: step < bounds.length ? bounds[step] : null };
  }

  /**
   * The band of degrees the ground's EAWS class covers (2026-10-19).
   *
   * @param {*} angle The slope angle, degrees.
   * @returns {?Band} Null for an unknown angle.
   */
  function groundBand(angle) {
    const value = finite(angle);
    if (value === null) return null;
    const step = stepOf(value, GROUND_BOUNDS);
    return {
      low: step > 0 ? GROUND_BOUNDS[step - 1] : null,
      high: step < GROUND_BOUNDS.length ? GROUND_BOUNDS[step] : null,
    };
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
    return GROUND_WORDS[stepOf(value, GROUND_BOUNDS)];
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
   * The chord, not one step: how the track lies on the slope describes the
   * 25 m segment as a whole. Its two ends are `turnOf`'s.
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
   * Which side of the skier the ground falls away to.
   *
   * The aspect's signed turn from the heading: clockwise (east of a
   * northward track) is the skier's right.
   *
   * @param {number} heading The track's bearing, degrees.
   * @param {number} aspect The way the ground falls, degrees.
   * @returns {string} 'right' or 'left'; 'right' straight ahead or behind,
   *   which a traverse never is.
   */
  function fallSide(heading, aspect) {
    const turn = ((((aspect - heading) % 360) + 540) % 360) - 180;
    return turn >= 0 ? 'right' : 'left';
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
   *   step, null for a pattern that carries none; `side` the way the
   *   ground falls on a traverse, null otherwise.
   */
  function headline(heading, aspect, gradient, angle) {
    const grade = finite(gradient);
    if (grade === null) return { key: 'no-height', steepness: null, side: null };
    const steepness = trackWord(grade);
    const level = steepness === 'level';
    const ground = finite(angle);
    const crossing = finite(heading) !== null && finite(aspect) !== null
      && ground !== null && ground >= FLAT_GROUND_DEG;
    if (!crossing) {
      if (level) return { key: 'level', steepness: null, side: null };
      return { key: grade > 0 ? 'climb' : 'descent', steepness: steepness, side: null };
    }
    const h = /** @type {number} */ (heading);
    const a = /** @type {number} */ (aspect);
    if (level) return { key: 'level-traverse', steepness: null, side: fallSide(h, a) };
    const d = angleBetween(h, a);
    const down = d < FALL_LINE_DEG;
    const up = d > 180 - FALL_LINE_DEG;
    if ((down && grade > 0) || (up && grade < 0)) {
      return { key: grade > 0 ? 'climb-turning' : 'descent-turning', steepness: steepness, side: null };
    }
    if (down) return { key: 'fall-descent', steepness: steepness, side: null };
    if (up) return { key: 'fall-climb', steepness: steepness, side: null };
    return {
      key: grade > 0 ? 'rising-traverse' : 'descending-traverse',
      steepness: steepness,
      side: fallSide(h, a),
    };
  }

  /**
   * The side the ground falls at a segment's first step and at its last.
   *
   * @typedef {{firstSide: string, lastSide: string}} Turn
   */

  /**
   * The bearings of a path's steps, in order, skipping any step with no
   * length.
   *
   * @param {*} path The segment's `[lon, lat]` path, unchecked.
   * @returns {Array<number>} One bearing per step that has a length.
   */
  function stepBearings(path) {
    const wheel = self.pwaAspectWheelCore;
    if (!wheel || !Array.isArray(path)) return [];
    const points = path.filter((p) => Array.isArray(p) && finite(p[0]) !== null && finite(p[1]) !== null);
    /** @type {Array<number>} */
    const out = [];
    for (let i = 0; i < points.length - 1; i += 1) {
      const a = points[i];
      const b = points[i + 1];
      if (a[0] === b[0] && a[1] === b[1]) continue;
      out.push(wheel.bearingDeg(a, b));
    }
    return out;
  }

  /**
   * The side the ground falls at each end of a segment (SNOW-1069).
   *
   * Where the two differ on a segment that turned, the track crossed the
   * fall line. On its own a difference proves nothing: a track along the
   * fall line that wobbles 2° either side of it differs too, which is why
   * the Slope line asks for two heading sectors as well.
   *
   * @param {*} path The segment's `[lon, lat]` path.
   * @param {?number} aspect The way the ground falls, degrees; null where
   *   it faces nowhere or is unknown.
   * @returns {?Turn} Null with no aspect or no step with a length.
   */
  function turnOf(path, aspect) {
    const fall = finite(aspect);
    const bearings = stepBearings(path);
    if (fall === null || !bearings.length) return null;
    return {
      firstSide: fallSide(bearings[0], fall),
      lastSide: fallSide(bearings[bearings.length - 1], fall),
    };
  }

  /**
   * The headline's kind: how the track lies on the slope.
   *
   * A segment that heads into one sector is named by its chord's pattern.
   * One that heads into two turned, and is named by its gradient rather
   * than its chord (SNOW-1094): descending with either end in the
   * ground's own sector it is the fall line, otherwise an ascent or a
   * descent by the gradient's sign.
   *
   * @param {Headline} head `headline`'s reading of the chord.
   * @param {?number} gradient The track's signed gradient, degrees.
   * @param {Array<number>} track The heading sectors the ring lights.
   * @param {?number} fall The sector the ground falls towards; null where
   *   it faces nowhere or is unknown.
   * @returns {?string} 'ascent', 'descent', 'traverse' or 'fall-line';
   *   null for a level track that turned or crosses no slope, or no
   *   height.
   */
  function kindOf(head, gradient, track, fall) {
    const grade = finite(gradient);
    if (grade === null || head.key === 'no-height') return null;
    if (Array.isArray(track) && track.length > 1) {
      if (trackWord(grade) === 'level') return null;
      if (grade < 0 && fall !== null && track.includes(fall)) return 'fall-line';
      return grade > 0 ? 'ascent' : 'descent';
    }
    switch (head.key) {
      case 'fall-descent':
      case 'fall-climb':
        return 'fall-line';
      case 'rising-traverse':
      case 'descending-traverse':
      case 'level-traverse':
        return 'traverse';
      case 'climb':
      case 'climb-turning':
        return 'ascent';
      case 'descent':
      case 'descent-turning':
        return 'descent';
      default:
        return null;
    }
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
   * The heading in words: one compass point, or the first and last
   * joined.
   *
   * @param {*} track The wheel's heading sectors.
   * @param {Object<string, string>} strings The strings object.
   * @param {string} pairKey `heading-pair` to draw, `label-heading-pair`
   *   to read aloud.
   * @returns {string} The heading; empty with none.
   */
  function headingWords(track, strings, pairKey) {
    if (!Array.isArray(track) || !track.length) return '';
    const names = track.map((s) => fill(strings, `compass-${s}`));
    if (names.length === 1) return names[0];
    return fill(strings, pairKey, { first: names[0], second: names[names.length - 1] });
  }

  /**
   * A text with its band of degrees after it, drawn and spoken.
   *
   * @param {string} text The word or phrase.
   * @param {?Band} band The band; none adds nothing.
   * @param {Object<string, string>} strings The strings object.
   * @returns {{drawn: string, spoken: string}} "Steep · 30–35°", and
   *   "Steep, 30 to 35 degrees" to read aloud.
   */
  function withBand(text, band, strings) {
    if (!band) return { drawn: text, spoken: text };
    let shape = 'range';
    if (band.low === null) shape = 'under';
    else if (band.high === null) shape = 'over';
    const values = { low: String(band.low), high: String(band.high) };
    return {
      drawn: fill(strings, 'with-band', { text: text, band: fill(strings, `band-${shape}`, values) }),
      spoken: fill(strings, 'label-with-band', { text: text, band: fill(strings, `label-band-${shape}`, values) }),
    };
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
   *   kind: ?string, state: *}} `headline` is the Track line and `ground`
   *   the Slope line; `label` is the wheel's accessible name, both lines
   *   with their labels as read aloud; `kind` the Track line's kind, or
   *   null.
   */
  function reading(input, strings) {
    const wheel = self.pwaAspectWheelCore;
    const state = wheel ? wheel.wheelState(input) : null;
    const index = input.index;
    const paths = Array.isArray(input.paths) ? input.paths : [];
    const angle = Array.isArray(input.angles) ? finite(input.angles[index]) : null;
    const terrain = state ? state.terrain : { kind: 'none' };
    // Flat ground has no sector, so the sides need ground of 5° or more.
    const aspect = terrain && terrain.kind === 'faces' ? terrain.sector * 45 : null;
    const gradient = Array.isArray(input.gradients) ? finite(input.gradients[index]) : null;
    const head = headline(headingDeg(paths[index]), aspect, gradient, angle);
    const turn = turnOf(paths[index], aspect);
    const track = state ? state.track : [];
    const kind = kindOf(head, gradient, track, aspect === null ? null : terrain.sector);

    // The kind and the step's band, "Descent · 15–25°"
    // (2026-10-19: the band replaced the step's word). A track with no
    // kind is level, and says so in a word: its band adds nothing.
    let phrase = { drawn: fill(strings, 'headline-no-height'), spoken: fill(strings, 'headline-no-height') };
    if (gradient !== null) {
      phrase = kind
        ? withBand(fill(strings, `kind-${kind}`), trackBand(gradient), strings)
        : { drawn: fill(strings, 'headline-level'), spoken: fill(strings, 'headline-level') };
    }
    const drawn = [headingWords(track, strings, 'heading-pair'), phrase.drawn].filter(Boolean);
    const spoken = [headingWords(track, strings, 'label-heading-pair'), phrase.spoken].filter(Boolean);
    const join = (/** @type {Array<string>} */ parts, /** @type {string} */ key) => parts
      .reduce((before, after) => fill(strings, key, { before: before, after: after }));
    const headlineText = join(drawn, 'join');
    const headlineSpoken = join(spoken, 'label-join');

    // A payload cached before SNOW-976 carries no `aspects`, and the wheel
    // draws it as unknown; the words agree rather than claim flat ground.
    const groundKey = terrain && (terrain.kind === 'unknown' || terrain.kind === 'none')
      ? null
      : groundWord(angle);
    const slope = groundKey === null
      ? withBand(fill(strings, 'ground-unknown'), null, strings)
      : withBand(fill(strings, `ground-${groundKey}`), groundBand(angle), strings);
    // A side needs ground that faces somewhere; the guard keeps an
    // unknown reading from claiming one. The fall line names none.
    let fallKey = null;
    if (groundKey !== null && groundKey !== 'flat' && kind !== 'fall-line') {
      if (Array.isArray(track) && track.length > 1) {
        // A turning segment's side is its two ends', never its chord's.
        if (turn && turn.firstSide === turn.lastSide) {
          fallKey = `ground-falling-${turn.firstSide}`;
        } else if (turn) {
          fallKey = `ground-falling-${turn.firstSide}-then-${turn.lastSide}`;
        }
      } else if (head.side) {
        fallKey = `ground-falling-${head.side}`;
      }
    }
    const groundText = fallKey ? fill(strings, fallKey, { ground: slope.drawn }) : slope.drawn;
    const groundSpoken = fallKey ? fill(strings, fallKey, { ground: slope.spoken }) : slope.spoken;
    return {
      headline: headlineText,
      ground: groundText,
      label: fill(strings, 'label', { headline: headlineSpoken, ground: groundSpoken }),
      kind: kind,
      state: state,
    };
  }

  self.pwaRoutePointCardCore = Object.freeze({
    TRACK_BOUNDS: TRACK_BOUNDS,
    FALL_LINE_DEG: FALL_LINE_DEG,
    trackWord: trackWord,
    trackBand: trackBand,
    groundWord: groundWord,
    groundBand: groundBand,
    angleBetween: angleBetween,
    headingDeg: headingDeg,
    fallSide: fallSide,
    headline: headline,
    turnOf: turnOf,
    kindOf: kindOf,
    segmentGradients: segmentGradients,
    reading: reading,
  });
})();
