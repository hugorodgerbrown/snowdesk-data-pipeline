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
 * turns that into the two-point LineStrings MapLibre paints, and decides
 * which colour bucket an angle falls in.
 *
 * THREE STATES, AND THEY MUST NOT COLLAPSE INTO TWO:
 *
 *   - NO `slope` PROPERTY AT ALL — the route has never been sampled. It
 *     produces NOTHING here, and `map.js` draws it as the flat line it has
 *     always been. An unsampled route is not an unknown one, and drawing
 *     a dashed "we looked and could not tell" over a track nothing has
 *     looked at would be a claim we have not earned.
 *   - A NULL ANGLE — sampled, no answer. That becomes an `unknown: true`
 *     feature, which `map.js` paints dashed and grey, and it NEVER carries
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
 *   STEEP_THRESHOLD_DEG    — the angle a length is counted against
 *   classify(angle)        — a bucket index, or null for an unknown
 *   segmentFeatures(f)     — one OWNED route feature -> its segments
 *   segmentCollection(fc)  — a routes FeatureCollection -> all of them
 *   summaryLines(terrain)  — the same record in words (SNOW-961)
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
   * The two-point LineStrings one route's slope record draws as.
   *
   * The record's N + 1 coordinates are shared endpoints: segment i runs
   * from `points[i]` to `points[i + 1]`. Pairing them back out here is
   * what lets the payload carry half the coordinates it otherwise would.
   *
   * Each feature carries the owning route's `uuid`, because these layers
   * are what a tap on a sampled route lands on and `map.js` has to get
   * from the segment back to the route to open its popup.
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
   * @param {?{properties?: any}} feature One route feature from the routes
   *   payload, as served by `routes:geojson`.
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

    const features = [];
    for (let i = 0; i < angles.length; i += 1) {
      const slopeClass = classify(angles[i]);
      features.push({
        type: 'Feature',
        geometry: { type: 'LineString', coordinates: [points[i], points[i + 1]] },
        // An unknown segment carries `unknown` and NO `slope_class`, and a
        // known one the reverse. Never both, never neither — the two
        // layers filter on exactly this and a segment answering to both
        // would be painted twice.
        properties: slopeClass === null
          ? Object.assign({ unknown: true }, identity)
          : Object.assign({ slope_class: slopeClass }, identity),
      });
    }
    return features;
  }

  /**
   * Every route's segments, as one FeatureCollection.
   *
   * The source `map.js` hands to MapLibre. Always a valid collection, even
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
   * The angle at and above which ground is reported as steep.
   *
   * Mirrors `STEEP_THRESHOLD_DEG` in
   * `apps/routes/services/slope_summary.py`, which is where the reasoning
   * for 30° lives. Only the LABEL is built here — the metres were counted
   * against that threshold on the server, so changing this constant alone
   * would relabel a figure without recounting it.
   */
  const STEEP_THRESHOLD_DEG = 30;

  // Below this many metres a length is reported in metres rather than
  // kilometres. "0.1km over 30°" is a figure the reader has to convert
  // back; "80m over 30°" is the same fact already in the unit they think
  // in for a short passage.
  const KILOMETRE_M = 1000;

  // A shortfall under this is not reported as unsurveyed ground.
  //
  // One metre, which is small enough to report every real gap and exists
  // only to absorb float residue. `sampled_m` and `surveyed_m` are summed
  // from the SAME lengths, so their difference is the exact length of the
  // segments the terrain could not answer for — not an estimate, and not
  // a comparison between two different measurements of the track. A
  // single unknown segment is about one stride (25 m) and IS a coverage
  // gap the reader should be told about, so the floor must stay far below
  // it. The two figures are rounded independently to a tenth of a metre,
  // which is the only way a wholly-surveyed track can differ from itself.
  const UNSURVEYED_FLOOR_M = 1;

  /**
   * One length, as the string key and params that render it.
   *
   * @param {number} metres The length.
   * @param {string} kmKey The string key for a kilometre rendering.
   * @param {string} mKey The string key for a metre rendering.
   * @returns {{key: string, params: object}} The descriptor.
   */
  function lengthLine(metres, kmKey, mKey) {
    if (metres < KILOMETRE_M) {
      return { key: mKey, params: { m: String(Math.round(metres)) } };
    }
    return { key: kmKey, params: { km: (metres / KILOMETRE_M).toFixed(1) } };
  }

  /**
   * What a route's terrain summary says, as string keys and params.
   *
   * Returns DESCRIPTORS rather than text: the caller owns the strings
   * (`window.pwaStrings`, from the surface partial's `<template>`), so
   * nothing here can ship an English literal to a translated page — the
   * rule `tox -e i18n-lint` enforces.
   *
   * THE ALL-UNKNOWN CASE IS ITS OWN LINE, not a set of zeroes. A route
   * outside the terrain coverage has no steepest angle and no steep
   * length, and printing "0m over 30°" for it would state the ground is
   * gentle on the strength of never having looked at it. It says only
   * that it is unsurveyed, which is the one thing known about it.
   *
   * @param {?object} terrain The feature's `terrain` property — the
   *   server's summary (`apps/routes/services/slope_summary.py`), already
   *   parsed. Null/absent for a route that has never been sampled, which
   *   produces no lines at all rather than an unsurveyed claim.
   * @returns {Array<{key: string, params: object}>} The lines, in order.
   */
  function summaryLines(terrain) {
    if (!terrain || typeof terrain !== 'object') return [];

    const sampled = typeof terrain.sampled_m === 'number' ? terrain.sampled_m : 0;
    const surveyed = typeof terrain.surveyed_m === 'number' ? terrain.surveyed_m : 0;
    if (surveyed <= 0) {
      return sampled > 0 ? [{ key: 'route-terrain-unsurveyed-all', params: {} }] : [];
    }

    const lines = [];
    if (typeof terrain.steepest_deg === 'number') {
      lines.push({
        key: 'route-terrain-steepest',
        params: { deg: String(Math.round(terrain.steepest_deg)) },
      });
    }
    if (typeof terrain.steep_m === 'number' && terrain.steep_m > 0) {
      const line = lengthLine(
        terrain.steep_m,
        'route-terrain-steep-km',
        'route-terrain-steep-m',
      );
      line.params.deg = String(STEEP_THRESHOLD_DEG);
      lines.push(line);
    }
    const unsurveyed = sampled - surveyed;
    if (unsurveyed >= UNSURVEYED_FLOOR_M) {
      lines.push(
        lengthLine(
          unsurveyed,
          'route-terrain-unsurveyed-km',
          'route-terrain-unsurveyed-m',
        ),
      );
    }
    return lines;
  }

  self.pwaRouteSlopeCore = Object.freeze({
    CLASSES: CLASSES,
    UNKNOWN_COLOUR: UNKNOWN_COLOUR,
    UNKNOWN_TOKEN: UNKNOWN_TOKEN,
    STEEP_THRESHOLD_DEG: STEEP_THRESHOLD_DEG,
    classify: classify,
    segmentFeatures: segmentFeatures,
    segmentCollection: segmentCollection,
    summaryLines: summaryLines,
  });
})();
