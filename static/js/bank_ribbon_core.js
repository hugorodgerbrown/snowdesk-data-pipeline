/*
 * static/js/bank_ribbon_core.js — the bank's level-ski wedge geometry
 * (SNOW-1021; drawn as wedges since SNOW-1031).
 *
 * The bank angle is how far the ground tilts ACROSS a track: a traverse of
 * a 40° face banks at 40°, a descent straight down it at 0°. The server
 * derives it per segment (apps/routes/services/bank.py) and sends it as
 * `properties.slope.banks`, a flat list aligned with `angles`, one whole
 * SIGNED degree each — positive where the ground falls away on the
 * skier's right — and null for an unknown segment.
 *
 * THE GLYPH IS A PAIR OF LEVEL SKIS ON THE GROUND. Each glyph is
 * `2 × halfWidth` px wide and pivots on its centre (x, y): the level line
 * through it is the skis, held level, and the GROUND line runs through the
 * same pivot at the bank angle, from the uphill end (x − halfWidth, y − dy)
 * to the downhill end (x + halfWidth, y + dy), mirrored by the sign of the
 * roll. The two triangles between the skis and the ground are filled: the
 * uphill one solid (the ground above the skis) and the downhill one pale
 * (the air below them), so the pale side is the side the ground falls
 * away to. A positive roll puts the pale wedge on the right.
 *
 * The tilt is exaggerated: dy = halfWidth × EXAGGERATION × tan|roll|, at
 * EXAGGERATION = 1.5, so a 10° bank still reads at 7 px wide. It is capped
 * at CAP_PX = 13, which a 1.5× bank reaches at about 51°: anything steeper
 * draws the same, and the row never grows past 2 × CAP_PX.
 *
 * Under MIN_FILL_PX of rise the fills are dropped and only the ground line
 * is drawn, so a track on the fall line reads as a row of flat dashes
 * without a gap in it: 0° is a reading, not a missing one.
 *
 * THE GLYPH COUNT FOLLOWS THE PITCH, NOT THE SEGMENT COUNT. A zoomed leg
 * of twenty segments across 600 px and a whole 600-segment tour across
 * the same width both get 40 glyphs at a 15 px pitch; each glyph reads the
 * segment under its own x.
 *
 * THE X → INDEX CONVERSION IS THE CALLER'S. The rule route_cursor_core.js
 * set: each surface owns its geometry and converts it to a sample index
 * in its own code. `indexAt(x)` is that conversion; this module never
 * learns how the rail lays out its axis.
 *
 * A null bank draws NO glyph — a gap, never a flat one, which would claim
 * the ground is level across the track where nothing is known. See
 * docs/decisions/the-bank-angle-is-drawn-signed.md for why the sign is
 * drawn at all.
 *
 * This module owns no DOM, no strings and no colours; the mount
 * (route_rail_two.js) owns the tokens.
 *
 * Exports (frozen `self.pwaBankRibbonCore`):
 *
 *   GLYPH_PITCH       → px between glyph centres, 15
 *   GLYPH_HALF_WIDTH  → half a glyph's width in px, 7
 *   EXAGGERATION      → the tilt's exaggeration, 1.5
 *   CAP_PX            → the most a glyph rises either side of y, 13
 *   MIN_FILL_PX       → under this rise the wedges are not filled, 0.6
 *   bankWedges({banks, width, indexAt, pitch, halfWidth, exaggeration,
 *     capPx, minFillPx, y})
 *     — one glyph per `pitch` px across `width`
 */

// @ts-check

(function () {
  'use strict';

  /** Px between glyph centres at a wide view. */
  const GLYPH_PITCH = 15;

  /** Half a glyph's width in px; the glyph is 14 px wide. */
  const GLYPH_HALF_WIDTH = 7;

  /** How much the drawn tilt exaggerates the bank's tangent. */
  const EXAGGERATION = 1.5;

  /** The most a glyph rises above (and falls below) its centre, in px. */
  const CAP_PX = 13;

  /** Under this rise in px the wedges are too thin to fill. */
  const MIN_FILL_PX = 0.6;

  /**
   * @typedef {[number, number]} Point
   *   An `[x, y]` pair in the caller's pixel space (y down).
   */

  /**
   * @typedef {{
   *   x: number,
   *   index: number,
   *   roll: number,
   *   dy: number,
   *   ground: {x1: number, y1: number, x2: number, y2: number},
   *   up: ?Array<Point>,
   *   down: ?Array<Point>,
   * }} BankWedge
   *   One glyph: its centre `x`, the sample `index` it reads, the signed
   *   `roll` in degrees, the rise `dy` either side of the centre, the
   *   ground line from its uphill end (`x1`, `y1`) to its downhill end
   *   (`x2`, `y2`), and the uphill (`up`, drawn solid) and downhill
   *   (`down`, drawn pale) triangles — both null under `minFillPx`. In the
   *   caller's pixel space, y down as SVG and canvas are.
   */

  /**
   * @typedef {{
   *   banks: Array<?number>,
   *   width: number,
   *   indexAt: function(number): number,
   *   pitch?: number,
   *   halfWidth?: number,
   *   exaggeration?: number,
   *   capPx?: number,
   *   minFillPx?: number,
   *   y?: number,
   * }} BankWedgeOptions
   */

  /**
   * Lay out the level-ski wedges across a width.
   *
   * Glyphs sit at the centre of each `pitch`-wide slot, so the first is
   * half a pitch in from the left edge and the last half a pitch or more
   * in from the right.
   *
   * @param {BankWedgeOptions} options
   *   `banks` — the wire's signed whole degrees, null where unknown.
   *   `width` — the drawing's width in px.
   *   `indexAt` — the caller's x → sample-index conversion. A fractional
   *     answer is rounded; one outside `banks` draws no glyph.
   *   `pitch` — px between glyphs (default `GLYPH_PITCH`).
   *   `halfWidth` — half a glyph's width in px (default `GLYPH_HALF_WIDTH`).
   *   `exaggeration` — the tilt's exaggeration (default `EXAGGERATION`).
   *   `capPx` — the most `dy` may reach (default `CAP_PX`).
   *   `minFillPx` — under this `dy` the wedges are null
   *     (default `MIN_FILL_PX`).
   *   `y` — the glyphs' vertical centre (default 0).
   * @returns {Array<BankWedge>} The glyphs, left to right.
   */
  function bankWedges(options) {
    const banks = options.banks;
    const width = options.width;
    const indexAt = options.indexAt;
    const pitch = options.pitch === undefined ? GLYPH_PITCH : options.pitch;
    const halfWidth = options.halfWidth === undefined ? GLYPH_HALF_WIDTH : options.halfWidth;
    const exaggeration = options.exaggeration === undefined ? EXAGGERATION : options.exaggeration;
    const capPx = options.capPx === undefined ? CAP_PX : options.capPx;
    const minFillPx = options.minFillPx === undefined ? MIN_FILL_PX : options.minFillPx;
    const y = options.y === undefined ? 0 : options.y;

    if (!Array.isArray(banks)) throw new TypeError('banks must be an array');
    if (typeof indexAt !== 'function') throw new TypeError('indexAt must be a function');
    if (!Number.isFinite(width) || width < 0) throw new RangeError('width must be >= 0');
    if (!Number.isFinite(pitch) || pitch <= 0) throw new RangeError('pitch must be > 0');

    /** @type {Array<BankWedge>} */
    const wedges = [];
    const count = Math.floor(width / pitch);
    for (let slot = 0; slot < count; slot += 1) {
      const x = (slot + 0.5) * pitch;
      const index = Math.round(indexAt(x));
      if (!Number.isInteger(index) || index < 0 || index >= banks.length) continue;
      const roll = banks[index];
      // A gap, not a flat glyph: null is "not known", never "level".
      if (typeof roll !== 'number' || !Number.isFinite(roll)) continue;
      const radians = (Math.abs(roll) * Math.PI) / 180;
      const dy = Math.min(capPx, halfWidth * exaggeration * Math.tan(radians));
      // The uphill end is on the side opposite the fall: positive → left.
      const sign = roll >= 0 ? 1 : -1;
      const xu = x - halfWidth * sign;
      const xd = x + halfWidth * sign;
      const filled = dy >= minFillPx;
      wedges.push(
        Object.freeze({
          x: x,
          index: index,
          roll: roll,
          dy: dy,
          ground: Object.freeze({ x1: xu, y1: y - dy, x2: xd, y2: y + dy }),
          up: filled
            ? /** @type {Array<Point>} */ ([[xu, y - dy], [x, y], [xu, y]])
            : null,
          down: filled
            ? /** @type {Array<Point>} */ ([[x, y], [xd, y + dy], [xd, y]])
            : null,
        }),
      );
    }
    return wedges;
  }

  self.pwaBankRibbonCore = Object.freeze({
    GLYPH_PITCH: GLYPH_PITCH,
    GLYPH_HALF_WIDTH: GLYPH_HALF_WIDTH,
    EXAGGERATION: EXAGGERATION,
    CAP_PX: CAP_PX,
    MIN_FILL_PX: MIN_FILL_PX,
    bankWedges: bankWedges,
  });
})();
