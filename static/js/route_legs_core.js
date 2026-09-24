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
 * second message.
 *
 * A ROUTE WHOSE LEGS CANNOT ALL BE SLICED DRAWS FLAT. An overlay payload
 * cached before SNOW-1017 carries `legs` without point indices. map.js's
 * flat-line filter tests the presence of `legs`, so such a route would
 * leave the flat line and draw no legs — invisible. `withDrawableLegs`
 * is the answer: the copy of the payload handed to the `routes` source
 * has `legs` removed from any route `hasDrawableLegs` rejects, so the
 * flat line keeps it. `legCollection` and `transitionCollection` apply
 * the same test, so a route is drawn one way or the other, never both
 * and never neither.
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
 *   hasDrawableLegs(f)        — whether every leg of a route can be sliced
 *   withDrawableLegs(fc)      — the payload with undrawable `legs` removed,
 *                               for the flat line's source
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
   * Whether every leg of one route feature can be sliced from its
   * coordinates.
   *
   * All or nothing: a route drawn with one leg missing would show a gap
   * that reads as a break in the track. `false` for a route with no legs
   * at all, and for any leg whose `point_from`/`point_to` are missing,
   * not integers, out of order or past the end of the geometry.
   *
   * @param {?{geometry?: any, properties?: any}} feature One route feature.
   * @returns {boolean}
   */
  function hasDrawableLegs(feature) {
    const properties = (feature && feature.properties) || {};
    const legs = properties.legs;
    const coordinates = feature && feature.geometry && feature.geometry.coordinates;
    if (!Array.isArray(legs) || !legs.length || !Array.isArray(coordinates)) return false;
    return legs.every((leg) => Boolean(leg) && slices(leg, coordinates));
  }

  /**
   * The routes payload with `legs` removed from every route whose legs
   * cannot be drawn.
   *
   * For the `routes` source only, whose flat-line filter tests the
   * presence of `legs`: a route that keeps the key there must be one the
   * leg layers draw. A shallow copy — the features that change are new
   * objects with new `properties`, the rest are the originals — so the
   * caller's payload, which the rail reads its legs from, is untouched.
   *
   * @param {?{features?: Array<any>}} geojson The routes FeatureCollection.
   * @returns {?{features?: Array<any>}} The copy, or `geojson` itself when
   *   it holds no features.
   */
  function withDrawableLegs(geojson) {
    if (!geojson || !Array.isArray(geojson.features)) return geojson;
    return Object.assign({}, geojson, {
      features: geojson.features.map((feature) => {
        const properties = feature && feature.properties;
        if (!properties || !('legs' in properties) || hasDrawableLegs(feature)) {
          return feature;
        }
        const stripped = Object.assign({}, properties);
        delete stripped.legs;
        return Object.assign({}, feature, { properties: stripped });
      }),
    });
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
      if (!hasDrawableLegs(feature)) continue;
      const coordinates = feature.geometry.coordinates;
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
    hasDrawableLegs: hasDrawableLegs,
    withDrawableLegs: withDrawableLegs,
  });
})();
