/*
 * static/js/elevation_profile_core.js — the saved-route elevation profile.
 *
 * A route's popup (map.js's `activateRoute`) reports its length and its
 * vertical figures as text. This module turns the SAME data into the
 * picture those figures summarise: where the climbing is, where the
 * descending is, and how the two are distributed along the track.
 *
 * ## Where the data comes from — and what is deliberately absent
 *
 * Nothing here fetches anything. `Route.points` is stored as
 * `[lon, lat, ele]` and `routes_geojson` hands it to the map verbatim as
 * LineString `coordinates`, so the elevation series is already the third
 * ordinate of the geometry the routes layer is drawn from. RFC 7946
 * allows that third element and MapLibre ignores it. The profile is
 * therefore a pure derivation of a payload the page already holds — and
 * because the routes overlay is write-through cached
 * (`pwaMapOverlayCache.putOverlay('routes', …)`), it draws offline too.
 *
 * `ele` is whatever the uploaded GPX's `<ele>` said, and NOTHING ELSE.
 * There is no DEM lookup, no smoothing and — the important one — no
 * interpolation across a gap. A point whose `<ele>` the source omitted is
 * parsed to `null` (`apps/routes/services/gpx.py`) and stays null here:
 * the line BREAKS across it rather than being bridged with a plausible
 * straight segment. Drawing terrain the file never recorded is the same
 * class of error as reporting 0 m ascent for a track with no elevation at
 * all, which `Route` refuses to do for stated safety reasons.
 *
 * A track with no elevation anywhere has no profile, and `readProfile`
 * says so with `hasElevation: false` rather than returning a flat line at
 * zero. The caller draws nothing.
 *
 * ## Why the figures are NOT recomputed here
 *
 * `distance_m`, `ascent_m` and `descent_m` arrive as feature properties,
 * measured at ingest on the FULL-RESOLUTION track. What this module sees
 * is the simplified geometry (Douglas-Peucker, capped at 2,000 points),
 * so any total it computed itself would be a second, slightly smaller set
 * of numbers for the same route — shown next to the first, in the same
 * popup. So the totals stay the server's, and everything here is used
 * only to SHAPE the curve. `readProfile` returns `distanceM` for scaling
 * the x-axis, never for display.
 *
 * The min/max elevation it reports ARE displayed, and are honest: they
 * are the extremes of the stored series, and simplification cannot
 * invent a high or low point that was not in the source.
 *
 * ## Exports (frozen `self.pwaElevationProfileCore`)
 *
 *   readProfile(coordinates)            → profile data, gaps preserved
 *   buildPaths(profile, geometry)       → SVG path `d` strings
 *   createProfileSvg(profile, options)  → the <svg> element, or null
 *   VIEWBOX                             → { width, height }
 */

(function () {
  'use strict';

  /**
   * Mean Earth radius in metres (IUGG).
   *
   * The same constant `apps/routes/services/gpx.py` measures with, for the
   * same reason: over tens of kilometres of mountain terrain the spherical
   * / ellipsoidal difference sits far below the GPS noise already in the
   * source track.
   */
  var EARTH_RADIUS_M = 6371008.8;

  /**
   * The SVG user-space box every profile is drawn in.
   *
   * Sized for the route popup, which is `min(320px, 100vw - 32px)` wide
   * with padding either side. The element itself is rendered `w-full`, so
   * this is an aspect ratio and a coordinate system rather than a pixel
   * size — a narrower popup scales it down intact.
   */
  var VIEWBOX = { width: 288, height: 72 };

  /**
   * Vertical inset, in user-space units, above and below the curve.
   *
   * Keeps the highest point's stroke from being clipped by the top edge
   * and leaves the lowest point sitting on a visible floor rather than
   * merged into the baseline rule.
   */
  var PAD_Y = 6;

  /**
   * Great-circle distance between two WGS-84 points, in metres.
   *
   * @param {number} lon1 Longitude of the first point, degrees.
   * @param {number} lat1 Latitude of the first point, degrees.
   * @param {number} lon2 Longitude of the second point, degrees.
   * @param {number} lat2 Latitude of the second point, degrees.
   * @returns {number} Distance in metres.
   */
  function haversineM(lon1, lat1, lon2, lat2) {
    var phi1 = (lat1 * Math.PI) / 180;
    var phi2 = (lat2 * Math.PI) / 180;
    var deltaPhi = phi2 - phi1;
    var deltaLambda = ((lon2 - lon1) * Math.PI) / 180;
    var a =
      Math.sin(deltaPhi / 2) * Math.sin(deltaPhi / 2) +
      Math.cos(phi1) * Math.cos(phi2) * Math.sin(deltaLambda / 2) * Math.sin(deltaLambda / 2);
    return 2 * EARTH_RADIUS_M * Math.asin(Math.sqrt(a));
  }

  /**
   * Read a LineString's coordinates into plottable profile data.
   *
   * Walks the track once, accumulating along-track distance for the
   * x-axis and collecting elevation for the y-axis. Consecutive points
   * that both carry an elevation join into a run; a point without one
   * ENDS the current run and starts a new one after it, so the caller
   * draws a broken line rather than bridging terrain the file never
   * recorded.
   *
   * Distance keeps accumulating across a gap — the horizontal axis is
   * along-track distance, which is known for every point regardless of
   * whether its elevation is — so the runs either side of a gap sit at
   * their true positions with a hole between them, not butted together.
   *
   * @param {Array<Array<number>>} coordinates `[lon, lat, ele]` triples;
   *   `ele` may be null, undefined or absent.
   * @returns {{
   *   runs: Array<Array<{d: number, e: number}>>,
   *   distanceM: number,
   *   minEle: number|null,
   *   maxEle: number|null,
   *   hasElevation: boolean
   * }} `runs` holds one entry per unbroken elevation run, each a list of
   *   `{d: metres along track, e: metres above sea level}`. Runs of a
   *   single point are kept: they are a real, if unplottable, fact, and
   *   `buildPaths` decides what to do with them.
   */
  function readProfile(coordinates) {
    var empty = {
      runs: [], distanceM: 0, minEle: null, maxEle: null, hasElevation: false,
    };
    if (!Array.isArray(coordinates) || coordinates.length === 0) return empty;

    var runs = [];
    var current = [];
    var distance = 0;
    var minEle = Infinity;
    var maxEle = -Infinity;
    var previous = null;

    for (var i = 0; i < coordinates.length; i += 1) {
      var point = coordinates[i];
      if (!Array.isArray(point) || point.length < 2) continue;

      var lon = point[0];
      var lat = point[1];
      if (typeof lon !== 'number' || typeof lat !== 'number') continue;

      if (previous) distance += haversineM(previous[0], previous[1], lon, lat);
      previous = [lon, lat];

      // `typeof` rather than a truthiness test: 0 m above sea level is a
      // legitimate elevation, and `null` is the parser's explicit "this
      // point's <ele> was missing or unparseable".
      var ele = point.length > 2 ? point[2] : null;
      if (typeof ele !== 'number' || !isFinite(ele)) {
        // The gap ends the run. Distance carried on accumulating above,
        // so whatever comes next starts at its true along-track position.
        if (current.length) runs.push(current);
        current = [];
        continue;
      }

      if (ele < minEle) minEle = ele;
      if (ele > maxEle) maxEle = ele;
      current.push({ d: distance, e: ele });
    }
    if (current.length) runs.push(current);

    if (!runs.length) return { ...empty, distanceM: distance };

    return {
      runs: runs,
      distanceM: distance,
      minEle: minEle,
      maxEle: maxEle,
      hasElevation: true,
    };
  }

  /**
   * Project profile data into SVG path `d` strings.
   *
   * Two paths per run: the stroked line itself, and a closed area between
   * that line and the floor. The area is what makes the shape read as
   * terrain at a glance rather than as a graph, and it is per-run so a
   * gap leaves a genuine hole in the fill too.
   *
   * Scaling notes:
   *
   *   - **x** is along-track distance over the track's full length. A
   *     zero-length track (every point in the same place) would divide by
   *     zero, so it degenerates to a flat line across the full width.
   *   - **y** is elevation over the track's own min-to-max range, NOT a
   *     fixed or sea-level-anchored scale. A profile is read for its
   *     shape, and anchoring at zero would flatten a 300 m tour into a
   *     hairline. The range is labelled in the popup precisely because
   *     the axis is relative.
   *   - a track with **no vertical range at all** (genuinely flat, or one
   *     single elevation repeated) would also divide by zero, and is
   *     centred instead — a level line, which is what it is.
   *
   * @param {object} profile A `readProfile` result.
   * @param {{width: number, height: number}} [geometry] User-space box.
   *   Defaults to `VIEWBOX`.
   * @returns {Array<{line: string, area: string}>} One entry per run with
   *   at least two points. Single-point runs are dropped: a lone vertex
   *   has no line to draw and would render as an invisible zero-width
   *   area.
   */
  function buildPaths(profile, geometry) {
    var box = geometry || VIEWBOX;
    if (!profile || !profile.hasElevation) return [];

    var range = profile.maxEle - profile.minEle;
    var floor = box.height - PAD_Y;
    var usable = box.height - PAD_Y * 2;

    var scaleX = function (d) {
      if (!profile.distanceM) return 0;
      return (d / profile.distanceM) * box.width;
    };
    var scaleY = function (e) {
      // No range: put the line down the middle rather than at 0/0.
      if (!range) return box.height / 2;
      return floor - ((e - profile.minEle) / range) * usable;
    };

    var paths = [];
    for (var i = 0; i < profile.runs.length; i += 1) {
      var run = profile.runs[i];
      if (run.length < 2) continue;

      var line = '';
      for (var j = 0; j < run.length; j += 1) {
        var x = scaleX(run[j].d).toFixed(2);
        var y = scaleY(run[j].e).toFixed(2);
        line += (j === 0 ? 'M' : 'L') + x + ' ' + y;
        if (j < run.length - 1) line += ' ';
      }

      var startX = scaleX(run[0].d).toFixed(2);
      var endX = scaleX(run[run.length - 1].d).toFixed(2);
      var base = floor.toFixed(2);
      // Down to the floor, back along it, and closed — the area under
      // exactly this run and no further.
      var area = line + ' L' + endX + ' ' + base + ' L' + startX + ' ' + base + ' Z';

      paths.push({ line: line, area: area });
    }
    return paths;
  }

  /**
   * Build the profile `<svg>` element, or null when there is nothing to draw.
   *
   * Colour comes from `currentColor` against a design token
   * (`text-route-line`, the same `--color-route-line` the map draws the
   * track with), so the chart and the line on the map are self-evidently
   * the same object, and both follow the theme. The baseline rule takes a
   * token of its own.
   *
   * The element is inert to assistive tech beyond its label: a decorative
   * curve with no readable structure is worse as a traversable tree than
   * as one labelled image, and the figures it illustrates are already in
   * the popup as text right beside it.
   *
   * @param {object} profile A `readProfile` result.
   * @param {{label?: string, doc?: Document}} [options] `label` becomes
   *   the accessible name — the caller supplies it already translated
   *   (`pwaStrings`), because this module has no catalogue of its own.
   *   `doc` overrides the document, for tests.
   * @returns {SVGElement|null} The chart, or null when the track carries
   *   no drawable elevation.
   */
  function createProfileSvg(profile, options) {
    var opts = options || {};
    var doc = opts.doc || (typeof document !== 'undefined' ? document : null);
    if (!doc) return null;

    var paths = buildPaths(profile);
    if (!paths.length) return null;

    var NS = 'http://www.w3.org/2000/svg';
    var svg = doc.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + VIEWBOX.width + ' ' + VIEWBOX.height);
    svg.setAttribute('role', 'img');
    svg.setAttribute('class', 'mt-1.5 block h-auto w-full text-route-line');
    if (opts.label) svg.setAttribute('aria-label', opts.label);

    // The floor the areas sit on, drawn first so the fill overlaps it.
    var baseline = doc.createElementNS(NS, 'line');
    baseline.setAttribute('x1', '0');
    baseline.setAttribute('x2', String(VIEWBOX.width));
    baseline.setAttribute('y1', String(VIEWBOX.height - PAD_Y));
    baseline.setAttribute('y2', String(VIEWBOX.height - PAD_Y));
    baseline.setAttribute('stroke', 'currentColor');
    baseline.setAttribute('stroke-width', '1');
    baseline.setAttribute('class', 'text-border');
    svg.appendChild(baseline);

    for (var i = 0; i < paths.length; i += 1) {
      var area = doc.createElementNS(NS, 'path');
      area.setAttribute('d', paths[i].area);
      area.setAttribute('fill', 'currentColor');
      area.setAttribute('fill-opacity', '0.16');
      area.setAttribute('stroke', 'none');
      svg.appendChild(area);

      var line = doc.createElementNS(NS, 'path');
      line.setAttribute('d', paths[i].line);
      line.setAttribute('fill', 'none');
      line.setAttribute('stroke', 'currentColor');
      line.setAttribute('stroke-width', '1.75');
      line.setAttribute('stroke-linejoin', 'round');
      line.setAttribute('stroke-linecap', 'round');
      svg.appendChild(line);
    }

    return svg;
  }

  self.pwaElevationProfileCore = Object.freeze({
    readProfile: readProfile,
    buildPaths: buildPaths,
    createProfileSvg: createProfileSvg,
    VIEWBOX: VIEWBOX,
  });
})();
