/*
 * static/js/route_slope_core.js — colouring a saved route by the steepness
 * of the ground it crosses (SNOW-910).
 *
 * The server samples the TERRAIN along a track — never the track's own
 * elevation, which on a zigzagging skin track is a completely different and
 * far gentler number (see apps/routes/services/slope_segments.py). What
 * arrives on a route feature is the compact form of that record:
 *
 *     properties.slope = { points: [[lon, lat], …],   // N + 1
 *                          angles: [34.2, null, …] }  // N
 *
 * with `null` for a segment the terrain had no answer for. This module
 * turns that into LineStrings, and decides which colour bucket an angle
 * falls in.
 *
 * SEGMENTS FOLLOW THE TRACK (SNOW-1053). A segment used to be the straight
 * chord `points[i]` → `points[i + 1]`, and the leg casing under it is drawn
 * from the route's real coordinates, so at high zoom the two parted — by
 * up to 9.9 m on a bend. The record now carries `seams`, one index per
 * boundary into the feature's own `geometry.coordinates` (the last
 * coordinate at or before that boundary), and `segmentPaths` draws a
 * segment as its two boundary points with the coordinates between them.
 * Without valid `seams` — a payload cached before the key, or a record
 * the server could not place on the geometry — it falls back to the chord.
 *
 * WHO READS IT NOW. SNOW-1017 took the slope-coloured line off the home
 * map below z14 — a route there draws as its legs (route_legs_core.js,
 * and docs/decisions/legs-not-slope-classes-on-the-map.md) — and from z14
 * map.js paints the route's core in these classes again, through
 * route_legs_core.js's slopeSegmentCollection, which builds on
 * `segmentFeatures`, and `colourExpression`. The trip page (trip_map.js)
 * paints the slope-coloured line with `segmentCollection` and
 * `colourExpression`; the rail and the elevation profile read `CLASSES`
 * and `classify`. The route detail sheet read the record in words until
 * SNOW-1095 removed it, and the functions that wrote those words with it.
 *
 * THREE STATES, AND THEY MUST NOT COLLAPSE INTO TWO:
 *
 *   - NO `slope` PROPERTY AT ALL — the route has never been sampled. It
 *     produces NOTHING here, and the trip page draws it as the flat line
 *     it has always been. An unsampled route is not an unknown one, and drawing
 *     a dashed "we looked and could not tell" over a track nothing has
 *     looked at would be a claim we have not earned.
 *   - A NULL ANGLE — sampled, no answer. That becomes an `unknown: true`
 *     feature, which the trip page paints dashed and grey, and it NEVER carries
 *     a `slope_class`: a step expression given a class for an unknown is
 *     one refactor away from painting it green.
 *   - A NUMBER — one of six buckets, below.
 *
 * THE PALETTE IS THE SLOPE RASTER'S, verbatim. The five painted classes
 * are the same hexes `slope_overlay_core.js` carries and the same
 * `--color-slope-*` tokens the legend swatches use, so a route crossing a
 * shaded face is the colour of the shading under it. `gentle` is the sixth,
 * and has no raster counterpart because the raster paints nothing under
 * 30° — but a LINE cannot be transparent where the raster is blank without
 * the track appearing to break, so the gentle band gets an explicit colour
 * of its own.
 *
 * Hex literals rather than CSS variables throughout: MapLibre paint
 * properties take literal colours and cannot reference a custom property.
 * Each names the token it mirrors, the same convention `ROUTE_LINE_COLOUR`
 * and the EAWS danger scale already follow in `map.js`.
 *
 * Every function here is pure.
 *
 * Exports (frozen `self.pwaRouteSlopeCore`):
 *
 *   CLASSES                — the six buckets, gentlest first
 *   UNKNOWN_COLOUR         — the dashed line's colour
 *   UNKNOWN_TOKEN          — and the token that colour mirrors
 *   PASSAGE_CORE_COLOUR    — the no-fall split line's core (SNOW-964)
 *   PASSAGE_CORE_TOKEN     — and the token that colour mirrors
 *   FALL_LINE_COLOUR       — the fall-line arrow's ink
 *   FALL_LINE_TOKEN        — and the token that colour mirrors
 *   classify(angle)        — a bucket index, or null for an unknown
 *   colourExpression()     — the MapLibre `step` painting a segment by class
 *   segmentPaths(s, c)     — each segment's [lon, lat] path along c
 *   segmentFeatures(f)     — one OWNED route feature -> its segments
 *   segmentCollection(fc)  — a routes FeatureCollection -> all of them
 *   fallLineCollection(fc) — its fall-line arrows as Points
 *
 * SNOW-964 adds `passages` to the record — the stretches where the TRACK
 * is on no-fall ground — and a `passage: true` flag on the segments they
 * name. Only ever `true`, never `false`, and only ever on a segment that
 * already has an angle: a feature can never carry both `unknown` and
 * `passage`, because unsurveyed ground is never inside a passage
 * (apps/routes/services/passages.py).
 *
 * The fall-line marks are the newest key: `fall_lines`, a list of
 * `{i, deg}` — an index into the same `angles` array, and the compass
 * bearing the ground there FACES, which is the direction downhill. They
 * arrive as a bearing per PLACE rather than as an aspect per segment, so
 * this module PLACES each one and classifies nothing: the server decided
 * where an arrow goes and how many there are
 * (apps/routes/services/fall_line.py), and a client that re-thinned them
 * would be a second opinion about which way a face runs.
 */

// @ts-check

(function () {
  'use strict';

  /**
   * The six buckets an angle falls in, gentlest first.
   *
   * `from` is inclusive and `to` is exclusive, so a sample of exactly 35°
   * is in `slope-35` and not in `slope-30`. That is the raster's own
   * convention — its classes are named for their lower bound — and getting
   * it the other way round would put every boundary sample one band too
   * gentle, which is the direction that matters.
   *
   * `token` names the `@theme` custom property the legend swatch is
   * painted with (`src/css/main.css`) and `hex` is that token's value, so
   * a test can assert the two agree and the line cannot drift from the key
   * that explains it.
   *
   * The five steep classes are `slope_overlay_core.js`'s CLASSES, value
   * for value. `gentle` is ours: see the module comment on why a line
   * needs a colour where the raster needs none.
   */
  const CLASSES = Object.freeze([
    Object.freeze({ id: 'slope-gentle', from: 0, to: 30, token: '--color-slope-gentle', hex: '#38bdf8' }),
    Object.freeze({ id: 'slope-30', from: 30, to: 35, token: '--color-slope-30', hex: '#f2e50a' }),
    Object.freeze({ id: 'slope-35', from: 35, to: 40, token: '--color-slope-35', hex: '#f46f24' }),
    Object.freeze({ id: 'slope-40', from: 40, to: 45, token: '--color-slope-40', hex: '#de055b' }),
    Object.freeze({ id: 'slope-45', from: 45, to: 50, token: '--color-slope-45', hex: '#c889bb' }),
    Object.freeze({ id: 'slope-50', from: 50, to: null, token: '--color-slope-50', hex: '#4b4b4b' }),
  ]);

  /**
   * The colour of a segment the terrain had no answer for.
   *
   * A neutral grey, and deliberately NOT one of the six above — least of
   * all the gentle one. "We do not know" must not be readable as "not
   * steep", which is the whole reason the server keeps a reason rather
   * than a null (see apps/locations/services/terrain.py). The dash carries
   * the same meaning for a reader who cannot separate this grey from the
   * over-50° one.
   *
   * Mirrors `--color-slope-unknown` in `src/css/main.css`.
   */
  const UNKNOWN_COLOUR = '#94a3b8';

  /**
   * The `@theme` custom property `UNKNOWN_COLOUR` is the value of.
   *
   * The six painted classes each carry a `token` beside their `hex`; the
   * unknown treatment is not one of them and so had nowhere to record its
   * own. SNOW-960 gave it one, because the popup's elevation profile
   * paints this same six-plus-one scale as an inline SVG — where a CSS
   * variable IS readable, unlike in a MapLibre paint property — and
   * reading the token is what keeps the chart from carrying a second copy
   * of the palette that could drift from this one.
   */
  const UNKNOWN_TOKEN = '--color-slope-unknown';

  /**
   * The light core down the middle of a no-fall passage (SNOW-964).
   *
   * A passage is drawn as a SPLIT LINE: the band colour widened, with
   * this run down its centre. The mark has to be legible over all six
   * band colours and read as a GAP in the line rather than as a seventh
   * class of ground, so it is a near-white off the steepness scale
   * entirely.
   *
   * `transparent` is not an option: MapLibre cannot punch a hole through
   * one line layer to another, so a transparent core would reveal the
   * dark casing under the route and read as a shadow. A shade off
   * `#ffffff` rather than pure white, kept from when pure white was
   * another marker's halo (the crux ring, removed by SNOW-1066).
   *
   * Mirrors `--color-passage-core` in `src/css/main.css`; the literal is
   * here because a MapLibre paint property cannot read a custom
   * property, the same convention `UNKNOWN_COLOUR` follows.
   */
  const PASSAGE_CORE_COLOUR = '#f8fafc';

  /** The `@theme` custom property `PASSAGE_CORE_COLOUR` is the value of. */
  const PASSAGE_CORE_TOKEN = '--color-passage-core';

  /**
   * The fall-line arrow's ink.
   *
   * Near-black, and deliberately not a hue of its own. An arrow says
   * which way to look, not how steep the ground is, and a marker tinted
   * from the ramp reads as a further class of ground — a colour of its
   * own would make an arrow look like a seventh band.
   *
   * It has a token of its own because a token named for one mark but
   * used by two is exactly the drift SNOW-969 spent two tickets closing.
   * `--color-route-line-casing` already carries this same value under
   * another name, on the same reasoning.
   *
   * Registered `sdf: true` in `map.js`, so this value reaches the map as
   * `icon-color` rather than as pixel data — the arrow itself is an
   * alpha mask (`route_markers_core.js`).
   *
   * Mirrors `--color-fall-line-arrow` in `src/css/main.css`; the literal
   * is here because a MapLibre paint property cannot read a custom
   * property, the convention `UNKNOWN_COLOUR` follows.
   */
  const FALL_LINE_COLOUR = '#1a1916';

  /** The `@theme` custom property `FALL_LINE_COLOUR` is the value of. */
  const FALL_LINE_TOKEN = '--color-fall-line-arrow';

  /**
   * Which bucket an angle falls in.
   *
   * @param {?number} angle Degrees from horizontal, or null/undefined for
   *   a segment the terrain had no answer for.
   * @returns {?number} The index into `CLASSES`, or null when there is no
   *   angle to classify. A non-finite number answers null too — the
   *   callers have nowhere to put an exception, and an unknown treatment
   *   is the honest reading of a number that is not one.
   */
  function classify(angle) {
    if (typeof angle !== 'number' || !Number.isFinite(angle)) return null;
    // Walked from the top so the open-ended last class needs no special
    // case, and so a negative angle (which the sampler cannot produce, but
    // a hand-written record could) still lands in the gentle bucket rather
    // than falling off the end.
    for (let i = CLASSES.length - 1; i > 0; i -= 1) {
      if (angle >= CLASSES[i].from) return i;
    }
    return 0;
  }

  /**
   * One no-fall passage, as the server sends it (SNOW-964).
   *
   * Every field is optional because this arrives from a feature property
   * and is checked rather than trusted. `fall_line` is ABSENT — never
   * null — on a passage nothing could classify, which is a real state:
   * steep ground earns the mark, and a missing aspect is a fact about
   * the survey.
   *
   * @typedef {object} Passage
   * @property {number} [from] First segment index, inclusive.
   * @property {number} [to] Last segment index, inclusive.
   * @property {number} [m] Its along-track length, in metres.
   * @property {string} [fall_line] `descending`, `climbing` or `crossing`.
   */

  /**
   * The segment indices the passages name.
   *
   * The server sends INDEX PAIRS into the same `angles` array this module
   * is already pairing into segments, so there is only one geometry and
   * nothing to disagree with it. A malformed entry is skipped rather than
   * thrown on: the record arrives from a feature property, and a route
   * that fails to draw is worse than one drawn without its marks.
   *
   * @param {*} passages The record's `passages`, unchecked.
   * @returns {Set<number>} Every index inside a passage, possibly empty.
   */
  function passageIndices(passages) {
    const marked = new Set();
    if (!Array.isArray(passages)) return marked;
    for (let i = 0; i < passages.length; i += 1) {
      const passage = passages[i] || {};
      const from = passage.from;
      const to = passage.to;
      if (typeof from !== 'number' || typeof to !== 'number') continue;
      if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) continue;
      for (let index = from; index <= to; index += 1) marked.add(index);
    }
    return marked;
  }

  /**
   * One route's slope record, as the server sends it.
   *
   * Every field is optional because it arrives from a feature property
   * and is checked rather than trusted.
   *
   * @typedef {object} Slope
   * @property {Array<Array<number>>} [points] N + 1 boundary coordinates.
   * @property {Array<?number>} [angles] N segment angles, null unknown.
   * @property {Array<number>} [seams] N + 1 indices into the feature's
   *   geometry: the last coordinate at or before each boundary (SNOW-1053).
   * @property {Array<Passage>} [passages] The no-fall passages.
   */

  /**
   * Whether `seams` can place every boundary on `coordinates`.
   *
   * One per boundary, each a whole number inside the coordinates array,
   * and never decreasing — a seam that went backwards would draw a
   * segment over ground behind it.
   *
   * @param {*} seams The record's `seams`, unchecked.
   * @param {number} boundaryCount How many boundary points the record has.
   * @param {number} coordinateCount How many coordinates the geometry has.
   * @returns {boolean} True when every seam is usable.
   */
  function validSeams(seams, boundaryCount, coordinateCount) {
    if (!Array.isArray(seams) || seams.length !== boundaryCount) return false;
    for (let i = 0; i < seams.length; i += 1) {
      const seam = seams[i];
      if (!Number.isInteger(seam) || seam < 0 || seam >= coordinateCount) return false;
      if (i > 0 && seam < seams[i - 1]) return false;
    }
    return true;
  }

  /**
   * A coordinate as a bare `[lon, lat]`, its elevation dropped.
   *
   * @param {Array<number>} coordinate `[lon, lat]` or `[lon, lat, ele]`.
   * @returns {Array<number>} `[lon, lat]`.
   */
  function lonLat(coordinate) {
    return [coordinate[0], coordinate[1]];
  }

  /**
   * Whether two coordinates name the same place, elevation aside.
   *
   * @param {Array<number>} a A coordinate.
   * @param {Array<number>} b Another.
   * @returns {boolean} True when longitude and latitude are equal.
   */
  function samePlace(a, b) {
    return a[0] === b[0] && a[1] === b[1];
  }

  /**
   * The path each segment of a slope record is drawn along.
   *
   * With valid `seams`, segment i is `points[i]`, then every coordinate
   * after `seams[i]` up to and including `seams[i + 1]`, then
   * `points[i + 1]` — so it lies on the same line the leg casing is drawn
   * from. A coordinate equal to either boundary point is left out rather
   * than repeated: on the merged terrain track most boundaries ARE
   * coordinates, and a doubled vertex is a zero-length step to every
   * consumer. Elevations are dropped, so a path stays two-dimensional like
   * the chord it replaces.
   *
   * Without valid `seams` every segment is the chord
   * `[points[i], points[i + 1]]`, which is what was drawn before SNOW-1053.
   *
   * @param {?Slope} slope The record, unchecked beyond its shape.
   * @param {*} coordinates The feature's `geometry.coordinates`.
   * @returns {Array<Array<Array<number>>>} One path per segment; empty
   *   when the record's halves do not pair up.
   */
  function segmentPaths(slope, coordinates) {
    const points = slope && slope.points;
    const angles = slope && slope.angles;
    if (!Array.isArray(points) || !Array.isArray(angles)) return [];
    if (points.length !== angles.length + 1) return [];

    const seams = slope && slope.seams;
    const follow = Array.isArray(coordinates)
      && validSeams(seams, points.length, coordinates.length);

    const paths = [];
    for (let i = 0; i < angles.length; i += 1) {
      const start = points[i];
      const end = points[i + 1];
      if (!follow || !seams) {
        paths.push([start, end]);
        continue;
      }
      const path = [start];
      for (let c = seams[i] + 1; c <= seams[i + 1]; c += 1) {
        const coordinate = coordinates[c];
        if (!Array.isArray(coordinate)) continue;
        if (samePlace(coordinate, start) || samePlace(coordinate, end)) continue;
        path.push(lonLat(coordinate));
      }
      path.push(end);
      paths.push(path);
    }
    return paths;
  }

  /**
   * The LineStrings one route's slope record draws as.
   *
   * The record's N + 1 coordinates are shared endpoints: segment i runs
   * from `points[i]` to `points[i + 1]`. Pairing them back out here is
   * what lets the payload carry half the coordinates it otherwise would.
   * Between the two, it follows the feature's own geometry where the
   * record carries `seams` — see `segmentPaths`.
   *
   * Each feature carries the owning route's `uuid`, so a consumer can get
   * from the segment back to its route — `route_legs_core.js` builds the
   * home map's passage marks from these.
   *
   * A PENDING ROUTE PRODUCES NOTHING. A followed share is drawn as a teal
   * dashed line saying "this one is not yours yet", which is the fact that
   * matters about it and the only action it offers; recolouring it by
   * steepness would spend the one line on a second message and leave the
   * first with nothing to carry it. Saving it makes it an owned route, and
   * owned routes are coloured. That also means no segment here ever
   * carries a `token`, so the layers cannot hand a non-owner's identifier
   * to a popup built for owners.
   *
   * A route with no `slope` property produces NOTHING either — see the
   * module comment's three states. So does a record whose halves do not
   * pair up, which would otherwise draw segments against the wrong ground.
   *
   * @param {?{properties?: any, geometry?: any}} feature One route feature
   *   from the routes payload, as served by `routes:geojson`.
   * @returns {Array<object>} Its segment features, possibly empty.
   */
  function segmentFeatures(feature) {
    const properties = (feature && feature.properties) || {};
    if (properties.pending) return [];

    const slope = properties.slope;
    if (!slope) return [];

    const points = slope.points;
    const angles = slope.angles;
    if (!Array.isArray(points) || !Array.isArray(angles)) return [];
    if (points.length !== angles.length + 1) return [];

    const identity = properties.uuid ? { uuid: properties.uuid } : {};
    const marked = passageIndices(slope.passages);
    const geometry = feature && feature.geometry;
    const paths = segmentPaths(slope, geometry && geometry.coordinates);

    const features = [];
    for (let i = 0; i < angles.length; i += 1) {
      const slopeClass = classify(angles[i]);
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: paths[i] },
        // An unknown segment carries `unknown` and NO `slope_class`, and a
        // known one the reverse. Never both, never neither — the two
        // layers filter on exactly this and a segment answering to both
        // would be painted twice.
        //
        // SNOW-964's `passage` joins the KNOWN branch only, and is set
        // to `true` or left off entirely — never `false`, the rule
        // the server's passage pass follows. The server never names an
        // unknown segment in a passage, and this is the second place
        // that holds: a feature cannot carry both.
        properties: slopeClass === null
          ? Object.assign({ unknown: true }, identity)
          : Object.assign(
            { slope_class: slopeClass },
            marked.has(i) ? { passage: true } : {},
            identity,
          ),
      });
    }
    return features;
  }

  /**
   * The MapLibre `step` expression painting a segment by its slope class.
   *
   * Built from CLASSES rather than written out, so the trip page and the
   * map page cannot drift: both read this one expression. A `step` takes
   * the first colour, then a (stop, colour) pair per class after it — the
   * stops are the CLASS INDICES `classify` assigns, not angles.
   *
   * @returns {Array<*>} The expression.
   */
  function colourExpression() {
    /** @type {Array<*>} */
    const expression = ['step', ['get', 'slope_class'], CLASSES[0].hex];
    for (let i = 1; i < CLASSES.length; i += 1) expression.push(i, CLASSES[i].hex);
    return expression;
  }

  /**
   * Every route's segments, as one FeatureCollection.
   *
   * The source the trip page hands to MapLibre. Always a valid collection, even
   * when nothing in the payload has been sampled — an empty one paints
   * nothing, where a null would make `setData` throw.
   *
   * @param {?{features?: Array<any>}} geojson The routes FeatureCollection.
   * @returns {{type: string, features: Array<object>}} The segments.
   */
  function segmentCollection(geojson) {
    const features = (geojson && geojson.features) || [];
    const segments = [];
    for (let i = 0; i < features.length; i += 1) {
      const own = segmentFeatures(features[i]);
      for (let j = 0; j < own.length; j += 1) segments.push(own[j]);
    }
    return { type: 'FeatureCollection', features: segments };
  }

  /**
   * One fall-line mark, as the server sends it.
   *
   * Both fields are optional because this arrives from a feature
   * property and is checked rather than trusted.
   *
   * @typedef {object} FallLineMark
   * @property {number} [i] The segment index the mark sits on.
   * @property {number} [deg] The bearing the ground there faces, in
   *   whole compass degrees — which is downhill.
   */

  /**
   * Every route's fall-line arrows, as one Point FeatureCollection.
   *
   * The mark names a SEGMENT and the arrow is placed at that segment's
   * MIDPOINT, which is where the aspect was sampled — the "sample at the
   * midpoints" rule in
   * docs/decisions/a-slope-segment-is-the-shared-record.md. Placing it
   * on a boundary instead would put the arrow a dozen metres from the
   * ground it describes.
   *
   * A straight average of the two boundary coordinates, not a
   * great-circle midpoint: over one 25 m stride the two differ by well
   * under a millimetre, and the spherical form would be arithmetic
   * nobody could check by eye.
   *
   * A PENDING ROUTE PRODUCES NOTHING — `segmentFeatures`' rule, for its
   * reason: a followed share's one line says "this one is not yours
   * yet", and hanging marks off it would spend that line on a second
   * message. It also keeps a non-owner's feature carrying nothing but
   * its token.
   *
   * Always a valid collection, even when nothing is marked — `setData`
   * throws on a null.
   *
   * @param {?{features?: Array<any>}} geojson The routes FeatureCollection.
   * @returns {{type: string, features: Array<object>}} The arrows, each
   *   carrying `deg` for `icon-rotate` and the owning route's `uuid`.
   */
  function fallLineCollection(geojson) {
    const features = (geojson && geojson.features) || [];
    const arrows = [];
    for (let i = 0; i < features.length; i += 1) {
      const properties = (features[i] && features[i].properties) || {};
      if (properties.pending) continue;
      const slope = properties.slope;
      const marks = slope && slope.fall_lines;
      const points = (slope && slope.points) || [];
      if (!Array.isArray(marks) || !Array.isArray(points)) continue;
      for (let j = 0; j < marks.length; j += 1) {
        const mark = marks[j] || {};
        const index = mark.i;
        const deg = mark.deg;
        if (typeof index !== 'number' || typeof deg !== 'number') continue;
        if (!Number.isFinite(index) || !Number.isFinite(deg)) continue;
        // An index past the end of the geometry is a record whose halves
        // disagree. Skipping it loses one arrow; trusting it would throw
        // out of the whole routes overlay.
        const from = points[index];
        const to = points[index + 1];
        if (!Array.isArray(from) || !Array.isArray(to)) continue;
        arrows.push({
          type: 'Feature',
          geometry: {
            type: 'Point',
            coordinates: [(from[0] + to[0]) / 2, (from[1] + to[1]) / 2],
          },
          properties: Object.assign(
            { deg: deg },
            properties.uuid ? { uuid: properties.uuid } : {},
          ),
        });
      }
    }
    return { type: 'FeatureCollection', features: arrows };
  }

  self.pwaRouteSlopeCore = Object.freeze({
    CLASSES: CLASSES,
    UNKNOWN_COLOUR: UNKNOWN_COLOUR,
    UNKNOWN_TOKEN: UNKNOWN_TOKEN,
    PASSAGE_CORE_COLOUR: PASSAGE_CORE_COLOUR,
    PASSAGE_CORE_TOKEN: PASSAGE_CORE_TOKEN,
    FALL_LINE_COLOUR: FALL_LINE_COLOUR,
    FALL_LINE_TOKEN: FALL_LINE_TOKEN,
    classify: classify,
    colourExpression: colourExpression,
    segmentPaths: segmentPaths,
    segmentFeatures: segmentFeatures,
    segmentCollection: segmentCollection,
    fallLineCollection: fallLineCollection,
  });
})();
