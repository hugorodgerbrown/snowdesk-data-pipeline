/*
 * static/js/choropleth_core.js — one choropleth paint routine (SNOW-623).
 *
 * `static/js/map.js` painted the region choropleth in three places —
 * `repaintRegionsForDate`, `paintNewCountry` and `paintTodayRatings` (since
 * SNOW-660: `paintBootRatings`) — with
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
 *   BACKDROP_COLOUR
 *   compositeOverBackdrop(colour, alpha)
 *   repaintDateForStyleSwap(committed, urlParam)
 */

(function () {
  'use strict';

  /**
   * The fixed surface the choropleth is composited against.
   *
   * The fill layer used to be painted with a translucent `fill-opacity`
   * straight over the basemap, which meant MapLibre alpha-blended each
   * rating colour with whatever the basemap drew underneath. There are five
   * selectable basemaps and they differ enormously — openfreemap's warm
   * near-white land, swisstopo winter's blue-white hillshade, IGN's grey —
   * so one rating rendered as a different colour on each of them, and the
   * same region could read `moderate`-yellow on one basemap and a washed
   * pink on another. For a danger-rating choropleth that is a correctness
   * problem, not a cosmetic one: the colour IS the message, and the legend
   * pill next to it never moved.
   *
   * The fix is to blend against a constant instead of against the basemap
   * (`compositeOverBackdrop` below), then paint the result opaque. This
   * value is the project's own `--color-bg` warm off-white, which is within
   * a couple of points of openfreemap Liberty's land colour — so the
   * default basemap looks essentially as it did, and the other four now
   * match it rather than each going their own way.
   *
   * A literal hex rather than a token lookup because MapLibre paint
   * properties cannot resolve a CSS variable (see CLAUDE.md, "Design
   * system", rule 3 — the JS/MapLibre carve-out).
   */
  var BACKDROP_COLOUR = '#f2f0ec';

  /**
   * Parse a `#rgb` or `#rrggbb` string into `[r, g, b]` 0–255 channels.
   *
   * @param {string} colour A hex colour, with or without the leading `#`.
   * @returns {number[]} The three channels.
   * @throws {Error} If the string is not 3- or 6-digit hex — a silent
   *   fallback here would ship a wrong danger colour, which is exactly the
   *   failure this module exists to prevent.
   */
  function parseHex(colour) {
    var hex = String(colour).replace(/^#/, '');
    if (hex.length === 3) {
      hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    }
    if (!/^[0-9a-fA-F]{6}$/.test(hex)) {
      throw new Error('compositeOverBackdrop: not a hex colour: ' + colour);
    }
    return [
      parseInt(hex.slice(0, 2), 16),
      parseInt(hex.slice(2, 4), 16),
      parseInt(hex.slice(4, 6), 16),
    ];
  }

  /**
   * Alpha-composite `colour` at `alpha` over `BACKDROP_COLOUR`.
   *
   * The returned colour, painted at `fill-opacity: 1`, is pixel-identical
   * to painting `colour` at `fill-opacity: alpha` over a solid
   * `BACKDROP_COLOUR` — which is the whole point: the appearance the fill
   * used to have over a light basemap, now independent of the basemap.
   * Doing it here rather than adding an opaque underlay layer keeps the
   * choropleth one layer, so the eight places that name `regions-fill`
   * (visibility toggles, country filters, edit mode, hit-testing) do not
   * each have to learn about a second one and stay in lockstep with it.
   *
   * @param {string} colour A `#rgb` / `#rrggbb` foreground colour.
   * @param {number} alpha Opacity in 0–1.
   * @returns {string} The composited colour as `#rrggbb`.
   */
  function compositeOverBackdrop(colour, alpha) {
    var fg = parseHex(colour);
    var bg = parseHex(BACKDROP_COLOUR);
    var out = '#';
    for (var i = 0; i < 3; i += 1) {
      var value = Math.round(alpha * fg[i] + (1 - alpha) * bg[i]);
      out += Math.max(0, Math.min(255, value)).toString(16).padStart(2, '0');
    }
    return out;
  }

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

  /**
   * Which date the choropleth must be repainted for after a style swap.
   *
   * `MAP.setStyle` (the basemap picker) drops every source we added, and
   * reinstalling the regions source wipes its feature-state — which is
   * where the ratings live. Something has to repaint them, and it has to
   * know for which day.
   *
   * `map.js` asked the URL's `?d=` and did nothing when it was absent. At
   * the time absent was the *normal* state: the scrubber stripped `?d=`
   * whenever the displayed day was today (`commitDate`), and out of season
   * it snapped silently to the season's last populated day without ever
   * writing one. So the common visit — land on the map, change basemap —
   * repainted nothing and every region fell back to `no_rating` grey.
   *
   * The committed date wins because it is the only one that survives both
   * of those paths; `?d=` is still read for a swap early enough that the
   * scrubber has not committed anything yet.
   *
   * SNOW-660: `null` means neither is known — which now means no day has
   * been asked for at all, and the caller paints NOTHING rather than
   * falling back to a boot frame. There is no longer a boot frame to fall
   * back to: the map fetches ratings only for a day the URL requested, so
   * "no date" and "no colours" are the same state, deliberately.
   *
   * @param {?string} committed The last date `snowdesk:date-changed`
   *   carried (`map.js`'s `currentDisplayedDate`), or null.
   * @param {?string} urlParam The validated `?d=` value, or null.
   * @returns {?string} The date key to repaint, or null for "no date known".
   */
  function repaintDateForStyleSwap(committed, urlParam) {
    return committed || urlParam || null;
  }

  self.pwaChoroplethCore = Object.freeze({
    paintRatingsFrame: paintRatingsFrame,
    BACKDROP_COLOUR: BACKDROP_COLOUR,
    compositeOverBackdrop: compositeOverBackdrop,
    repaintDateForStyleSwap: repaintDateForStyleSwap,
  });
})();
