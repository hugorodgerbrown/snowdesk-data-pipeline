/*
 * static/js/choropleth_core.js — one choropleth paint routine (SNOW-623).
 *
 * `static/js/map.js` painted the region choropleth in three places —
 * `repaintRegionsForDate`, `paintNewCountry` and `paintTodayRatings` — with
 * the same `setFeatureState({source: 'regions', id}, {rating})` loop in
 * each. Finding D6.
 *
 * They were NOT interchangeable, which is the thing worth being careful
 * about here: two of them iterate the FRAME and leave every region absent
 * from it untouched, while the third iterates every KNOWN region and paints
 * the absent ones `no_rating`. Collapsing them onto one semantic would have
 * broken something either way:
 *
 *   - clearing when a country loads would wipe the countries already
 *     painted, since a per-country frame only names its own regions;
 *   - not clearing when the scrubbed date changes would leave the previous
 *     day's colours showing for any region with no rating on the new one.
 *
 * So `clearMissing` is a required decision at each call site rather than a
 * defaulted convenience, and `tests/js/test_choropleth_core.js` asserts both
 * behaviours are still reachable.
 *
 * Lives here rather than in `map.js` for the usual reason: `map.js` is one
 * file of IIFEs that cannot be imported under jsdom, and this is exactly the
 * kind of "two callers, two semantics" logic that needs a test.
 *
 * Exports (frozen `self.pwaChoroplethCore`):
 *
 *   paintRatingsFrame(deps, frame, options)
 */

(function () {
  'use strict';

  /**
   * Paint one ratings frame onto the region choropleth.
   *
   * @param {{
   *   featureById: Object<string, {id: (number|string)}>,
   *   setRating: function((number|string), string): void,
   *   intToRating: string[],
   * }} deps
   *   `featureById` — map.js's `FEATURE_BY_REGION_ID`, every region the map
   *     currently knows about.
   *   `setRating` — applies one region's rating; map.js passes a closure
   *     over `MAP.setFeatureState`, which is what keeps MapLibre out of
   *     this module.
   *   `intToRating` — map.js's `INT_TO_RATING` lookup.
   * @param {Object<string, number>} frame Region id → rating int, as the
   *   ratings cache stores one day.
   * @param {{clearMissing: boolean}} options `clearMissing` decides what
   *   happens to a region the frame does not name: `true` paints it
   *   `no_rating` (a date change — the previous day's colour must not
   *   linger), `false` leaves it alone (a country loading — the other
   *   countries are already painted and are not in this frame). Required:
   *   see the module header for why there is no default.
   * @returns {number} How many regions were painted — so a caller can tell
   *   "painted nothing" from "painted everything grey".
   */
  function paintRatingsFrame(deps, frame, options) {
    var ratings = frame || {};
    var painted = 0;
    var clearMissing = !!(options && options.clearMissing);

    if (clearMissing) {
      var known = deps.featureById || {};
      for (var regionID in known) {
        if (!Object.prototype.hasOwnProperty.call(known, regionID)) continue;
        var feature = known[regionID];
        if (!feature) continue;
        var value = ratings[regionID];
        deps.setRating(
          feature.id,
          value == null ? 'no_rating' : deps.intToRating[value] || 'no_rating',
        );
        painted += 1;
      }
      return painted;
    }

    for (var id in ratings) {
      if (!Object.prototype.hasOwnProperty.call(ratings, id)) continue;
      var known2 = deps.featureById || {};
      var f = known2[id];
      // A frame can name a region whose geometry has not loaded — a
      // country's ratings arrive independently of its GeoJSON. Skipping is
      // correct: there is nothing on the map to paint yet, and the
      // country-load path repaints once it is there.
      if (!f) continue;
      deps.setRating(f.id, deps.intToRating[ratings[id]] || 'no_rating');
      painted += 1;
    }
    return painted;
  }

  self.pwaChoroplethCore = Object.freeze({
    paintRatingsFrame: paintRatingsFrame,
  });
})();
