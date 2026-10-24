// @ts-check
/*
 * static/js/meeting_place_core.js — where a meeting point is, in words the
 * group can use (SNOW-1214).
 *
 * The Plan page's pin sits still while the map moves under it, and the
 * sentence under the map follows it: "At the route's start", "On the
 * route, 9 km in", "2 km north-east of the route's start". The trip page
 * and the trip list render the same sentence server-side, from
 * apps/trips/services/meeting_place.py. The two implementations share
 * tests/fixtures/meeting_place_vectors.json, so a rule changed on one side
 * only fails a test on both.
 *
 * `describe` returns the parts — a kind, a distance label, a compass
 * octant — and `sentence` words them from the caller's strings, so the
 * vectors stay language-free and every word on screen comes from a
 * {% trans %} in the template.
 *
 * Exports (frozen `self.pwaMeetingPlaceCore`):
 *   AT_PLACE_M                       → 75
 *   distanceLabel(metres)            → "300 m" | "2.5 km" | "12 km"
 *   describe(points, lat, lon)       → {kind, distance, octant} | null
 *   sentence(place, strings)         → the worded sentence, or ""
 */

(function () {
  'use strict';

  // The same sphere as apps/core/geo.py's EARTH_RADIUS_M.
  var EARTH_RADIUS_M = 6371008.8;

  // How close a pin must be to a place to be AT it — see the Python
  // module's AT_PLACE_M, which this must equal.
  var AT_PLACE_M = 75;

  var OCTANTS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];

  /**
   * @param {number} deg
   * @returns {number}
   */
  function radians(deg) {
    return (deg * Math.PI) / 180;
  }

  /**
   * Great-circle distance, as apps.core.geo.haversine_m.
   *
   * @param {number} lat1
   * @param {number} lon1
   * @param {number} lat2
   * @param {number} lon2
   * @returns {number} Metres.
   */
  function haversineM(lat1, lon1, lat2, lon2) {
    var p1 = radians(lat1);
    var p2 = radians(lat2);
    var dp = radians(lat2 - lat1);
    var dl = radians(lon2 - lon1);
    var a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return EARTH_RADIUS_M * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  }

  /**
   * The compass octant from one point to another, as
   * apps.core.geo.initial_bearing_deg + octant_for.
   *
   * @param {number} lat1
   * @param {number} lon1
   * @param {number} lat2
   * @param {number} lon2
   * @returns {string} One of OCTANTS, or "" for coincident points.
   */
  function octantFrom(lat1, lon1, lat2, lon2) {
    if (lat1 === lat2 && lon1 === lon2) return '';
    var p1 = radians(lat1);
    var p2 = radians(lat2);
    var dl = radians(lon2 - lon1);
    var y = Math.sin(dl) * Math.cos(p2);
    var x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
    var bearing = ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
    return OCTANTS[Math.floor((bearing + 22.5) / 45) % 8];
  }

  /**
   * Word a distance the way a person gives directions.
   *
   * @param {number} metres
   * @returns {string}
   */
  function distanceLabel(metres) {
    if (metres < 975) return Math.max(50, Math.round(metres / 50) * 50) + ' m';
    if (metres < 9950) {
      var tenths = Math.round(metres / 100);
      return (tenths % 10 === 0 ? String(tenths / 10) : (tenths / 10).toFixed(1)) + ' km';
    }
    return Math.round(metres / 1000) + ' km';
  }

  /**
   * How far the pin is from the track, and how far along it — the same
   * flat-plane projection, in the same order, as the Python module.
   *
   * @param {Array<Array<number>>} points
   * @param {number} lat
   * @param {number} lon
   * @returns {{off: number, along: number}}
   */
  function nearestOnTrack(points, lat, lon) {
    var scale = radians(1) * EARTH_RADIUS_M;
    var cosLat = Math.cos(radians(lat));
    /** @param {Array<number>} p @returns {Array<number>} */
    function plane(p) {
      return [(p[0] - lon) * cosLat * scale, (p[1] - lat) * scale];
    }
    var bestOff = Infinity;
    var bestAlong = 0;
    var walked = 0;
    var a = plane(points[0]);
    for (var i = 1; i < points.length; i++) {
      var b = plane(points[i]);
      var dx = b[0] - a[0];
      var dy = b[1] - a[1];
      var lengthSq = dx * dx + dy * dy;
      var t = lengthSq === 0 ? 0 : Math.max(0, Math.min(1, -(a[0] * dx + a[1] * dy) / lengthSq));
      var off = Math.hypot(a[0] + t * dx, a[1] + t * dy);
      var length = Math.sqrt(lengthSq);
      if (off < bestOff) {
        bestOff = off;
        bestAlong = walked + t * length;
      }
      walked += length;
      a = b;
    }
    return { off: bestOff, along: bestAlong };
  }

  /**
   * Place a meeting point against its route.
   *
   * @param {?Array<Array<number>>} points The track, [[lon, lat, …], …].
   * @param {number} lat The pin's latitude.
   * @param {number} lon The pin's longitude.
   * @returns {?{kind: string, distance: string, octant: string}}
   */
  function describe(points, lat, lon) {
    if (!points || !points.length) return null;
    var start = points[0];
    var end = points[points.length - 1];
    var fromStart = haversineM(start[1], start[0], lat, lon);
    var fromEnd = haversineM(end[1], end[0], lat, lon);
    if (fromStart <= AT_PLACE_M) return { kind: 'start', distance: '', octant: '' };
    if (fromEnd <= AT_PLACE_M) return { kind: 'end', distance: '', octant: '' };
    if (points.length > 1) {
      var nearest = nearestOnTrack(points, lat, lon);
      if (nearest.off <= AT_PLACE_M) {
        return { kind: 'on_route', distance: distanceLabel(nearest.along), octant: '' };
      }
    }
    var nearStart = fromStart <= fromEnd;
    var origin = nearStart ? start : end;
    return {
      kind: nearStart ? 'from_start' : 'from_end',
      distance: distanceLabel(nearStart ? fromStart : fromEnd),
      octant: octantFrom(origin[1], origin[0], lat, lon),
    };
  }

  /**
   * Word a place from the caller's strings.
   *
   * `strings` carries one sentence per kind, keyed by the kind, with
   * `{distance}` and `{direction}` placeholders, plus one word per octant
   * keyed `octant-N` … `octant-NW`.
   *
   * @param {?{kind: string, distance: string, octant: string}} place
   * @param {Object<string, string>} strings
   * @returns {string}
   */
  function sentence(place, strings) {
    if (!place) return '';
    var template = strings[place.kind] || '';
    return template
      .replace('{distance}', place.distance)
      .replace('{direction}', strings['octant-' + place.octant] || '');
  }

  self.pwaMeetingPlaceCore = Object.freeze({
    AT_PLACE_M: AT_PLACE_M,
    distanceLabel: distanceLabel,
    describe: describe,
    sentence: sentence,
  });
})();
