/*
 * static/js/slope_overlay_core.js — the pure half of the slope-angle
 * overlay (SNOW-691).
 *
 * The overlay is a raster from swisstopo's
 * `ch.swisstopo.hangneigung-ueber_30` WMTS layer, and everything that is
 * interesting about it is a fact about ITS COVERAGE rather than about
 * MapLibre. That fact is a rectangle, taken verbatim from the service's own
 * `WMTSCapabilities.xml` `WGS84BoundingBox`, and it matters in two places:
 *
 *   - the source's `bounds`, so MapLibre never requests a tile outside it
 *     (the service answers HTTP 400 with a JSON body out there — not an
 *     empty tile, so an unbounded source would spend real requests on
 *     errors); and
 *   - the layers-menu row, which disables itself with a reason once the
 *     viewport centre leaves the box.
 *
 * The second exists because of the safety line in the ticket: absent
 * shading must never be readable as "no steep terrain here". Unshaded
 * ground inside the box means "under 30°"; unshaded ground outside it
 * means "not surveyed", and nothing on a raster distinguishes the two.
 *
 * A third mechanism — the rectangle drawn on the map as a line layer —
 * was built and then removed: a hard box across the whole map, most of
 * whose length runs through terrain nobody is looking at, cost far more
 * legibility than the edge case was worth. The row's disable reason is
 * what carries that job now.
 *
 * The raster is NOT Switzerland plus a buffer, despite the layer's name.
 * swisstopo derive it at 10 m from a combined DEM — swissALTI3D (CH/LI),
 * RGE ALTI (FR), TINITALY/01 (IT), DGM10 (AT), DGM1 (Bavaria), EU-DEM
 * (Baden-Württemberg) — and clip the result to the rectangle below. So it
 * genuinely covers parts of five countries, and it genuinely stops dead at
 * a straight line through the Alps: the Zillertal (lon ~11.87) and the
 * Dolomites are outside it, as are the southern Écrins and the Mercantour.
 *
 * Lives outside `map.js` for the usual reason: `map.js` is one file of
 * IIFEs that jsdom cannot import, and a coverage predicate that silently
 * inverts a comparison is exactly the sort of thing that needs a test.
 *
 * Every function here is pure.
 *
 * Exports (frozen `self.pwaSlopeOverlayCore`):
 *
 *   COVERAGE_BOUNDS              — [west, south, east, north]
 *   MIN_ZOOM / MAX_ZOOM          — the zoom range worth requesting
 *   RASTER_OPACITY               — the paint constant, and why
 *   CLASSES                      — the five painted slope classes
 *   coversPoint(lng, lat)
 */

(function () {
  'use strict';

  /**
   * The layer's declared coverage, as `[west, south, east, north]`.
   *
   * Verbatim from the WMTS capabilities document's `WGS84BoundingBox`
   * (lower corner `5.140242 45.398181`, upper corner `11.47757 48.230651`),
   * reordered into the sequence MapLibre's source `bounds` takes. These are
   * NOT rounded, and should not be: they are a quoted constant from the
   * service, and the only way to keep this honest as the service changes is
   * to re-read that document rather than to nudge a number.
   */
  const COVERAGE_BOUNDS = Object.freeze([5.140242, 45.398181, 11.47757, 48.230651]);

  /**
   * The zoom range worth requesting.
   *
   * The service's `3857_17` matrix set publishes z0-17, but z17 carries no
   * more information than z16: comparing a z16 tile upscaled 2x against the
   * four native z17 tiles under it differs in 1.3% of pixels (anti-alias
   * noise), where z15->z16 differs in 2.7% and z14->z15 in 7.0%. The
   * pyramid's real detail stops at z16 and the top level is a server-side
   * upscale.
   *
   * So `maxzoom` is 16 and MapLibre overzooms above it — pixel-identical to
   * fetching z17, minus four requests per tile of screen at touring zooms.
   *
   * Declaring it at all is the SNOW-604 lesson: MapLibre defaults a raster
   * source's `maxzoom` to 22, and this service answers HTTP 400 past z17,
   * so an omitted value turns every close-in zoom into failed requests.
   */
  const MIN_ZOOM = 0;
  const MAX_ZOOM = 16;

  /**
   * The raster's paint opacity.
   *
   * swisstopo's own portal renders this layer at 0.4. Ours sits UNDER the
   * danger choropleth — which is itself translucent, and whose strength the
   * reader controls in five steps (SNOW-656) — so it is attenuated a second
   * time before it reaches the eye. 0.6 lands the composite at roughly what
   * 0.4 gives on their map, and a reader who wants the terrain clean has
   * the fill control to take the choropleth to 0.
   */
  const RASTER_OPACITY = 0.6;

  /**
   * The five painted classes, weakest first.
   *
   * The SLF slope classification, sampled off the served tiles rather than
   * copied from prose. `token` names the `@theme` custom property the
   * legend swatch is painted with (`src/css/main.css`), so the legend and
   * the raster cannot drift apart; `hex` is that token's value, kept here
   * so a test can assert the two agree.
   *
   * Everything under 30° is FULLY TRANSPARENT in the source raster, which
   * is why there is no sixth entry: the layer's name
   * (`hangneigung-ueber_30`, "slope over 30") is literal. The first three
   * classes are the 30–45° band avalanches mostly release on, which is the
   * whole reason the overlay exists — the legend says so.
   */
  const CLASSES = Object.freeze([
    Object.freeze({ id: 'slope-30', from: 30, to: 35, token: '--color-slope-30', hex: '#f2e50a' }),
    Object.freeze({ id: 'slope-35', from: 35, to: 40, token: '--color-slope-35', hex: '#f46f24' }),
    Object.freeze({ id: 'slope-40', from: 40, to: 45, token: '--color-slope-40', hex: '#de055b' }),
    Object.freeze({ id: 'slope-45', from: 45, to: 50, token: '--color-slope-45', hex: '#c889bb' }),
    Object.freeze({ id: 'slope-50', from: 50, to: null, token: '--color-slope-50', hex: '#4b4b4b' }),
  ]);

  /**
   * Whether a point falls inside the layer's declared coverage.
   *
   * Inclusive on all four edges: the bounds are the service's own, so a
   * point exactly on one is covered by it, and excluding the edge would
   * disable the menu row one pixel before the raster actually stops.
   *
   * A non-finite coordinate answers `false` rather than throwing — the
   * callers are a `moveend` handler and a source guard, and neither has
   * anywhere useful to put an exception.
   *
   * @param {number} lng Longitude in degrees.
   * @param {number} lat Latitude in degrees.
   * @returns {boolean} True when the point is inside the rectangle.
   */
  function coversPoint(lng, lat) {
    if (!Number.isFinite(lng) || !Number.isFinite(lat)) return false;
    const [west, south, east, north] = COVERAGE_BOUNDS;
    return lng >= west && lng <= east && lat >= south && lat <= north;
  }

  self.pwaSlopeOverlayCore = Object.freeze({
    COVERAGE_BOUNDS: COVERAGE_BOUNDS,
    MIN_ZOOM: MIN_ZOOM,
    MAX_ZOOM: MAX_ZOOM,
    RASTER_OPACITY: RASTER_OPACITY,
    CLASSES: CLASSES,
    coversPoint: coversPoint,
  });
})();
