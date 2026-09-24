/*
 * static/js/bank_ribbon_core.js — the bank ribbon's tick geometry
 * (SNOW-1021).
 *
 * The bank angle is how far the ground tilts ACROSS a track: a traverse of
 * a 40° face banks at 40°, a descent straight down it at 0°. The server
 * derives it per segment (apps/routes/services/bank.py) and sends it as
 * `properties.slope.banks`, a flat list aligned with `angles`, one whole
 * SIGNED degree each — positive where the ground falls away on the
 * skier's right — and null for an unknown segment.
 *
 * The ribbon draws it as a row of short ticks, one every `pitch` pixels,
 * each leaning from vertical by the roll: the top of the tick leans
 * toward the downhill shoulder, so a positive roll leans right. A
 * switchback sequence reads as ticks alternating left and right, which is
 * the pattern the sign exists to show — see
 * docs/decisions/the-bank-angle-is-drawn-signed.md for why the sign is
 * drawn at all.
 *
 * THE TICK COUNT FOLLOWS THE PITCH, NOT THE SEGMENT COUNT. A zoomed leg
 * of twenty segments across 600 px and a whole 600-segment tour across
 * the same width both get 75 ticks at an 8 px pitch; each tick reads the
 * segment under its own x.
 *
 * THE X → INDEX CONVERSION IS THE CALLER'S. The rule route_cursor_core.js
 * set: each surface owns its geometry and converts it to a sample index
 * in its own code. `indexAt(x)` is that conversion; this module never
 * learns how the rail lays out its axis.
 *
 * A null bank draws NO tick — a gap, never a vertical tick, which would
 * claim the ground is flat across the track where nothing is known.
 *
 * This module owns no DOM, no strings and no colours; the mount (SNOW-1019)
 * owns the tokens, including how `strong` ticks are painted.
 *
 * Exports (frozen `self.pwaBankRibbonCore`):
 *
 *   bankTicks({banks, width, indexAt, pitch, halfLength, strongDeg, y})
 *     — one tick per `pitch` px across `width`
 */

// @ts-check

(function () {
  'use strict';

  /**
   * @typedef {{
   *   x: number,
   *   index: number,
   *   roll: number,
   *   x1: number,
   *   y1: number,
   *   x2: number,
   *   y2: number,
   *   strong: boolean,
   * }} BankTick
   *   One tick: its centre `x`, the sample `index` it reads, the signed
   *   `roll` in degrees, its bottom (`x1`, `y1`) and top (`x2`, `y2`) in
   *   the caller's pixel space (y down, as SVG and canvas are), and
   *   whether the roll reaches `strongDeg`.
   */

  /**
   * @typedef {{
   *   banks: Array<?number>,
   *   width: number,
   *   indexAt: function(number): number,
   *   pitch?: number,
   *   halfLength?: number,
   *   strongDeg?: number,
   *   y?: number,
   * }} BankTickOptions
   */

  /**
   * Lay out the ribbon's ticks across a width.
   *
   * Ticks sit at the centre of each `pitch`-wide slot, so the first is
   * half a pitch in from the left edge and the last half a pitch or more
   * in from the right.
   *
   * @param {BankTickOptions} options
   *   `banks` — the wire's signed whole degrees, null where unknown.
   *   `width` — the drawing's width in px.
   *   `indexAt` — the caller's x → sample-index conversion. A fractional
   *     answer is rounded; one outside `banks` draws no tick.
   *   `pitch` — px between ticks (default 8).
   *   `halfLength` — half a tick's length in px (default 9).
   *   `strongDeg` — `|roll|` at and above which a tick is `strong`
   *     (default 25).
   *   `y` — the ticks' vertical centre (default 0).
   * @returns {Array<BankTick>} The ticks, left to right.
   */
  function bankTicks(options) {
    const banks = options.banks;
    const width = options.width;
    const indexAt = options.indexAt;
    const pitch = options.pitch === undefined ? 8 : options.pitch;
    const halfLength = options.halfLength === undefined ? 9 : options.halfLength;
    const strongDeg = options.strongDeg === undefined ? 25 : options.strongDeg;
    const y = options.y === undefined ? 0 : options.y;

    if (!Array.isArray(banks)) throw new TypeError('banks must be an array');
    if (typeof indexAt !== 'function') throw new TypeError('indexAt must be a function');
    if (!Number.isFinite(width) || width < 0) throw new RangeError('width must be >= 0');
    if (!Number.isFinite(pitch) || pitch <= 0) throw new RangeError('pitch must be > 0');

    /** @type {Array<BankTick>} */
    const ticks = [];
    const count = Math.floor(width / pitch);
    for (let slot = 0; slot < count; slot += 1) {
      const x = (slot + 0.5) * pitch;
      const index = Math.round(indexAt(x));
      if (!Number.isInteger(index) || index < 0 || index >= banks.length) continue;
      const roll = banks[index];
      // A gap, not a vertical tick: null is "not known", never "flat".
      if (typeof roll !== 'number' || !Number.isFinite(roll)) continue;
      const radians = (roll * Math.PI) / 180;
      const dx = halfLength * Math.sin(radians);
      const dy = halfLength * Math.cos(radians);
      ticks.push(
        Object.freeze({
          x: x,
          index: index,
          roll: roll,
          // The top leans toward the downhill shoulder: positive → right.
          x1: x - dx,
          y1: y + dy,
          x2: x + dx,
          y2: y - dy,
          strong: Math.abs(roll) >= strongDeg,
        }),
      );
    }
    return ticks;
  }

  self.pwaBankRibbonCore = Object.freeze({
    bankTicks: bankTicks,
  });
})();
