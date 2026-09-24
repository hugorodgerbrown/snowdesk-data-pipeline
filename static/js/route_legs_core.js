/*
 * static/js/route_legs_core.js — drawing a saved route as its legs and the
 * transitions between them (SNOW-1017).
 *
 * SNOW-910 painted each owned route in 25 m slope-class segments. On a real
 * track that is a textured line, and the reader had to zoom in to learn
 * anything from it. This module is what replaced it: one LineString per
 * leg, a climb or a descent, and one numbered point at each transition.
 * The reasoning is docs/decisions/legs-not-slope-classes-on-the-map.md.
 *
 * THE GEOMETRY IS THE ROUTE'S OWN. Each wire leg (apps/routes/services/
 * leg_wire.py) carries two index pairs: `from`/`to` into the slope
 * record's segments, which the cursor and the rails are keyed on, and
 * `point_from`/`point_to` into the route's stored coordinates, which is
 * what a leg is sliced out of here. The stored coordinates rather than the
 * slope record's 25 m points, because an unsampled route has no slope
 * record and still has legs — a leg is a fact about the geometry. Adjacent
 * legs SHARE their seam point, so the lines meet with no gap.
 *
 * A PENDING ROUTE PRODUCES NOTHING, `route_slope_core.js`'s rule for its
 * reason: a followed share's one line is a teal dash saying "this one is
 * not yours yet", and cutting it into legs would spend that line on a
 * second message. A leg without point indices (an older cached payload,
 * from before SNOW-1017) is skipped, and its route stays on the flat line
 * only if it carries no `legs` at all — see map.js's flat-route filter.
 *
 * Hex literals rather than CSS variables: MapLibre paint properties take
 * literal colours and cannot reference a custom property. Each names the
 * token it mirrors, the convention `route_slope_core.js` follows.
 *
 * Every function here is pure.
 *
 * Exports (frozen `self.pwaRouteLegsCore`):
 *
 *   LEG_CLIMB_COLOUR          — a climbing leg's line
 *   LEG_DESCENT_COLOUR        — a descending leg's line
 *   legCollection(fc)         — one LineString per leg, `{uuid, i, climbing}`
 *   transitionCollection(fc)  — one Point per transition,
 *                               `{uuid, n, climbing}`
 *   passageCollection(fc)     — the no-fall passages, tagged with their
 *                               leg's `climbing`
 *   dimOpacity(open, on, off) — the opacity expression a selection paints
 */

// @ts-check

(function () {
  'use strict';

  /**
   * A climbing leg's colour. Mirrors `--color-route-rail-climb` in
   * `src/css/main.css`, so a leg on the map is the colour of the same leg
   * on the rail below it.
   */
  const LEG_CLIMB_COLOUR = '#c026d3';

  /**
   * A descending leg's colour. Mirrors `--color-route-rail-descent` in
   * `src/css/main.css`.
   */
  const LEG_DESCENT_COLOUR = '#64748b';

  /**
   * One leg as it arrives on the wire. Every field is optional because it
   * arrives from a feature property and is checked rather than trusted.
   *
   * @typedef {object} WireLeg
   * @property {number} [i] The leg's 1-based number.
   * @property {number} [from] Its first slope segment, inclusive.
   * @property {number} [to] Its last slope segment, inclusive.
   * @property {boolean} [climbing] Whether it gains height.
   * @property {number} [point_from] Its first coordinate index.
   * @property {number} [point_to] Its last coordinate index, inclusive.
   */

  /**
   * @typedef {{type: string, features: Array<object>}} FeatureCollection
   */

  /**
   * An empty FeatureCollection, which is what every fallback has to be:
   * `setData` throws on a null.
   *
   * @returns {FeatureCollection}
   */
  function empty() {
    return { type: 'FeatureCollection', features: [] };
  }

  /**
   * Whether a value is a usable, non-negative integer index.
   *
   * @param {*} value The value to test.
   * @returns {boolean}
   */
  function isIndex(value) {
    return Number.isInteger(value) && value >= 0;
  }

  /**
   * The owned routes that carry legs, with the pieces each reader needs.
   *
   * @param {?{features?: Array<any>}} geojson The routes FeatureCollection.
   * @returns {Array<{uuid: ?string, coordinates: Array<Array<number>>,
   *   legs: Array<WireLeg>}>}
   */
  function leggedRoutes(geojson) {
    const features = (geojson && geojson.features) || [];
    const routes = [];
    for (let i = 0; i < features.length; i += 1) {
      const feature = features[i] || {};
      const properties = feature.properties || {};
      if (properties.pending) continue;
      if (!Array.isArray(properties.legs) || !properties.legs.length) continue;
      const coordinates = feature.geometry && feature.geometry.coordinates;
      if (!Array.isArray(coordinates)) continue;
      routes.push({
        uuid: properties.uuid ? String(properties.uuid) : null,
        coordinates: coordinates,
        legs: properties.legs,
      });
    }
    return routes;
  }

  /**
   * Whether a wire leg's point indices slice a line out of `coordinates`.
   *
   * @param {WireLeg} leg The leg.
   * @param {Array<*>} coordinates The route's coordinates.
   * @returns {boolean}
   */
  function slices(leg, coordinates) {
    return isIndex(leg.point_from)
      && isIndex(leg.point_to)
      && /** @type {number} */ (leg.point_from) < /** @type {number} */ (leg.point_to)
      && /** @type {number} */ (leg.point_to) < coordinates.length;
  }

  /**
   * One LineString per leg of every owned route.
   *
   * Sliced `coordinates[point_from..point_to]`, both ends inclusive, so a
   * leg's last coordinate is the next leg's first. Properties are the
   * owning route's `uuid` — a tap on a leg resolves back to its route —
   * the leg's number `i`, which the selection's opacity expression
   * matches on, and `climbing`, which picks the layer.
   *
   * @param {?{features?: Array<any>}} geojson The routes FeatureCollection.
   * @returns {FeatureCollection} The legs, possibly none.
   */
  function legCollection(geojson) {
    const out = empty();
    const routes = leggedRoutes(geojson);
    for (let r = 0; r < routes.length; r += 1) {
      const route = routes[r];
      for (let j = 0; j < route.legs.length; j += 1) {
        const leg = route.legs[j] || {};
        if (!slices(leg, route.coordinates)) continue;
        out.features.push({
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: route.coordinates.slice(
              /** @type {number} */ (leg.point_from),
              /** @type {number} */ (leg.point_to) + 1,
            ),
          },
          properties: Object.assign(
            { i: leg.i, climbing: leg.climbing === true },
            route.uuid ? { uuid: route.uuid } : {},
          ),
        });
      }
    }
    return out;
  }

  /**
   * One numbered Point at each transition of every owned route.
   *
   * A transition is where leg 2 onwards starts, so a route with n legs has
   * n − 1 markers numbered 1 to n − 1 in track order, and a single-leg
   * route has none. Each carries the owning route's `uuid` and the
   * `climbing` flag of the leg it opens, which colours the marker.
   *
   * @param {?{features?: Array<any>}} geojson The routes FeatureCollection.
   * @returns {FeatureCollection} The markers, possibly none.
   */
  function transitionCollection(geojson) {
    const out = empty();
    const routes = leggedRoutes(geojson);
    for (let r = 0; r < routes.length; r += 1) {
      const route = routes[r];
      let n = 0;
      for (let j = 1; j < route.legs.length; j += 1) {
        const leg = route.legs[j] || {};
        n += 1;
        if (!isIndex(leg.point_from)) continue;
        const point = route.coordinates[/** @type {number} */ (leg.point_from)];
        if (!Array.isArray(point) || point.length < 2) continue;
        out.features.push({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [point[0], point[1]] },
          properties: Object.assign(
            { n: n, climbing: leg.climbing === true },
            route.uuid ? { uuid: route.uuid } : {},
          ),
        });
      }
    }
    return out;
  }

  /**
   * The no-fall passage segments of every owned route, each tagged with
   * the `climbing` flag of the leg it lies in.
   *
   * The segments are `route_slope_core.js`'s: `segmentFeatures` emits one
   * feature per entry of `slope.angles`, in order, so a segment's position
   * in its output IS its sample index — the index space the legs' `from`
   * and `to` are in. The passage's edge then takes the leg's colour, so
   * the split reads as part of the line it splits.
   *
   * Empty when `route_slope_core.js` is not loaded: the passages are an
   * addition to the leg lines, and a missing core should cost them only.
   *
   * @param {?{features?: Array<any>}} geojson The routes FeatureCollection.
   * @returns {FeatureCollection} The passage segments, possibly none.
   */
  function passageCollection(geojson) {
    const out = empty();
    const slopeCore = self.pwaRouteSlopeCore;
    if (!slopeCore || !slopeCore.segmentFeatures) return out;
    const features = (geojson && geojson.features) || [];
    for (let r = 0; r < features.length; r += 1) {
      const properties = (features[r] && features[r].properties) || {};
      /** @type {Array<WireLeg>} */
      const legs = Array.isArray(properties.legs) ? properties.legs : [];
      const segments = slopeCore.segmentFeatures(features[r]);
      for (let index = 0; index < segments.length; index += 1) {
        const segment = segments[index];
        if (!segment.properties || segment.properties.passage !== true) continue;
        const leg = legs.find(
          (candidate) => candidate
            && typeof candidate.from === 'number'
            && typeof candidate.to === 'number'
            && candidate.from <= index
            && index <= candidate.to,
        );
        out.features.push({
          type: 'Feature',
          geometry: segment.geometry,
          properties: Object.assign({}, segment.properties, {
            climbing: Boolean(leg && leg.climbing === true),
          }),
        });
      }
    }
    return out;
  }

  /**
   * The MapLibre opacity a selection paints a leg layer with.
   *
   * With a leg open, the open leg keeps `on` and every other leg — on the
   * same route and on every other route — drops to `off`. With none open
   * it is plain `on`, so closing a leg restores the lines exactly.
   *
   * @param {?{uuid?: ?string, i?: number}} open The open leg's route and
   *   number, or null when nothing is open.
   * @param {number} on The opacity of the open leg, and of every leg when
   *   none is open.
   * @param {number} off The opacity of every other leg.
   * @returns {number|Array<*>} A number or a `case` expression.
   */
  function dimOpacity(open, on, off) {
    if (!open || !open.uuid || typeof open.i !== 'number') return on;
    return [
      'case',
      ['all', ['==', ['get', 'uuid'], open.uuid], ['==', ['get', 'i'], open.i]],
      on,
      off,
    ];
  }

  self.pwaRouteLegsCore = Object.freeze({
    LEG_CLIMB_COLOUR: LEG_CLIMB_COLOUR,
    LEG_DESCENT_COLOUR: LEG_DESCENT_COLOUR,
    legCollection: legCollection,
    transitionCollection: transitionCollection,
    passageCollection: passageCollection,
    dimOpacity: dimOpacity,
  });
})();
