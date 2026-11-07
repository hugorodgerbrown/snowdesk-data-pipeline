// @ts-check
/*
 * static/js/map_3d_flight_core.js — a 3D fly-past's camera path (SNOW-1466).
 *
 * A fly-past flies the camera along a route in /map/'s 3D view while the
 * line draws itself: the promo video's recorder (bin/record-3d-flyby)
 * steps it frame by frame, and a later "Fly this route" control will play
 * it live. Both read the same path from here, so it is a pure function of
 * time with no map and no DOM, and Vitest can pin it
 * (docs/decisions/a-fly-past-is-rendered-frame-by-frame.md).
 *
 * buildFlight(coords, options) takes a LineString's or MultiLineString's
 * coordinates (the parts joined end to end), drops repeated points and
 * densifies the line with a centripetal Catmull-Rom spline, so neither the
 * drawn line nor the camera turns a corner on a single frame. It keeps the
 * distance along the line in metres and precomputes the follow camera's
 * bearing table, limited to TURN_DEG_PER_S, which is what makes a frame
 * depend on `t` alone: the limit needs the bearing before, so the table
 * is walked once at build time instead of frame to frame.
 *
 * flightFrame(flight, t), for t in [0, 1], is the frame at that moment:
 *
 *   { phase, camera: {center, zoom, pitch, bearing},
 *     fit: {bounds, weight} | null, drawn: [[lng, lat], …], head }
 *
 * through four phases, each eased into the next:
 *
 *   - establish: high and wide over the route's middle, turning slowly;
 *   - descend: down onto the start, ending on the follow camera;
 *   - follow: the line draws to the head while the camera looks along it
 *     from behind and above, centred on a point LOOK_AHEAD_M ahead so the
 *     head sits in the lower third;
 *   - reveal: pull back until the whole route is in view. Only the map can
 *     fit bounds, so `fit` carries them with a weight easing from 0 to 1,
 *     and `camera` keeps the follow camera's centre and zoom with the
 *     pitch and bearing to end on. The driver (map_3d_flight.js) fits the
 *     bounds at that pitch and bearing and blends towards them by weight.
 *     Outside the reveal `fit` is null.
 *
 * Exposes window.pwaMap3dFlightCore:
 *   DEFAULTS / PHASES
 *   buildFlight(coords, options?) / flightFrame(flight, t)
 *   distanceM(a, b) / bearingDeg(a, b)
 */
(function () {
  'use strict';

  /** The mean Earth radius in metres, for distances along the line. */
  const EARTH_RADIUS_M = 6371008.8;

  /** The phases, in order. */
  const PHASES = Object.freeze(['establish', 'descend', 'follow', 'reveal']);

  /**
   * The defaults, sized for a 20 s clip of a route a few kilometres long.
   * `share` is each phase's part of the clip; it is normalised, so it need
   * not add up to 1.
   */
  const DEFAULTS = Object.freeze({
    seconds: 20,
    share: Object.freeze({ establish: 0.14, descend: 0.14, follow: 0.5, reveal: 0.22 }),
    establish: Object.freeze({ zoom: 11.6, pitch: 55, turnDeg: 20, bearingOffsetDeg: -35 }),
    follow: Object.freeze({ zoom: 14, pitch: 66 }),
    reveal: Object.freeze({ pitch: 60, bearingDeg: /** @type {?number} */ (null) }),
    lookAheadM: 450,
    bearingBehindM: 250,
    bearingAheadM: 350,
    turnDegPerS: 30,
    stepM: 10,
  });

  /** Steps in the follow camera's bearing table. */
  const BEARING_STEPS = 400;

  /**
   * @typedef {[number, number]} LngLat
   * @typedef {{center: LngLat, zoom: number, pitch: number, bearing: number}} Camera
   * @typedef {{bounds: [LngLat, LngLat], weight: number}} Fit
   * @typedef {{phase: string, camera: Camera, fit: ?Fit, drawn: LngLat[], head: LngLat}} Frame
   * @typedef {{
   *   points: LngLat[], cum: number[], length: number, bounds: [LngLat, LngLat],
   *   bearings: number[], options: any, cuts: number[]
   * }} Flight
   */

  /**
   * Great-circle distance in metres.
   *
   * @param {LngLat} a
   * @param {LngLat} b
   * @returns {number}
   */
  function distanceM(a, b) {
    const k = Math.PI / 180;
    const dLat = (b[1] - a[1]) * k;
    const dLng = (b[0] - a[0]) * k;
    const h = Math.sin(dLat / 2) ** 2
      + Math.cos(a[1] * k) * Math.cos(b[1] * k) * Math.sin(dLng / 2) ** 2;
    return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  /**
   * Initial bearing from a to b, degrees clockwise from north, in (-180, 180].
   *
   * @param {LngLat} a
   * @param {LngLat} b
   * @returns {number}
   */
  function bearingDeg(a, b) {
    const k = Math.PI / 180;
    const y = Math.sin((b[0] - a[0]) * k) * Math.cos(b[1] * k);
    const x = Math.cos(a[1] * k) * Math.sin(b[1] * k)
      - Math.sin(a[1] * k) * Math.cos(b[1] * k) * Math.cos((b[0] - a[0]) * k);
    return Math.atan2(y, x) / k;
  }

  /**
   * Ease in and out (smoothstep's cubic cousin).
   *
   * @param {number} u In [0, 1].
   * @returns {number}
   */
  function ease(u) {
    const x = Math.min(1, Math.max(0, u));
    return x < 0.5 ? 4 * x * x * x : 1 - ((-2 * x + 2) ** 3) / 2;
  }

  /**
   * @param {number} a
   * @param {number} b
   * @param {number} u
   * @returns {number}
   */
  function lerp(a, b, u) {
    return a + (b - a) * u;
  }

  /**
   * The shortest turn from a to b, in degrees, in (-180, 180].
   *
   * @param {number} a
   * @param {number} b
   * @returns {number}
   */
  function turn(a, b) {
    let d = (b - a) % 360;
    if (d > 180) d -= 360;
    if (d <= -180) d += 360;
    return d;
  }

  /**
   * Blend two cameras; the bearing takes the shorter way round.
   *
   * @param {Camera} a
   * @param {Camera} b
   * @param {number} u
   * @returns {Camera}
   */
  function blend(a, b, u) {
    return {
      center: [lerp(a.center[0], b.center[0], u), lerp(a.center[1], b.center[1], u)],
      zoom: lerp(a.zoom, b.zoom, u),
      pitch: lerp(a.pitch, b.pitch, u),
      bearing: a.bearing + turn(a.bearing, b.bearing) * u,
    };
  }

  /**
   * Merge options over DEFAULTS, one level deep for the phase groups.
   *
   * @param {any} options
   * @returns {any}
   */
  function merged(options) {
    const o = options || {};
    return {
      ...DEFAULTS,
      ...o,
      share: { ...DEFAULTS.share, ...(o.share || {}) },
      establish: { ...DEFAULTS.establish, ...(o.establish || {}) },
      follow: { ...DEFAULTS.follow, ...(o.follow || {}) },
      reveal: { ...DEFAULTS.reveal, ...(o.reveal || {}) },
    };
  }

  /**
   * The line's points, parts joined end to end, repeats dropped.
   *
   * @param {any} coords A LineString's or MultiLineString's coordinates.
   * @returns {LngLat[]}
   */
  function flatten(coords) {
    if (!Array.isArray(coords) || coords.length === 0) return [];
    const parts = typeof coords[0][0] === 'number' ? [coords] : coords;
    /** @type {LngLat[]} */
    const out = [];
    for (const part of parts) {
      for (const p of part) {
        const point = /** @type {LngLat} */ ([Number(p[0]), Number(p[1])]);
        if (!Number.isFinite(point[0]) || !Number.isFinite(point[1])) continue;
        const last = out[out.length - 1];
        if (last && last[0] === point[0] && last[1] === point[1]) continue;
        out.push(point);
      }
    }
    return out;
  }

  /**
   * Densify with a centripetal Catmull-Rom spline (alpha 0.5), worked in a
   * local plane so a degree of longitude is not taken for a degree of
   * latitude. Each span gets a point about every `stepM` metres.
   *
   * @param {LngLat[]} pts At least two distinct points.
   * @param {number} stepM
   * @returns {LngLat[]}
   */
  function densify(pts, stepM) {
    const lat0 = pts[0][1];
    const sx = Math.cos((lat0 * Math.PI) / 180);
    /** @type {LngLat[]} */
    const plane = pts.map((p) => [p[0] * sx, p[1]]);
    /** @param {LngLat} a @param {LngLat} b */
    const knot = (a, b) => Math.sqrt(Math.hypot(b[0] - a[0], b[1] - a[1])) || 1e-9;
    /** @type {LngLat[]} */
    const out = [pts[0]];
    for (let i = 0; i < plane.length - 1; i += 1) {
      const p1 = plane[i];
      const p2 = plane[i + 1];
      // Mirror the ends so the first and last spans have a neighbour.
      const p0 = i > 0 ? plane[i - 1] : [2 * p1[0] - p2[0], 2 * p1[1] - p2[1]];
      const p3 = i + 2 < plane.length ? plane[i + 2] : [2 * p2[0] - p1[0], 2 * p2[1] - p1[1]];
      const t0 = 0;
      const t1 = t0 + knot(p0, p1);
      const t2 = t1 + knot(p1, p2);
      const t3 = t2 + knot(p2, p3);
      const span = distanceM(pts[i], pts[i + 1]);
      const n = Math.max(1, Math.ceil(span / stepM));
      for (let j = 1; j <= n; j += 1) {
        if (j === n) {
          out.push(pts[i + 1]);
          break;
        }
        const t = t1 + ((t2 - t1) * j) / n;
        /** @param {number} c */
        const at = (c) => {
          const a1 = ((t1 - t) / (t1 - t0)) * p0[c] + ((t - t0) / (t1 - t0)) * p1[c];
          const a2 = ((t2 - t) / (t2 - t1)) * p1[c] + ((t - t1) / (t2 - t1)) * p2[c];
          const a3 = ((t3 - t) / (t3 - t2)) * p2[c] + ((t - t2) / (t3 - t2)) * p3[c];
          const b1 = ((t2 - t) / (t2 - t0)) * a1 + ((t - t0) / (t2 - t0)) * a2;
          const b2 = ((t3 - t) / (t3 - t1)) * a2 + ((t - t1) / (t3 - t1)) * a3;
          return ((t2 - t) / (t2 - t1)) * b1 + ((t - t1) / (t2 - t1)) * b2;
        };
        out.push([at(0) / sx, at(1)]);
      }
    }
    return out;
  }

  /**
   * The point `d` metres along the line, clamped to its ends.
   *
   * @param {Flight} flight
   * @param {number} d
   * @returns {LngLat}
   */
  function pointAt(flight, d) {
    const { points, cum, length } = flight;
    if (d <= 0) return points[0];
    if (d >= length) return points[points.length - 1];
    let lo = 0;
    let hi = cum.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (cum[mid] <= d) lo = mid;
      else hi = mid;
    }
    const f = (d - cum[lo]) / (cum[hi] - cum[lo] || 1);
    return [lerp(points[lo][0], points[hi][0], f), lerp(points[lo][1], points[hi][1], f)];
  }

  /**
   * The line from its start to `d` metres along.
   *
   * @param {Flight} flight
   * @param {number} d
   * @returns {LngLat[]}
   */
  function lineTo(flight, d) {
    if (d <= 0) return [];
    if (d >= flight.length) return flight.points.slice();
    /** @type {LngLat[]} */
    const out = [];
    for (let i = 0; i < flight.points.length && flight.cum[i] < d; i += 1) out.push(flight.points[i]);
    out.push(pointAt(flight, d));
    return out;
  }

  /**
   * How far along the line the head is, `u` of the way through follow.
   *
   * @param {Flight} flight
   * @param {number} u
   * @returns {number}
   */
  function followDistance(flight, u) {
    return ease(u) * flight.length;
  }

  /**
   * The follow camera's bearing, `u` of the way through follow, read from
   * the rate-limited table.
   *
   * @param {Flight} flight
   * @param {number} u
   * @returns {number}
   */
  function followBearing(flight, u) {
    const x = Math.min(1, Math.max(0, u)) * BEARING_STEPS;
    const i = Math.min(BEARING_STEPS - 1, Math.floor(x));
    return lerp(flight.bearings[i], flight.bearings[i + 1], x - i);
  }

  /**
   * The follow camera, `u` of the way through follow.
   *
   * @param {Flight} flight
   * @param {number} u
   * @returns {Camera}
   */
  function followCamera(flight, u) {
    const o = flight.options;
    const d = followDistance(flight, u);
    return {
      center: pointAt(flight, d + o.lookAheadM),
      zoom: o.follow.zoom,
      pitch: o.follow.pitch,
      bearing: followBearing(flight, u),
    };
  }

  /**
   * The establish camera, `u` of the way through establish.
   *
   * @param {Flight} flight
   * @param {number} u
   * @returns {Camera}
   */
  function establishCamera(flight, u) {
    const o = flight.options;
    const [[w, s], [e, n]] = flight.bounds;
    const start = flight.bearings[0] + o.establish.bearingOffsetDeg;
    return {
      center: [(w + e) / 2, (s + n) / 2],
      zoom: o.establish.zoom,
      pitch: o.establish.pitch,
      bearing: start + o.establish.turnDeg * u,
    };
  }

  /**
   * Build a flight along a route.
   *
   * @param {any} coords A LineString's or MultiLineString's coordinates.
   * @param {any} [options] Overrides of DEFAULTS.
   * @returns {Flight}
   */
  function buildFlight(coords, options) {
    const raw = flatten(coords);
    if (raw.length < 2) throw new Error('A fly-past needs a line of at least two points');
    const o = merged(options);
    const points = densify(raw, o.stepM);
    const cum = [0];
    for (let i = 1; i < points.length; i += 1) cum.push(cum[i - 1] + distanceM(points[i - 1], points[i]));
    const length = cum[cum.length - 1];
    let [w, s, e, n] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const [x, y] of points) {
      w = Math.min(w, x); e = Math.max(e, x);
      s = Math.min(s, y); n = Math.max(n, y);
    }
    const total = PHASES.reduce((sum, p) => sum + Math.max(0, o.share[p]), 0) || 1;
    /** @type {number[]} */
    const cuts = [0];
    for (const p of PHASES) cuts.push(cuts[cuts.length - 1] + Math.max(0, o.share[p]) / total);
    cuts[cuts.length - 1] = 1;

    /** @type {Flight} */
    const flight = { points, cum, length, bounds: [[w, s], [e, n]], bearings: [], options: o, cuts };

    // The bearing table: the line's direction at each step, unwrapped so it
    // never jumps by 360, then held to the turn limit over the step's time.
    const followSeconds = (cuts[3] - cuts[2]) * o.seconds;
    const maxStep = (o.turnDegPerS * followSeconds) / BEARING_STEPS;
    let previous = 0;
    for (let i = 0; i <= BEARING_STEPS; i += 1) {
      const d = followDistance(flight, i / BEARING_STEPS);
      const raw = bearingDeg(pointAt(flight, d - o.bearingBehindM), pointAt(flight, d + o.bearingAheadM));
      if (i === 0) {
        previous = raw;
      } else {
        const wanted = turn(previous, raw);
        previous += Math.max(-maxStep, Math.min(maxStep, wanted));
      }
      flight.bearings.push(previous);
    }
    return flight;
  }

  /**
   * The frame `t` of the way through the flight.
   *
   * @param {Flight} flight
   * @param {number} t In [0, 1]; clamped.
   * @returns {Frame}
   */
  function flightFrame(flight, t) {
    const x = Math.min(1, Math.max(0, Number(t) || 0));
    const { cuts, options: o } = flight;
    let k = 0;
    while (k < 3 && x >= cuts[k + 1]) k += 1;
    const u = cuts[k + 1] > cuts[k] ? (x - cuts[k]) / (cuts[k + 1] - cuts[k]) : 1;
    const phase = PHASES[k];
    const start = flight.points[0];
    if (phase === 'establish') {
      return { phase, camera: establishCamera(flight, u), fit: null, drawn: [], head: start };
    }
    if (phase === 'descend') {
      return {
        phase,
        camera: blend(establishCamera(flight, 1), followCamera(flight, 0), ease(u)),
        fit: null,
        drawn: [],
        head: start,
      };
    }
    if (phase === 'follow') {
      const d = followDistance(flight, u);
      return { phase, camera: followCamera(flight, u), fit: null, drawn: lineTo(flight, d), head: pointAt(flight, d) };
    }
    const end = followCamera(flight, 1);
    const w = ease(u);
    const bearing = o.reveal.bearingDeg === null ? end.bearing : end.bearing + turn(end.bearing, o.reveal.bearingDeg);
    return {
      phase,
      camera: {
        center: end.center,
        zoom: end.zoom,
        pitch: lerp(end.pitch, o.reveal.pitch, w),
        bearing: lerp(end.bearing, bearing, w),
      },
      fit: { bounds: flight.bounds, weight: w },
      drawn: flight.points.slice(),
      head: flight.points[flight.points.length - 1],
    };
  }

  self.pwaMap3dFlightCore = Object.freeze({
    DEFAULTS: DEFAULTS,
    PHASES: PHASES,
    buildFlight: buildFlight,
    flightFrame: flightFrame,
    distanceM: distanceM,
    bearingDeg: bearingDeg,
  });
})();
