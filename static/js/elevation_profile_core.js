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
 * ## The slope colouring (SNOW-960)
 *
 * SNOW-910 colours the route LINE on the map by the steepness of the
 * GROUND under it. This chart is a picture of the same track, so it
 * carries the same colours: the CURVE is stroked in the slope classes.
 * One object, one palette, whichever surface you read it on.
 *
 * THE COLOUR IS ON THE STROKE AND NOWHERE ELSE. Tinting the area
 * underneath by class as well was built, drawn against two real tracks and
 * rejected: on a 16 km tour of 638 samples, 77% of which are under 30
 * degrees, the steep classes arrive as 25 m slivers and a per-class tint
 * renders them as hairline vertical stripes — a texture the eye reads as
 * noise rather than as a place along the tour. It also costs the fill the
 * one job it has, which is to make the shape read as ground at a glance
 * rather than as a line graph. So the region stays ONE shape in the
 * route's own colour, and the classes live on the line, where each is a
 * position rather than a stripe.
 *
 * The region does drop from 16% to 6% opacity when the curve is coloured
 * (`SLOPE_AREA_OPACITY`). At the strength that suits a fuchsia curve, a
 * fuchsia region overpowers a class-coloured one.
 *
 * The record arrives as `properties.slope` on the route feature the
 * caller already holds — `{points: […N + 1], angles: […N]}` — and
 * `route_slope_core.js` owns both the palette and the bucketing. Nothing
 * here classifies an angle itself.
 *
 * BANDS ARE PLACED BY SHARE, AND THE SHARE COMES FROM THE INDEX. The
 * sampler walks a fixed stride ALONG the track
 * (apps/routes/services/slope_segments.py), so segment i owns the i-th of
 * N equal shares of it — no distance needs to be measured on this side at
 * all, and none should be. Neither of the two obvious alternatives works:
 * an absolute metre mark is wrong because this module's x-axis is summed
 * from the SIMPLIFIED geometry while the stride was walked on the
 * full-resolution track, and measuring the straight chords between sample
 * coordinates is wrong because a chord cuts the corner at every bend, by
 * more on a switchback than on a straight, so the loss cannot be
 * normalised away. See `slopeBands` for the measured cost of that second
 * one.
 *
 * ## Exports (frozen `self.pwaElevationProfileCore`)
 *
 *   readProfile(coordinates)            → profile data, gaps preserved
 *   buildPaths(profile, geometry)       → SVG path `d` strings
 *   slopeBands(slope)                   → the track's classes, as fractions
 *   buildSlopePaths(profile, slope, …)  → one LINE path per class present
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
   * Stroke width of the curve, in user-space units.
   *
   * Named rather than inline because the unknown treatment's dash is
   * expressed in multiples of it — the same way the map line's
   * `line-dasharray` is in line-widths, so the two dashes read as one
   * pattern at whatever size each is drawn.
   */
  var STROKE_WIDTH = 1.75;

  /**
   * Opacity of the region beneath an UNCOLOURED curve.
   *
   * The chart as it stood before SNOW-960, and the value a route that has
   * never been sampled still draws at.
   */
  var AREA_OPACITY = 0.16;

  /**
   * Opacity of that same region beneath a SLOPE-COLOURED curve.
   *
   * Lower, and measured by eye against two real tracks rather than
   * derived. The region is the route's own fuchsia either way; at the
   * strength that sits correctly under a fuchsia curve it overpowers a
   * class-coloured one, and the colours that matter are the ones on the
   * line. See the module comment.
   */
  var SLOPE_AREA_OPACITY = 0.06;

  /**
   * The dash of a stretch the terrain had no answer for.
   *
   * `[2, 1.5]` line-widths, which is `trip-route-slope-unknown`'s
   * `line-dasharray` in `trip_map.js` verbatim — the same stretch of
   * ground dashes identically on the trip map and in the chart. (The home
   * map's `routes-slope-unknown` carried it too, until SNOW-1017 drew
   * routes there as legs.)
   */
  var UNKNOWN_DASH = [2, 1.5];

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
   * The x/y projections for one profile in one box.
   *
   * @param {object} profile A `readProfile` result.
   * @param {{width: number, height: number}} box The user-space box.
   * @returns {{floor: number, x: function(number): number,
   *   y: function(number): number}} The floor the areas close onto, and
   *   the two scales.
   */
  function makeScales(profile, box) {
    var range = profile.maxEle - profile.minEle;
    var floor = box.height - PAD_Y;
    var usable = box.height - PAD_Y * 2;

    return {
      floor: floor,
      x: function (d) {
        if (!profile.distanceM) return 0;
        return (d / profile.distanceM) * box.width;
      },
      y: function (e) {
        // No range: put the line down the middle rather than at 0/0.
        if (!range) return box.height / 2;
        return floor - ((e - profile.minEle) / range) * usable;
      },
    };
  }

  /**
   * Project one list of profile points into a line and an area `d` string.
   *
   * The shared tail of both path builders: `buildPaths` calls it once per
   * elevation run, `buildSlopePaths` once per piece of a run. Keeping it
   * in one place is what guarantees a slope-coloured curve traces exactly
   * the line an uncoloured one would.
   *
   * @param {Array<{d: number, e: number}>} points At least two of them.
   * @param {{floor: number, x: function, y: function}} scales A
   *   `makeScales` result.
   * @returns {{line: string, area: string}} The stroked line, and the
   *   region between it and the floor.
   */
  function projectPoints(points, scales) {
    var line = '';
    for (var i = 0; i < points.length; i += 1) {
      var x = scales.x(points[i].d).toFixed(2);
      var y = scales.y(points[i].e).toFixed(2);
      line += (i === 0 ? 'M' : 'L') + x + ' ' + y;
      if (i < points.length - 1) line += ' ';
    }

    var startX = scales.x(points[0].d).toFixed(2);
    var endX = scales.x(points[points.length - 1].d).toFixed(2);
    var base = scales.floor.toFixed(2);
    // Down to the floor, back along it, and closed — the area under
    // exactly these points and no further.
    return { line: line, area: line + ' L' + endX + ' ' + base + ' L' + startX + ' ' + base + ' Z' };
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

    var scales = makeScales(profile, box);
    var paths = [];
    for (var i = 0; i < profile.runs.length; i += 1) {
      var run = profile.runs[i];
      if (run.length < 2) continue;
      paths.push(projectPoints(run, scales));
    }
    return paths;
  }

  /**
   * Read a route's wire slope record into bands of the track.
   *
   * Each segment becomes its SHARE of the track, and consecutive segments
   * of the same class merge — a tour that skins for two kilometres of
   * gentle valley arrives as 80 segments and leaves as one band.
   *
   * THE SHARE COMES FROM THE SEGMENT'S INDEX, NOT FROM MEASURING THE
   * SAMPLE POINTS, and that is the whole subtlety of this function.
   * `stride_distances` places every boundary but the last at a whole
   * multiple of the stride ALONG THE TRACK, so segment i occupies exactly
   * the i-th of N equal shares of it. Measuring instead the straight
   * chords between consecutive sample coordinates answers a different
   * question: wherever the track bends between two samples the chord cuts
   * the corner and comes up short, and because a switchback loses more
   * than a straight does, the shortfall accumulates unevenly and dividing
   * by the shortened total cannot take it back out. Measured against both
   * test tracks it displaced bands by up to 1.9 px of the 288-unit chart,
   * concentrated exactly where a skin track zigzags — which is where the
   * steep classes are. Index shares are off by at most 0.14 px, and that
   * residue is the absorbed stub described below.
   *
   * The one irregular segment is the last: `stride_distances` absorbs a
   * trailing remainder into it rather than appending a sliver, so it runs
   * between half and one and a half strides. Treating it as an equal
   * share is the only approximation here, it is bounded by half a stride
   * over the whole track, and the last band is pinned to exactly 1 so no
   * sliver of curve is left belonging to no class at all.
   *
   * @param {?{points?: Array<Array<number>>, angles?: Array<?number>}}
   *   slope The compact record from the route feature's `slope` property.
   *   `points` is not measured, but IS checked: N + 1 coordinates to N
   *   angles is the record's own integrity claim, and a record failing it
   *   is one whose segments would be placed against the wrong ground.
   * @returns {Array<{classIndex: ?number, from: number, to: number}>}
   *   Bands covering [0, 1], in track order. `classIndex` is null for a
   *   segment the terrain had no answer for. Empty when there is nothing
   *   to colour by: no record, a malformed one, or a page that has not
   *   loaded `route_slope_core.js`.
   */
  function slopeBands(slope) {
    // The palette and the bucketing both live there, and nothing here
    // classifies an angle itself. A page that draws a profile without
    // loading it (the trip map, today) simply gets no bands and the
    // uncoloured curve, which is the honest rendering of "this surface
    // does not know about slope classes".
    var core = self.pwaRouteSlopeCore;
    if (!core || !slope) return [];

    var points = slope.points;
    var angles = slope.angles;
    if (!Array.isArray(points) || !Array.isArray(angles)) return [];
    // The same pairing rule `route_slope_core.js` enforces: N + 1 to N, or
    // the bands would be placed against the wrong ground.
    if (points.length !== angles.length + 1) return [];

    var count = angles.length;
    if (!count) return [];

    var bands = [];
    for (var i = 0; i < count; i += 1) {
      var index = core.classify(angles[i]);
      var last = bands.length ? bands[bands.length - 1] : null;
      // `===` covers the null case too, which is what merges a run of
      // consecutive unknowns into one dashed stretch rather than one per
      // 25 m of unsurveyed ground.
      if (last && last.classIndex === index) {
        last.to = (i + 1) / count;
        continue;
      }
      bands.push({
        classIndex: index,
        from: i / count,
        to: (i + 1) / count,
      });
    }

    bands[bands.length - 1].to = 1;
    return bands;
  }

  /**
   * Where along a run's leg the class changes, as along-track distances.
   *
   * @param {Array<{classIndex: ?number, from: number, to: number}>} bands
   *   A `slopeBands` result.
   * @param {number} startD Distance at the start of the leg, metres.
   * @param {number} endD Distance at its end, metres.
   * @param {number} distanceM The track's full length on THIS module's
   *   scale, which is what turns a band's fraction back into a distance.
   * @returns {Array<number>} Ascending distances strictly inside the leg.
   *   Usually empty: a leg of a simplified track is typically shorter
   *   than a 25 m sample and falls wholly inside one band.
   */
  function cutsWithin(bands, startD, endD, distanceM) {
    var cuts = [];
    for (var i = 1; i < bands.length; i += 1) {
      var at = bands[i].from * distanceM;
      if (at > startD && at < endD) cuts.push(at);
    }
    return cuts;
  }

  /**
   * Split a run at every class boundary, into contiguous same-class pieces.
   *
   * A leg of the track that crosses a boundary is CUT at it and the
   * elevation interpolated there, so the colour changes exactly where the
   * ground does rather than at whichever vertex happened to be nearest.
   * On a simplified track a single leg can span several bands, so a leg
   * may produce several cuts.
   *
   * Each resulting sub-segment takes the class of its MIDPOINT, which is
   * the same rule the server sampled by — a segment's angle is the ground
   * at its middle, never at an end it only touches.
   *
   * @param {Array<{d: number, e: number}>} run One elevation run.
   * @param {Array<{classIndex: ?number, from: number, to: number}>} bands
   *   A `slopeBands` result.
   * @param {number} distanceM The track's full length on this scale.
   * @returns {Array<{classIndex: ?number, points: Array<{d: number,
   *   e: number}>}>} Pieces in track order, each with at least two points.
   */
  function splitRun(run, bands, distanceM) {
    var pieces = [];
    var current = null;

    var classAt = function (distance) {
      var fraction = distance / distanceM;
      for (var i = 0; i < bands.length; i += 1) {
        // `<` on the upper edge, so a point exactly on a boundary belongs
        // to the band starting there — the raster's own convention, and
        // the one `classify` follows for the angles themselves.
        if (fraction < bands[i].to) return bands[i].classIndex;
      }
      return bands[bands.length - 1].classIndex;
    };

    var extend = function (from, to) {
      var index = classAt((from.d + to.d) / 2);
      if (current && current.classIndex === index) {
        current.points.push(to);
        return;
      }
      if (current) pieces.push(current);
      current = { classIndex: index, points: [from, to] };
    };

    for (var i = 0; i < run.length - 1; i += 1) {
      var a = run[i];
      var b = run[i + 1];
      var cuts = cutsWithin(bands, a.d, b.d, distanceM);
      var previous = a;
      for (var j = 0; j < cuts.length; j += 1) {
        var span = b.d - a.d;
        // A zero-length leg holds no cut (nothing is strictly inside it),
        // so the division below is safe.
        var t = (cuts[j] - a.d) / span;
        var at = { d: cuts[j], e: a.e + (b.e - a.e) * t };
        extend(previous, at);
        previous = at;
      }
      extend(previous, b);
    }

    if (current) pieces.push(current);
    return pieces;
  }

  /**
   * Project a profile into one LINE path per slope class present.
   *
   * NO AREAS. The region beneath the curve is one shape in the route's
   * own colour, built by `buildPaths` and drawn whether the track has
   * been sampled or not — see the module comment on why a per-class tint
   * was built and then taken back out.
   *
   * GROUPED BY CLASS, NOT ONE ELEMENT PER SEGMENT. A 16 km tour is about
   * 640 sampled segments, and a popup that appended 640 `<path>` nodes to
   * draw one curve would be paying for the colouring in layout rather
   * than in ink. Every piece of the same class joins one path with as
   * many subpaths as it has pieces, so the chart is at most seven
   * elements however long the tour.
   *
   * Falls back to nothing — an empty list, and the caller draws the plain
   * curve — whenever the colouring cannot be placed honestly: an
   * unsampled route, a malformed record, or a track with no length to
   * spread the bands along.
   *
   * @param {object} profile A `readProfile` result.
   * @param {?object} slope The route feature's `slope` property.
   * @param {{width: number, height: number}} [geometry] User-space box.
   *   Defaults to `VIEWBOX`.
   * @returns {Array<{classIndex: ?number, token: string, dashed: boolean,
   *   line: string}>} One entry per class present, gentlest first with the
   *   unknown treatment last. `token` is the `@theme` custom property the
   *   class is painted with — the chart reads the palette from the same
   *   place the legend swatches do, so the two cannot drift apart.
   */
  function buildSlopePaths(profile, slope, geometry) {
    var core = self.pwaRouteSlopeCore;
    var box = geometry || VIEWBOX;
    if (!core || !profile || !profile.hasElevation || !profile.distanceM) return [];

    var bands = slopeBands(slope);
    if (!bands.length) return [];

    var scales = makeScales(profile, box);

    // Keyed by class index, with the unknown under its own key: a null
    // must never share a bucket with class 0, which is the gentle one.
    var collected = {};
    for (var r = 0; r < profile.runs.length; r += 1) {
      var run = profile.runs[r];
      if (run.length < 2) continue;

      var pieces = splitRun(run, bands, profile.distanceM);
      for (var p = 0; p < pieces.length; p += 1) {
        var piece = pieces[p];
        var key = piece.classIndex === null ? 'unknown' : String(piece.classIndex);
        if (!collected[key]) collected[key] = [];
        collected[key].push(projectPoints(piece.points, scales).line);
      }
    }

    var paths = [];
    for (var c = 0; c < core.CLASSES.length; c += 1) {
      var held = collected[String(c)];
      if (!held) continue;
      paths.push({
        classIndex: c,
        token: core.CLASSES[c].token,
        dashed: false,
        line: held.join(' '),
      });
    }
    // Last, and always last: the unknown treatment is not a step on the
    // scale and is not sorted into it.
    if (collected.unknown) {
      paths.push({
        classIndex: null,
        token: core.UNKNOWN_TOKEN,
        dashed: true,
        line: collected.unknown.join(' '),
      });
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
   * WITH A SLOPE RECORD the CURVE is painted by class instead, in the
   * same palette the map line and the legend swatches use. The region
   * beneath it stays the route's own colour either way — only its
   * opacity changes, to keep a fuchsia ground from shouting over a
   * class-coloured line. A route that has never been sampled carries no
   * record and is drawn exactly as it was before SNOW-960: an unsampled
   * route is not an unknown one, and must not be coloured as though
   * someone looked.
   *
   * The element is inert to assistive tech beyond its label: a decorative
   * curve with no readable structure is worse as a traversable tree than
   * as one labelled image, and the figures it illustrates are already in
   * the popup as text right beside it. That holds for the colouring too —
   * it restates the shape already drawn, and the steepness it encodes is
   * the map's to explain, not this chart's.
   *
   * @param {object} profile A `readProfile` result.
   * @param {{label?: string, slope?: object, doc?: Document}} [options]
   *   `label` becomes the accessible name — the caller supplies it
   *   already translated (`pwaStrings`), because this module has no
   *   catalogue of its own. `slope` is the route feature's `slope`
   *   property, absent for a route that has never been sampled. `doc`
   *   overrides the document, for tests.
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

    var slopePaths = buildSlopePaths(profile, opts.slope, VIEWBOX);
    var coloured = slopePaths.length > 0;

    // THE REGION FIRST, AND IT IS THE SAME REGION EITHER WAY: one shape
    // per elevation run, in the route's own colour, from the projection
    // `buildPaths` already returned. Only its strength answers to the
    // curve above it.
    for (var i = 0; i < paths.length; i += 1) {
      var area = doc.createElementNS(NS, 'path');
      area.setAttribute('d', paths[i].area);
      area.setAttribute('fill', 'currentColor');
      area.setAttribute(
        'fill-opacity', String(coloured ? SLOPE_AREA_OPACITY : AREA_OPACITY),
      );
      area.setAttribute('stroke', 'none');
      svg.appendChild(area);
    }

    // Then the curve — in the slope classes where the ground has been
    // sampled, and in the route's colour where it has not.
    var lines = coloured
      ? slopePaths
      : paths.map(function (path) {
        return { token: null, dashed: false, line: path.line };
      });

    for (var j = 0; j < lines.length; j += 1) {
      // `var(--token)` in a presentation attribute, which is a CSS
      // property and resolves like one. The ds-lint hex rule's carve-out
      // for MapLibre paint does not apply here: an SVG in the page CAN
      // read a custom property, so it reads the same one the legend
      // swatch is painted with rather than holding a copy of the value.
      var colour = lines[j].token ? 'var(' + lines[j].token + ')' : 'currentColor';

      var line = doc.createElementNS(NS, 'path');
      line.setAttribute('d', lines[j].line);
      line.setAttribute('fill', 'none');
      line.setAttribute('stroke', colour);
      line.setAttribute('stroke-width', String(STROKE_WIDTH));
      line.setAttribute('stroke-linejoin', 'round');
      line.setAttribute('stroke-linecap', 'round');
      if (lines[j].dashed) {
        line.setAttribute(
          'stroke-dasharray',
          UNKNOWN_DASH.map(function (part) {
            return String(part * STROKE_WIDTH);
          }).join(' '),
        );
      }
      svg.appendChild(line);
    }

    return svg;
  }

  self.pwaElevationProfileCore = Object.freeze({
    readProfile: readProfile,
    buildPaths: buildPaths,
    slopeBands: slopeBands,
    buildSlopePaths: buildSlopePaths,
    createProfileSvg: createProfileSvg,
    VIEWBOX: VIEWBOX,
  });
})();
