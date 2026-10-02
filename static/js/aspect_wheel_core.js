/*
 * static/js/aspect_wheel_core.js — the aspect wheel: which way one route
 * segment heads, and which way the ground under it faces (SNOW-1063).
 *
 * Two concentric eight-sector compass rings, north up. The OUTER ring is
 * the terrain: the sector the ground faces is lit, filled with the slope
 * class of the ground's angle. The INNER ring is the track: the sector it
 * heads in is lit, filled on the TRACK SCALE (`trackFill`, SNOW-1064) —
 * level under 5°, gentle under 15°, moderate under 25°, steep under 35°,
 * very steep from 35° — because on the EAWS slope classes nearly every
 * skin track would read "under 30°" and draw blue, and the point card's
 * words name the track on this scale. The segments either side show in the
 * inner ring at 35% opacity, and a centre triangle says climbing (up) or
 * descending (down); a level track (under `LEVEL_DEG`, 5°) draws a bar.
 *
 * THE DATA. The aspect comes from `slope.aspects` (SNOW-976): one sector
 * index per segment, aligned with `angles`, null where the angle is
 * unknown or the ground is below 5°. The heading is read off the segment's
 * own path (`segmentPaths` in route_slope_core.js) — the bearing of its
 * first step and of its last, so a segment that turns inside itself lights
 * two sectors rather than one averaged between them. The gradient is rail
 * point card's `segmentGradients` (route_point_card_core.js), signed,
 * positive where the track rises.
 *
 * FOUR TERRAIN STATES, and they must not collapse:
 *
 *   - `faces`   — a sector and an angle: one outer sector lit.
 *   - `flat`    — the ground is below 5°: the outer ring is unlit, because
 *                 flat ground faces nowhere.
 *   - `unknown` — the segment was sampled with no answer: the whole outer
 *                 ring is the unknown grey.
 *   - `none`    — the payload has no `aspects` key, because it was cached
 *                 before SNOW-976. Drawn as `unknown`, never as `flat`: a
 *                 payload that says nothing about aspect must not claim
 *                 the ground is level.
 *
 * Colours are `var(--token)` strings, because the wheel is inline SVG and
 * can read custom properties. The outer ring's slope classes are
 * route_slope_core.js's CLASSES table, read at call time rather than
 * copied, so the wheel cannot drift from the line and the legend. The
 * inner ring's five track steps are `TRACK_STEPS` below.
 *
 * No user-facing literal lives here. `wheelLabel` and `headingLine` take a
 * strings object from the caller — read from the partial's `<template>`
 * through `window.pwaStrings` — so i18n-lint has nothing to find.
 *
 * Every function here is pure.
 *
 * Exports (frozen `self.pwaAspectWheelCore`):
 *
 *   SIZE_MIN / SIZE_MAX   — the size clamp, in CSS px
 *   LEVEL_DEG             — below this absolute gradient a track is level
 *   TRACK_STEPS           — the track scale: [upper bound, token] per step
 *   trackFill(gradient)   — the inner ring's fill for a gradient
 *   sectorOf(deg)         — a compass bearing's sector, 0 (N) to 7 (NW)
 *   bearingDeg(a, b)      — the forward azimuth from a to b, [lon, lat]
 *   headingSectors(path)  — a segment path's heading sectors, deduplicated
 *   wheelState(input)     — the cursor-to-values step for one segment
 *   aspectWheelSvg(opts)  — the wheel as SVG markup
 *   wheelLabel(s, str)    — the aria label, in words
 *   headingLine(s, str)   — the one-line summary under the wheel
 */

// @ts-check

(function () {
  'use strict';

  /** The smallest size the wheel draws at, in CSS px. */
  const SIZE_MIN = 16;

  /** The largest size the wheel draws at, in CSS px. */
  const SIZE_MAX = 200;

  /**
   * Below this absolute gradient, in degrees, the track is level: the
   * track scale's first step, and "Level" on the point card (SNOW-1064).
   */
  const LEVEL_DEG = 5;

  /**
   * The track scale (SNOW-1064): each step's exclusive upper bound in
   * degrees of absolute gradient, and the token it fills with. The last
   * step has no bound. The point card's `trackWord` reads the same bounds
   * (route_point_card_core.js), so a ring's colour and the card's word
   * always name the same step.
   *
   * @type {ReadonlyArray<[number, string]>}
   */
  const TRACK_STEPS = /** @type {ReadonlyArray<[number, string]>} */ (Object.freeze([
    Object.freeze([LEVEL_DEG, '--color-track-level']),
    Object.freeze([15, '--color-slope-gentle']),
    Object.freeze([25, '--color-slope-30']),
    Object.freeze([35, '--color-slope-35']),
    Object.freeze([Infinity, '--color-slope-40']),
  ]));

  /** Below this size, in CSS px, the centre is left empty. */
  const CENTRE_MIN_SIZE = 36;

  /** From this size, in CSS px, the gaps are 2 px rather than 1.5 px. */
  const WIDE_GAP_SIZE = 96;

  /** The opacity of a neighbouring segment's heading. */
  const NEIGHBOUR_OPACITY = 0.35;

  /** The opacity of a lit sector's keyline. */
  const KEYLINE_OPACITY = 0.55;

  /** The rings, in viewBox units: [inner radius, outer radius]. */
  const OUTER_RING = Object.freeze([34, 48]);
  const INNER_RING = Object.freeze([18, 31]);

  /** Half a sector's span, in degrees. */
  const HALF_SECTOR = 22.5;

  /** The token for a sector nothing lights. */
  const UNLIT = 'var(--color-card-hover)';

  /** The token the gaps are cut in. */
  const GAP = 'var(--color-card)';

  /** The token for the keyline and the centre mark. */
  const INK = 'var(--color-text-1)';

  /** The fallback for the unknown token, if route_slope_core is absent. */
  const UNKNOWN_TOKEN_FALLBACK = '--color-slope-unknown';

  /**
   * One neighbouring segment's heading.
   *
   * @typedef {{sector: number, gradeDeg: ?number}} Neighbour
   */

  /**
   * What the ground under the segment does.
   *
   * @typedef {{kind: 'faces', sector: number, slopeDeg: number}
   *   | {kind: 'flat'} | {kind: 'unknown'} | {kind: 'none'}} Terrain
   */

  /**
   * Everything the wheel draws for one segment.
   *
   * @typedef {object} WheelState
   * @property {Array<number>} track The segment's heading sectors, one or
   *   two; empty when its path has no length.
   * @property {?number} gradeDeg The segment's gradient, signed, positive
   *   where the track rises; null when no height is known.
   * @property {?Neighbour} prev The previous segment's arriving heading.
   * @property {?Neighbour} next The next segment's leaving heading.
   * @property {Terrain} terrain The ground.
   */

  /**
   * The compass sector a bearing falls in, 0 (N) to 7 (NW).
   *
   * Sector k spans k × 45° ± 22.5°, a boundary going clockwise: 22.5° is
   * NE and 337.5° is N. This is the server's `aspect_sector` rule
   * (apps/routes/services/fall_line.py), so a heading and an aspect bin
   * the same way.
   *
   * @param {*} deg A compass bearing, in degrees.
   * @returns {?number} The sector, or null for a non-finite input.
   */
  function sectorOf(deg) {
    if (typeof deg !== 'number' || !Number.isFinite(deg)) return null;
    const sector = Math.floor((deg + HALF_SECTOR) / 45) % 8;
    return (sector + 8) % 8;
  }

  /**
   * The forward azimuth from one point to another, 0 to 360.
   *
   * @param {Array<number>} a `[lon, lat]`, in degrees.
   * @param {Array<number>} b `[lon, lat]`, in degrees.
   * @returns {number} The compass bearing from a to b, in degrees.
   */
  function bearingDeg(a, b) {
    const rad = Math.PI / 180;
    const lat1 = a[1] * rad;
    const lat2 = b[1] * rad;
    const dLon = (b[0] - a[0]) * rad;
    const y = Math.sin(dLon) * Math.cos(lat2);
    const x = Math.cos(lat1) * Math.sin(lat2)
      - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon);
    const deg = Math.atan2(y, x) / rad;
    return (deg + 360) % 360;
  }

  /**
   * Whether a value is a usable `[lon, lat]` point.
   *
   * @param {*} point A candidate point.
   * @returns {boolean} True when both halves are finite numbers.
   */
  function isPoint(point) {
    return Array.isArray(point)
      && Number.isFinite(point[0]) && Number.isFinite(point[1]);
  }

  /**
   * Whether two points name the same place.
   *
   * @param {Array<number>} a A point.
   * @param {Array<number>} b Another.
   * @returns {boolean} True when longitude and latitude are equal.
   */
  function samePlace(a, b) {
    return a[0] === b[0] && a[1] === b[1];
  }

  /**
   * The bearing of a path's first step that has a length.
   *
   * @param {Array<Array<number>>} path The points, already checked.
   * @param {boolean} fromEnd Walk from the last point backwards.
   * @returns {?number} The bearing, or null when no step has a length.
   */
  function stepBearing(path, fromEnd) {
    for (let k = 0; k < path.length - 1; k += 1) {
      const i = fromEnd ? path.length - 2 - k : k;
      const a = path[i];
      const b = path[i + 1];
      if (!samePlace(a, b)) return bearingDeg(a, b);
    }
    return null;
  }

  /**
   * The sectors a segment heads in: its first step's and its last step's.
   *
   * A zero-length step is skipped, since it has no bearing. A segment
   * that turns inside itself answers two sectors, first step first; a
   * straight one answers one.
   *
   * @param {*} path The segment's `[lon, lat]` path, unchecked.
   * @returns {Array<number>} One or two sectors; empty when the path has
   *   no step with a length.
   */
  function headingSectors(path) {
    if (!Array.isArray(path)) return [];
    const points = path.filter(isPoint);
    const first = sectorOf(stepBearing(points, false));
    const last = sectorOf(stepBearing(points, true));
    const sectors = [];
    if (first !== null) sectors.push(first);
    if (last !== null && last !== first) sectors.push(last);
    return sectors;
  }

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
   * One neighbour's heading, or null where it adds nothing.
   *
   * @param {*} path The neighbour's path.
   * @param {*} grade The neighbour's gradient.
   * @param {boolean} arriving Read the last step (the previous segment,
   *   arriving) rather than the first (the next segment, leaving).
   * @param {Array<number>} track The current segment's sectors.
   * @returns {?Neighbour} The neighbour, or null when it has no heading or
   *   shares a sector with the current one.
   */
  function neighbour(path, grade, arriving, track) {
    const sectors = headingSectors(path);
    if (!sectors.length) return null;
    const sector = arriving ? sectors[sectors.length - 1] : sectors[0];
    if (track.indexOf(sector) !== -1) return null;
    return { sector: sector, gradeDeg: finite(grade) };
  }

  /**
   * What the ground under segment `index` does.
   *
   * @param {number} index The segment.
   * @param {*} angles The record's `angles`.
   * @param {*} aspects The record's `aspects`; undefined when the payload
   *   predates SNOW-976.
   * @returns {Terrain} The terrain state.
   */
  function terrainOf(index, angles, aspects) {
    if (!Array.isArray(aspects)) return { kind: 'none' };
    const angle = Array.isArray(angles) ? finite(angles[index]) : null;
    if (angle === null) return { kind: 'unknown' };
    // ASPECT_FLAT_DEG on the server: below it the aspect is null by rule.
    if (angle < 5) return { kind: 'flat' };
    const sector = aspects[index];
    if (!Number.isInteger(sector) || sector < 0 || sector > 7) return { kind: 'unknown' };
    return { kind: 'faces', sector: sector, slopeDeg: angle };
  }

  /**
   * The cursor-to-values step: everything the wheel draws for one segment.
   *
   * Any surface that places a cursor on a route can call this; it needs
   * nothing but the arrays the route feed already sends.
   *
   * @param {{index: number, paths: *, gradients: *, angles: *,
   *   aspects?: *}} input `paths` from `segmentPaths`, `gradients` from
   *   route_point_card_core.js's `segmentGradients`, `angles` and `aspects` from the
   *   slope record.
   * @returns {WheelState} The state.
   */
  function wheelState(input) {
    const index = input.index;
    const paths = Array.isArray(input.paths) ? input.paths : [];
    const gradients = Array.isArray(input.gradients) ? input.gradients : [];
    const track = headingSectors(paths[index]);
    const prev = index > 0
      ? neighbour(paths[index - 1], gradients[index - 1], true, track)
      : null;
    const next = index < paths.length - 1
      ? neighbour(paths[index + 1], gradients[index + 1], false, track)
      : null;
    return {
      track: track,
      gradeDeg: finite(gradients[index]),
      prev: prev,
      next: next,
      terrain: terrainOf(index, input.angles, input.aspects),
    };
  }

  /**
   * The fill token for an angle on the slope-class scale.
   *
   * @param {?number} angle Degrees, or null.
   * @returns {string} `var(--token)`; the unknown token for a null angle.
   */
  function classFill(angle) {
    const slope = self.pwaRouteSlopeCore;
    const unknown = (slope && slope.UNKNOWN_TOKEN) || UNKNOWN_TOKEN_FALLBACK;
    if (!slope) return `var(${unknown})`;
    const index = slope.classify(angle);
    return `var(${index === null ? unknown : slope.CLASSES[index].token})`;
  }

  /**
   * The fill token for a track gradient on the track scale (SNOW-1064).
   *
   * The inner ring's colour: the outer ring keeps `classFill`, because it
   * names the ground and the ground is classed by EAWS.
   *
   * @param {?number} gradient Degrees, signed or absolute, or null.
   * @returns {string} `var(--token)`; the unknown token for a null
   *   gradient.
   */
  function trackFill(gradient) {
    const value = finite(gradient);
    if (value === null) return classFill(null);
    const steep = Math.abs(value);
    const step = TRACK_STEPS.find((entry) => steep < entry[0]) || TRACK_STEPS[TRACK_STEPS.length - 1];
    return `var(${step[1]})`;
  }

  /**
   * A number rounded for SVG markup.
   *
   * @param {number} value A coordinate or length.
   * @returns {string} At most three decimals, no trailing zeros.
   */
  function fmt(value) {
    return String(Math.round(value * 1000) / 1000);
  }

  /**
   * A point on the wheel, north up and clockwise.
   *
   * @param {number} r The radius, in viewBox units.
   * @param {number} deg The compass bearing, in degrees.
   * @returns {string} `x y`.
   */
  function polar(r, deg) {
    const a = deg * Math.PI / 180;
    return `${fmt(50 + r * Math.sin(a))} ${fmt(50 - r * Math.cos(a))}`;
  }

  /**
   * An annular sector's path, inset by `inset` from every edge.
   *
   * The radial edges are offset as parallel lines, not rotated: a point
   * at radius r sits `asin(inset / r)` inside the edge's bearing, which is
   * exactly `inset` from the line. That keeps the keyline an even width
   * from the gap all round.
   *
   * @param {number} sector 0 (N) to 7 (NW).
   * @param {ReadonlyArray<number>} ring [inner radius, outer radius].
   * @param {number} inset The inset, in viewBox units; 0 for the sector.
   * @returns {string} The path's `d`.
   */
  function sectorPath(sector, ring, inset) {
    const centre = sector * 45;
    const rIn = ring[0] + inset;
    const rOut = ring[1] - inset;
    const outerTrim = Math.asin(Math.min(1, inset / rOut)) * 180 / Math.PI;
    const innerTrim = Math.asin(Math.min(1, inset / rIn)) * 180 / Math.PI;
    const o1 = centre - HALF_SECTOR + outerTrim;
    const o2 = centre + HALF_SECTOR - outerTrim;
    const i1 = centre - HALF_SECTOR + innerTrim;
    const i2 = centre + HALF_SECTOR - innerTrim;
    return `M${polar(rOut, o1)}A${fmt(rOut)} ${fmt(rOut)} 0 0 1 ${polar(rOut, o2)}`
      + `L${polar(rIn, i2)}A${fmt(rIn)} ${fmt(rIn)} 0 0 0 ${polar(rIn, i1)}Z`;
  }

  /**
   * Escape a string for an SVG attribute.
   *
   * @param {string} value The text.
   * @returns {string} The text, safe inside double quotes.
   */
  function escapeAttr(value) {
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  /**
   * The size, clamped; a non-number takes the smallest.
   *
   * @param {*} size The requested size, in CSS px.
   * @returns {number} The size the wheel draws at.
   */
  function clampSize(size) {
    if (typeof size !== 'number' || !Number.isFinite(size)) return SIZE_MIN;
    return Math.min(SIZE_MAX, Math.max(SIZE_MIN, size));
  }

  /**
   * The centre mark: a triangle for a climb or a descent, a bar for a
   * level track, nothing below 36 px or without a gradient.
   *
   * @param {?number} grade The signed gradient.
   * @param {number} size The clamped size, in CSS px.
   * @returns {string} The markup, possibly empty.
   */
  function centreMark(grade, size) {
    if (size < CENTRE_MIN_SIZE || grade === null) return '';
    const unit = 100 / size;
    const half = Math.max(3.2, size * 0.034) * unit;
    if (Math.abs(grade) < LEVEL_DEG) {
      const tall = 1.5 * unit;
      return `<rect data-centre="level" x="${fmt(50 - half)}" y="${fmt(50 - tall / 2)}"`
        + ` width="${fmt(half * 2)}" height="${fmt(tall)}" fill="${INK}"/>`;
    }
    const height = 1.25 * half;
    const up = grade > 0;
    const apex = up ? 50 - height / 2 : 50 + height / 2;
    const base = up ? 50 + height / 2 : 50 - height / 2;
    return `<path data-centre="${up ? 'climbing' : 'descending'}"`
      + ` d="M${fmt(50)} ${fmt(apex)}L${fmt(50 + half)} ${fmt(base)}`
      + `L${fmt(50 - half)} ${fmt(base)}Z" fill="${INK}"/>`;
  }

  /**
   * The aspect wheel as SVG markup.
   *
   * @param {{size?: number, state: WheelState, label?: string}} options
   *   `size` in CSS px, clamped to 16–200; `label` the aria label, already
   *   in words (see `wheelLabel`).
   * @returns {string} One `<svg role="img">` element.
   */
  function aspectWheelSvg(options) {
    const size = clampSize(options.size);
    const state = options.state;
    const unit = 100 / size;
    const gap = (size >= WIDE_GAP_SIZE ? 2 : 1.5) * unit;
    const keyline = unit;
    const inset = gap / 2 + keyline / 2;

    /** @type {Array<string>} */
    const outer = [];
    /** @type {Array<string>} */
    const inner = [];
    /** @type {Array<string>} */
    const keylines = [];

    /**
     * One sector, cut by the gap stroke.
     *
     * @param {Array<string>} into Where the markup goes.
     * @param {number} sector 0 to 7.
     * @param {ReadonlyArray<number>} ring The ring.
     * @param {string} fill The fill.
     * @param {string} attrs Extra attributes, leading space included.
     */
    function sector(into, sector, ring, fill, attrs) {
      into.push(`<path${attrs} d="${sectorPath(sector, ring, 0)}" fill="${fill}"`
        + ` stroke="${GAP}" stroke-width="${fmt(gap)}"/>`);
    }

    /**
     * A lit sector's keyline, inset inside the gap.
     *
     * @param {number} sector 0 to 7.
     * @param {ReadonlyArray<number>} ring The ring.
     * @param {number} opacity The keyline's opacity.
     */
    function keylineOf(sector, ring, opacity) {
      keylines.push(`<path d="${sectorPath(sector, ring, inset)}" fill="none"`
        + ` stroke="${INK}" stroke-opacity="${fmt(opacity)}"`
        + ` stroke-width="${fmt(keyline)}"/>`);
    }

    const terrain = state.terrain || { kind: 'none' };
    const unknownGround = terrain.kind === 'unknown' || terrain.kind === 'none';
    for (let k = 0; k < 8; k += 1) {
      if (unknownGround) {
        sector(outer, k, OUTER_RING, classFill(null), ' data-ring="terrain" data-lit="unknown"');
      } else if (terrain.kind === 'faces' && terrain.sector === k) {
        sector(outer, k, OUTER_RING, classFill(terrain.slopeDeg), ' data-ring="terrain" data-lit="faces"');
        keylineOf(k, OUTER_RING, KEYLINE_OPACITY);
      } else {
        sector(outer, k, OUTER_RING, UNLIT, ' data-ring="terrain"');
      }
    }

    const track = Array.isArray(state.track) ? state.track : [];
    const grade = finite(state.gradeDeg);
    const headingFill = trackFill(grade);

    // Where the previous and next segments head the same way, one sector
    // carries both, in the steeper of the two gradients.
    /** @type {Object<number, ?number>} */
    const neighbours = {};
    [state.prev, state.next].forEach((n) => {
      if (!n || track.indexOf(n.sector) !== -1) return;
      const steep = n.gradeDeg === null ? null : Math.abs(n.gradeDeg);
      if (!(n.sector in neighbours)) {
        neighbours[n.sector] = steep;
      } else {
        const held = neighbours[n.sector];
        if (held === null || (steep !== null && steep > held)) neighbours[n.sector] = steep;
      }
    });

    for (let k = 0; k < 8; k += 1) {
      if (track.indexOf(k) !== -1) {
        sector(inner, k, INNER_RING, headingFill, ' data-ring="track" data-lit="heading"');
        keylineOf(k, INNER_RING, KEYLINE_OPACITY);
      } else if (k in neighbours) {
        sector(inner, k, INNER_RING, trackFill(neighbours[k]),
          ` data-ring="track" data-lit="neighbour" fill-opacity="${NEIGHBOUR_OPACITY}"`);
        keylineOf(k, INNER_RING, KEYLINE_OPACITY * NEIGHBOUR_OPACITY);
      } else {
        sector(inner, k, INNER_RING, UNLIT, ' data-ring="track"');
      }
    }

    const label = options.label ? ` aria-label="${escapeAttr(options.label)}"` : '';
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"`
      + ` width="${fmt(size)}" height="${fmt(size)}" role="img"${label}>`
      + outer.join('') + inner.join('') + keylines.join('')
      + centreMark(grade, size)
      + '</svg>';
  }

  /**
   * A strings value with its placeholders filled.
   *
   * @param {Object<string, string>} strings The strings object.
   * @param {string} key The key.
   * @param {Object<string, string>} values The placeholders.
   * @returns {string} The text; the key itself when the strings lack it.
   */
  function fill(strings, key, values) {
    const template = strings[key] === undefined ? key : strings[key];
    return String(template).replace(/%\((\w+)\)s/g, (match, name) => (
      Object.prototype.hasOwnProperty.call(values, name) ? values[name] : match
    ));
  }

  /**
   * The heading in words: one compass point, or two joined.
   *
   * @param {Array<number>} track The heading sectors.
   * @param {Object<string, string>} strings The strings object.
   * @returns {?string} The heading, or null when there is none.
   */
  function headingWords(track, strings) {
    if (!Array.isArray(track) || !track.length) return null;
    const names = track.map((s) => fill(strings, `compass-${s}`, {}));
    if (names.length === 1) return names[0];
    return fill(strings, 'heading-pair', { first: names[0], second: names[1] });
  }

  /**
   * The aria label: the track in words, then the ground.
   *
   * "Heading NW, descending 25°; slope faces SW, 32°".
   *
   * @param {WheelState} state The state.
   * @param {Object<string, string>} strings The strings object, read from
   *   the partial's `<template>`.
   * @returns {string} The label.
   */
  function wheelLabel(state, strings) {
    const heading = headingWords(state.track, strings);
    const grade = finite(state.gradeDeg);
    let track;
    if (heading === null) {
      track = fill(strings, 'label-no-heading', {});
    } else if (grade === null) {
      track = fill(strings, 'label-heading', { heading: heading });
    } else if (Math.abs(grade) < LEVEL_DEG) {
      track = fill(strings, 'label-level', { heading: heading });
    } else {
      track = fill(strings, grade > 0 ? 'label-climbing' : 'label-descending', {
        heading: heading,
        grade: String(Math.round(Math.abs(grade))),
      });
    }
    return fill(strings, 'label', { track: track, terrain: terrainWords(state, strings, 'label') });
  }

  /**
   * The ground in words, for the label or the line.
   *
   * @param {WheelState} state The state.
   * @param {Object<string, string>} strings The strings object.
   * @param {string} prefix `label` or `line`.
   * @returns {string} The words.
   */
  function terrainWords(state, strings, prefix) {
    const terrain = state.terrain || { kind: 'none' };
    if (terrain.kind === 'faces') {
      return fill(strings, `${prefix}-faces`, {
        aspect: fill(strings, `compass-${terrain.sector}`, {}),
        slope: String(Math.round(terrain.slopeDeg)),
      });
    }
    if (terrain.kind === 'flat') return fill(strings, `${prefix}-flat`, {});
    return fill(strings, `${prefix}-unknown`, {});
  }

  /**
   * The line under the wheel: "Heading NW · slope faces SW".
   *
   * @param {WheelState} state The state.
   * @param {Object<string, string>} strings The strings object.
   * @returns {string} The line.
   */
  function headingLine(state, strings) {
    const heading = headingWords(state.track, strings);
    const track = heading === null
      ? fill(strings, 'label-no-heading', {})
      : fill(strings, 'line-heading', { heading: heading });
    return fill(strings, 'line', { track: track, terrain: terrainWords(state, strings, 'line') });
  }

  self.pwaAspectWheelCore = Object.freeze({
    SIZE_MIN: SIZE_MIN,
    SIZE_MAX: SIZE_MAX,
    LEVEL_DEG: LEVEL_DEG,
    TRACK_STEPS: TRACK_STEPS,
    trackFill: trackFill,
    sectorOf: sectorOf,
    bearingDeg: bearingDeg,
    headingSectors: headingSectors,
    wheelState: wheelState,
    aspectWheelSvg: aspectWheelSvg,
    wheelLabel: wheelLabel,
    headingLine: headingLine,
  });
})();
