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
 *   SELECTED_OUTLINE_ROUTE_OPACITY
 *   selectedOutlinePaint(routeOpen)
 *   FILL_FADE_START_ZOOM / FILL_FADE_END_ZOOM / FILL_TRACE_OPACITY /
 *   EDGE_MIN_ZOOM / EDGE_WIDTH_PX / EDGE_OFFSET_PX
 *   regionFillOpacityExpr(resting, emphasisRatio)
 *   regionEdgeOpacityExpr(resting)
 *   regionLabelFeatures(features, ratingById, paintedIds)
 *
 * SNOW-1375 (Hugo, 2 Nov 2026): the region colour gives way by zoom. The
 * fill fades from its resting strength at z8.5 to a faint trace at z10, and
 * from z10 a band of the region's colour runs just inside its edge, with a
 * label giving its level and name. A region whose slopes the problem-terrain
 * layer paints (feature-state ``terrain``) draws none of the three. See
 * docs/decisions/the-region-colour-gives-way-by-zoom.md.
 */

// @ts-check

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

  /**
   * How strongly the selected region's outline draws while a route is open:
   * the route is what the reader is looking at, and a near-black ring
   * crossing it reads as part of the route (2026-10-19).
   */
  const SELECTED_OUTLINE_ROUTE_OPACITY = 0.4;

  /**
   * The paint for `regions-line-selected`, the selected region's outline.
   *
   * It drew 4 px near-black at every zoom, which is right while the whole
   * region is on screen and too heavy once the reader is in close, where
   * the ring is one valley's edge among the terrain (2026-10-19). So it
   * tapers with zoom — 4 px to z9, 1.5 px from z12 — and recedes further
   * while a route is open. A region that is neither selected nor
   * previewing stays at 0 either way: the layer draws every feature and
   * the opacity is what picks the one (SNOW-172).
   *
   * @param {boolean} routeOpen Whether the route panel is open.
   * @returns {{'line-width': Array<*>, 'line-opacity': Array<*>}}
   */
  function selectedOutlinePaint(routeOpen) {
    return {
      'line-width': ['interpolate', ['linear'], ['zoom'], 9, 4, 12, 1.5],
      'line-opacity': [
        'case',
        [
          'any',
          ['boolean', ['feature-state', 'selected'], false],
          ['boolean', ['feature-state', 'previewing'], false],
        ],
        routeOpen ? SELECTED_OUTLINE_ROUTE_OPACITY : 1,
        0,
      ],
    };
  }

  // SNOW-1375: the zooms and sizes of the hand-over from fill to edge.
  const FILL_FADE_START_ZOOM = 8.5;
  const FILL_FADE_END_ZOOM = 10;
  const FILL_TRACE_OPACITY = 0.08;
  const EDGE_MIN_ZOOM = 10;
  const EDGE_WIDTH_PX = 8;
  // Positive: for polygon features MapLibre's line-offset is an INSET when
  // positive and an outset when negative (the style spec's line-offset). That
  // holds for rings in one consistent winding, so /api/regions.geojson serves
  // RFC 7946 winding — outer rings counter-clockwise, holes clockwise,
  // apps/regions/services/geometry.py — and every region gets its band on its
  // own side of the edge.
  const EDGE_OFFSET_PX = 4;
  // The band at the default 0.5 resting strength reads at about 0.55.
  const EDGE_OPACITY_RATIO = 1.1;
  const EDGE_OPACITY_MAX = 0.9;

  /** Feature-state: the region's slopes are being painted (SNOW-979). */
  const SLOPES_PAINTED = ['boolean', ['feature-state', 'terrain'], false];
  /** Feature-state: the region is selected or previewed from its sheet. */
  const EMPHASISED = [
    'any',
    ['boolean', ['feature-state', 'selected'], false],
    ['boolean', ['feature-state', 'previewing'], false],
  ];

  /**
   * One stop's per-feature fill opacity: 0 where slopes are painted, the
   * emphasised strength for a selected region, else the value.
   *
   * @param {number} value The resting opacity at this stop.
   * @param {number} emphasisRatio How much stronger a selected region is.
   * @returns {Array<*>}
   */
  function fillStop(value, emphasisRatio) {
    const emphasised = Math.min(1, +(value * emphasisRatio).toFixed(3));
    return ['case', SLOPES_PAINTED, 0, EMPHASISED, emphasised, value];
  }

  /**
   * The choropleth's ``fill-opacity``: the resting strength up to z8.5,
   * fading to a faint trace by z10 and holding there.
   *
   * MapLibre accepts ``['zoom']`` only at the top of a paint expression, so
   * the zoom ``interpolate`` is outermost and each stop carries its own
   * per-feature ``case``.
   *
   * @param {number} resting The user's resting opacity, 0–1 (the Bulletin
   *   opacity steps); 0 is off.
   * @param {number} emphasisRatio The selected-region multiplier.
   * @returns {Array<*>|number} A paint expression, or 0 when off.
   */
  function regionFillOpacityExpr(resting, emphasisRatio) {
    if (!(resting > 0)) return 0;
    const trace = Math.min(resting, FILL_TRACE_OPACITY);
    return [
      'interpolate', ['linear'], ['zoom'],
      FILL_FADE_START_ZOOM, fillStop(resting, emphasisRatio),
      FILL_FADE_END_ZOOM, fillStop(trace, emphasisRatio),
    ];
  }

  /**
   * The edge band's ``line-opacity``: scaled from the resting strength, 0
   * where slopes are painted, 0 altogether when the layer is off.
   *
   * @param {number} resting The user's resting opacity, 0–1.
   * @returns {Array<*>|number}
   */
  function regionEdgeOpacityExpr(resting) {
    if (!(resting > 0)) return 0;
    const value = Math.min(EDGE_OPACITY_MAX, +(resting * EDGE_OPACITY_RATIO).toFixed(3));
    return ['case', SLOPES_PAINTED, 0, value];
  }

  /**
   * A rating key → the EAWS level digit the label leads with.
   *
   * @type {Readonly<Object<string, string>>}
   */
  const LEVEL_DIGIT = Object.freeze({
    low: '1', moderate: '2', considerable: '3', high: '4', very_high: '5',
  });

  /**
   * The ``region-labels`` source's features: one point per region that has
   * a label position, carrying the text the label layer draws.
   *
   * ``text-field`` cannot read feature-state, where the rating lives, so the
   * level comes in as data and the source is rebuilt when a day's ratings
   * are painted. ``name`` is the label alone, which Terrain mode draws.
   *
   * @param {Array<{id: number, properties: Object<string, *>}>} features
   *   The loaded region features (``FEATURE_BY_REGION_ID``'s values).
   * @param {Map<number, string>} ratingById Each feature id's painted rating.
   * @param {Set<number>} paintedIds Feature ids whose slopes are painted.
   * @returns {{type: string, features: Array<Object>}}
   */
  function regionLabelFeatures(features, ratingById, paintedIds) {
    const out = [];
    for (const f of features) {
      const props = (f && f.properties) || {};
      const at = props.label;
      if (!Array.isArray(at) || at.length !== 2) continue;
      const digit = LEVEL_DIGIT[ratingById.get(f.id) || ''] || '';
      out.push({
        type: 'Feature',
        id: f.id,
        geometry: { type: 'Point', coordinates: [at[0], at[1]] },
        properties: {
          text: digit ? digit + ' · ' + props.name : String(props.name || ''),
          // The bare name, for Terrain mode, where no level is drawn.
          name: String(props.name || ''),
          country: props.country,
          covered: props.covered,
          painted: paintedIds.has(f.id),
        },
      });
    }
    return { type: 'FeatureCollection', features: out };
  }

  self.pwaChoroplethCore = Object.freeze({
    paintRatingsFrame: paintRatingsFrame,
    BACKDROP_COLOUR: BACKDROP_COLOUR,
    compositeOverBackdrop: compositeOverBackdrop,
    repaintDateForStyleSwap: repaintDateForStyleSwap,
    SELECTED_OUTLINE_ROUTE_OPACITY: SELECTED_OUTLINE_ROUTE_OPACITY,
    selectedOutlinePaint: selectedOutlinePaint,
    FILL_FADE_START_ZOOM: FILL_FADE_START_ZOOM,
    FILL_FADE_END_ZOOM: FILL_FADE_END_ZOOM,
    FILL_TRACE_OPACITY: FILL_TRACE_OPACITY,
    EDGE_MIN_ZOOM: EDGE_MIN_ZOOM,
    EDGE_WIDTH_PX: EDGE_WIDTH_PX,
    EDGE_OFFSET_PX: EDGE_OFFSET_PX,
    regionFillOpacityExpr: regionFillOpacityExpr,
    regionEdgeOpacityExpr: regionEdgeOpacityExpr,
    regionLabelFeatures: regionLabelFeatures,
  });
})();
