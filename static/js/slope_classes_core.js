/*
 * static/js/slope_classes_core.js — the slope-class ramp, in one place
 * (SNOW-1073).
 *
 * Five classes of steep ground, 30/35/40/45/50+ degrees, each with its
 * colour. The ramp is the SLF slope classification swisstopo's
 * "Slope classes over 30°" raster painted with (SNOW-691): those are the
 * colours a Swiss ski tourer already reads as slope angle, so Snowdesk kept
 * them when it stopped showing that raster.
 *
 * Two modules paint with it, and they must agree to the hex:
 *
 *   - terrain_filter_core.js — the terrain filter's "colour by slope class"
 *     mode, which paints the map's slope layer from our own terrain-class
 *     tiles (SLOPE_PALETTE);
 *   - route_slope_core.js — a saved route's core from z14, its rail and its
 *     elevation profile, which add a sixth "gentle" bucket in front.
 *
 * So a route crossing a shaded face is the colour of the shading under it,
 * and both colours come from the same terrain model (swissALTI3D on the
 * 5 m grid) with the same 5° boundaries. See
 * docs/decisions/slope-layer-is-painted-from-our-class-tiles.md.
 *
 * Must load before both of those modules: each reads this at parse time.
 *
 * Exports (frozen `self.pwaSlopeClassesCore`):
 *
 *   CLASSES — the five steep classes, gentlest first
 */

// @ts-check

(function () {
  'use strict';

  /**
   * The five painted classes, gentlest first.
   *
   * `from` is inclusive and `to` exclusive, so exactly 35° is in
   * `slope-35`; the classes are named for their lower bound. `token` names
   * the `@theme` custom property the legend swatch is painted with
   * (`src/css/main.css`) and `hex` is that token's value — MapLibre paint
   * properties and canvas pixels cannot read a custom property, so the
   * literal is kept here and a Vitest test asserts the two agree.
   *
   * Nothing under 30° is painted on the map, which is why there is no
   * entry for it here: route_slope_core.js adds its own gentle bucket
   * because a LINE cannot be transparent without appearing to break.
   */
  const CLASSES = Object.freeze([
    Object.freeze({ id: 'slope-30', from: 30, to: 35, token: '--color-slope-30', hex: '#f2e50a' }),
    Object.freeze({ id: 'slope-35', from: 35, to: 40, token: '--color-slope-35', hex: '#f46f24' }),
    Object.freeze({ id: 'slope-40', from: 40, to: 45, token: '--color-slope-40', hex: '#de055b' }),
    Object.freeze({ id: 'slope-45', from: 45, to: 50, token: '--color-slope-45', hex: '#c889bb' }),
    Object.freeze({ id: 'slope-50', from: 50, to: null, token: '--color-slope-50', hex: '#4b4b4b' }),
  ]);

  self.pwaSlopeClassesCore = Object.freeze({
    CLASSES: CLASSES,
  });
})();
