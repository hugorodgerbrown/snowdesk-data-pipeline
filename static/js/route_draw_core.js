/*
 * static/js/route_draw_core.js — the pure half of drawing a route on the
 * map (SNOW-1256).
 *
 * Everything route_draw.js decides that needs no DOM and no map: the list
 * of waypoints and its two edits, the straight-line length the panel shows
 * before the server has answered, the guide the map draws under the cursor,
 * the panel's figures line, and the draft kept on the device.
 *
 * A WAYPOINT is `[lon, lat]`, GeoJSON axis order, rounded to six decimal
 * places (about 0.1 m) — the shape routes:draw_preview and
 * routes:create_drawn take, and the precision apps/routes/services/drawn.py
 * keeps. The server re-validates every list it is sent; the checks here
 * exist so the device never stores or posts a list it already knows the
 * server will refuse.
 *
 * Exports (frozen `self.pwaRouteDrawCore`):
 *
 *   DRAFT_KEY                       — the localStorage key of the draft
 *   MAX_WAYPOINTS                   — the most points a draft may hold
 *   addPoint(points, lon, lat)      — a new list with one more point
 *   undo(points)                    — a new list without the last point
 *   lengthM(points)                 — the straight-line length, metres
 *   formatKm(metres)                — "5.4" (one decimal place)
 *   durationParts(seconds)          — {hours, minutes} of an estimate
 *   statsParts(props, points)       — the figures the panel shows, as data
 *   guideCollection(points, cursor) — the map's waypoints and guide line
 *   serialiseDraft(points, name)    — the draft as a string to store
 *   parseDraft(raw)                 — a stored draft back, or null
 */

// @ts-check

(function () {
  'use strict';

  /** Where the draft waits between visits, and across a sign-in. */
  const DRAFT_KEY = 'snowdesk.route-draft';

  /** apps/routes/services/gpx.py's MAX_POINTS — the server's own bound. */
  const MAX_WAYPOINTS = 20000;

  /** Two points closer than this are one (drawn.py's _COINCIDENT_M). */
  const COINCIDENT_M = 0.5;

  const EARTH_RADIUS_M = 6371008.8;

  /**
   * @typedef {[number, number]} Waypoint
   * @typedef {{type: 'FeatureCollection', features: Array<Object>}} FeatureCollection
   */

  /**
   * Round one ordinate to six decimal places.
   *
   * @param {number} value
   * @returns {number}
   */
  function round6(value) {
    return Math.round(value * 1e6) / 1e6;
  }

  /**
   * Whether a value is a usable `[lon, lat]` pair.
   *
   * @param {any} point
   * @returns {boolean}
   */
  function isWaypoint(point) {
    return (
      Array.isArray(point) &&
      point.length >= 2 &&
      typeof point[0] === 'number' &&
      typeof point[1] === 'number' &&
      Number.isFinite(point[0]) &&
      Number.isFinite(point[1]) &&
      point[0] >= -180 && point[0] <= 180 &&
      point[1] >= -90 && point[1] <= 90
    );
  }

  /**
   * The great-circle distance between two waypoints, in metres.
   *
   * @param {Waypoint} a
   * @param {Waypoint} b
   * @returns {number}
   */
  function haversineM(a, b) {
    const toRad = Math.PI / 180;
    const dLat = (b[1] - a[1]) * toRad;
    const dLon = (b[0] - a[0]) * toRad;
    const h =
      Math.sin(dLat / 2) ** 2 +
      Math.cos(a[1] * toRad) * Math.cos(b[1] * toRad) * Math.sin(dLon / 2) ** 2;
    return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  /**
   * A new list with one more waypoint at the end — or the same list when
   * the point is not a coordinate, lands on the last one, or the list is
   * full.
   *
   * @param {Array<Waypoint>} points
   * @param {number} lon
   * @param {number} lat
   * @returns {Array<Waypoint>}
   */
  function addPoint(points, lon, lat) {
    /** @type {Waypoint} */
    const point = [round6(lon), round6(lat)];
    if (!isWaypoint(point)) return points;
    if (points.length >= MAX_WAYPOINTS) return points;
    const last = points[points.length - 1];
    if (last && haversineM(last, point) < COINCIDENT_M) return points;
    return points.concat([point]);
  }

  /**
   * A new list without the last waypoint.
   *
   * @param {Array<Waypoint>} points
   * @returns {Array<Waypoint>}
   */
  function undo(points) {
    return points.slice(0, -1);
  }

  /**
   * The straight-line length along the waypoints, in metres.
   *
   * @param {Array<Waypoint>} points
   * @returns {number}
   */
  function lengthM(points) {
    let total = 0;
    for (let i = 1; i < points.length; i += 1) total += haversineM(points[i - 1], points[i]);
    return total;
  }

  /**
   * A distance in kilometres to one decimal place.
   *
   * @param {number} metres
   * @returns {string}
   */
  function formatKm(metres) {
    return (Math.max(metres, 0) / 1000).toFixed(1);
  }

  /**
   * A duration split the way an estimate is quoted: whole hours and the
   * minutes left over, the minutes zero-padded once there are hours
   * ("4 h 10"). The caller words it, from its strings template.
   *
   * @param {number} seconds
   * @returns {{hours: number, minutes: string}}
   */
  function durationParts(seconds) {
    const total = Math.round(Math.max(seconds, 0) / 60);
    const hours = Math.floor(total / 60);
    const rest = total % 60;
    return { hours: hours, minutes: hours > 0 ? String(rest).padStart(2, '0') : String(rest) };
  }

  /**
   * The figures the panel shows, as data for the caller to word.
   *
   * Before the server has answered (no `props`), the length is the
   * straight-line one the device can work out; afterwards it is the
   * server's. Climb, descent and the estimate are only ever the server's,
   * and null when the ground under the line is not modelled.
   *
   * @param {?Object<string, any>} props A preview Feature's properties, or null.
   * @param {Array<Waypoint>} points The waypoints the panel is showing.
   * @returns {{count: number, distanceM: number, ascentM: ?number, descentM: ?number, estimateS: ?number, terrain: boolean}}
   */
  function statsParts(props, points) {
    /** @type {Object<string, any>} */
    const p = props || {};
    const number = (/** @type {any} */ value) =>
      typeof value === 'number' && Number.isFinite(value) ? value : null;
    const distance = number(p.distance_m);
    const ascent = number(p.ascent_m);
    const descent = number(p.descent_m);
    return {
      count: points.length,
      distanceM: distance === null ? lengthM(points) : distance,
      ascentM: ascent,
      descentM: descent,
      estimateS: number(p.estimate_s),
      // A sampled preview carries `slope`; one outside the model does not.
      terrain: Boolean(props && p.slope),
    };
  }

  /**
   * What the map draws under the cursor: each waypoint as a dot, the
   * straight line through them, and — while a cursor position is known —
   * a dashed segment from the last waypoint to it, so the next leg can be
   * seen before it is added.
   *
   * @param {Array<Waypoint>} points
   * @param {?Waypoint} cursor The cross or the mouse, or null.
   * @returns {FeatureCollection}
   */
  function guideCollection(points, cursor) {
    /** @type {Array<Object>} */
    const features = points.map(function (point, index) {
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: point },
        properties: { role: 'waypoint', first: index === 0 },
      };
    });
    if (points.length >= 2) {
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: points },
        properties: { role: 'line' },
      });
    }
    const last = points[points.length - 1];
    if (last && cursor && isWaypoint(cursor)) {
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: [last, cursor] },
        properties: { role: 'pending' },
      });
    }
    return { type: 'FeatureCollection', features: features };
  }

  /**
   * The draft as the string kept in localStorage.
   *
   * @param {Array<Waypoint>} points
   * @param {string} name
   * @returns {string}
   */
  function serialiseDraft(points, name) {
    return JSON.stringify({ v: 1, points: points, name: name || '' });
  }

  /**
   * A stored draft back, or null for anything that is not one — a stale
   * shape, a hand-edited value, a list with a bad point in it. A draft
   * with no points is no draft.
   *
   * @param {?string} raw
   * @returns {?{points: Array<Waypoint>, name: string}}
   */
  function parseDraft(raw) {
    if (typeof raw !== 'string' || !raw) return null;
    let value;
    try {
      value = JSON.parse(raw);
    } catch (_err) {
      return null;
    }
    if (!value || value.v !== 1 || !Array.isArray(value.points)) return null;
    if (value.points.length === 0 || value.points.length > MAX_WAYPOINTS) return null;
    if (!value.points.every(isWaypoint)) return null;
    return {
      points: value.points.map(function (/** @type {Array<number>} */ point) {
        return /** @type {Waypoint} */ ([point[0], point[1]]);
      }),
      name: typeof value.name === 'string' ? value.name : '',
    };
  }

  self.pwaRouteDrawCore = Object.freeze({
    DRAFT_KEY: DRAFT_KEY,
    MAX_WAYPOINTS: MAX_WAYPOINTS,
    addPoint: addPoint,
    undo: undo,
    lengthM: lengthM,
    formatKm: formatKm,
    durationParts: durationParts,
    statsParts: statsParts,
    guideCollection: guideCollection,
    serialiseDraft: serialiseDraft,
    parseDraft: parseDraft,
  });
})();
