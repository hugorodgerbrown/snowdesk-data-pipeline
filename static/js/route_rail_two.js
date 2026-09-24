/*
 * static/js/route_rail_two.js — rail two's DOM half: one leg, zoomed, with
 * the ground under it (SNOW-1019). Fills `[data-route-rail-two]` in
 * templates/includes/_route_rail.html.
 *
 * Rail one (route_rail.js) ATTACHES this to each route it opens, handing
 * over the route's cursor, slope record, profile and legs. From then on
 * rail two follows the CURSOR: it shows when the cursor has an open leg and
 * hides when it has none, so a leg opened or closed from any surface opens
 * or closes it here without anyone telling it.
 *
 * WHAT IT DRAWS. Three rows on one x-axis (route_rail_two_core.js's module
 * comment has the axis): the strip of slope bands, the track line drawn as
 * the bank ribbon (bank_ribbon_core.js), and one bar per no-fall passage;
 * then the cursor line, the selection's outline, edge fades where more leg
 * lies beyond the window, and distance ticks, labelled in HTML under the
 * lane as on rail one. The leg's elevation profile was a fourth row above
 * the bands until SNOW-1019 removed it: at a 2 km window it drew near-flat
 * and added nothing rail one's highlighted leg does not show.
 *
 * REAL PIXELS. The svg's viewBox is the lane's measured width and never
 * stretched: the ribbon's tick lean IS the bank angle, and a lane squeezed
 * to 0.6× would draw a 45° bank at about 31°. Zoom and pan are therefore a
 * re-projection of every mark, not a transform, so strokes, ticks and bars
 * stay in screen units at any zoom. A `ResizeObserver` redraws on resize.
 *
 * THE WINDOW IS RAIL TWO'S OWN. The view and the span live here, never on
 * the cursor. Rail two opens on 2 km of ground (or the whole leg), pans by
 * drag, horizontal wheel or trackpad, and zooms by pinch, Ctrl/⌘-wheel,
 * the −/+ buttons and the −/+ keys. An index or a selection published from
 * elsewhere that lands outside the window CENTRES it there (a range wider
 * than the window aligns its start), so the cursor line and the leader
 * line ending on it sit mid-lane; rail two's own writes — its keys and
 * pointer — scroll the least distance instead, so stepping with the
 * arrows does not jump the view. After a pan or zoom the cursor index is
 * pulled into the window. Rail one draws a bracket over what the window shows from
 * `onView`, and presses inside the open leg call `centreOn`.
 *
 * PRESSES. A press that moves past `DRAG_PX` pans; a tap selects the band
 * or passage under it (`cursor.select`) — tapping the same one again
 * clears it — and moves the cursor there; a mouse hover moves the cursor.
 * A second pointer starts a pinch and cancels the press in progress, so a
 * pinch never pans, scrubs or selects.
 *
 * KEYS. The lane is one `role="slider"` tab stop — leg 4 of the seed tour
 * alone has 59 bands, which would be 59 tab stops. ←/→ move the cursor
 * (Shift: ten samples), Home/End go to the leg's ends, Enter/Space
 * selects the band under the cursor, −/+ zoom.
 *
 * Publishes (frozen `window.pwaRouteRailTwo`):
 *
 *   attach({cursor, slope, profile, legs, sampleCount, spanM, onView,
 *           onResize})  — follow one route's cursor
 *   detach()            — stop following it and hide
 *   centreOn(index)     — centre the window on a sample
 *   cursorPoint()       — the cursor at the band strip's top, viewport px
 *   view()              — the window `{from, to}`, or null when hidden
 */

(function routeRailTwoInit() {
  'use strict';

  var rail = document.getElementById('route-rail');
  var row = rail ? rail.querySelector('[data-route-rail-two]') : null;
  if (!row) return;

  var SVG_NS = 'http://www.w3.org/2000/svg';

  /** The lane's width when it has not been laid out (jsdom, a hidden rail). */
  var FALLBACK_WIDTH = 600;
  /** The ribbon's tick pitch at a wide view, in px. */
  var BASE_PITCH = 8;
  /** How far a press moves before it is a pan, in px. */
  var DRAG_PX = 4;
  /** Ctrl/⌘-wheel zoom rate: the span scales by exp(deltaY × this). */
  var WHEEL_ZOOM = 0.01;
  /** The width of the fade at an edge with more leg beyond it, in px. */
  var FADE_PX = 16;
  /** A distance label this close to an edge hangs inward, in px. */
  var LABEL_EDGE_PX = 24;

  // Server-translated copy; the literals are the English fallback (see
  // static/js/i18n_strings.js).
  var STRINGS = self.pwaStrings.read('route-rail-two-strings-template', {
    'unit-m': '%(value)s m',
    'unit-km': '%(value)s km',
    'figure-distance': '%(km)s km',
    'figure-ascent': '▲ %(m)sm',
    'figure-descent': '▼ %(m)sm',
    'figure-range': '%(start)s→%(end)sm',
    'leg-climb': 'Leg %(i)s — climb',
    'leg-descent': 'Leg %(i)s — descent',
    'two-lane-label': 'Slope and bank along %(leg)s',
    'two-value': '%(km)s km along the route',
    'two-hint': 'Press a band or a passage to select it.',
    'class-slope-gentle': 'under 30°',
    'class-slope-30': '30–35°',
    'class-slope-35': '35–40°',
    'class-slope-40': '40–45°',
    'class-slope-45': '45–50°',
    'class-slope-50': 'over 50°',
    'class-unknown': 'slope not known',
    'readout-angle': '%(angle)s° — %(class)s',
    'readout-bank-right': 'Banked %(deg)s° to the right',
    'readout-bank-left': 'Banked %(deg)s° to the left',
    'readout-bank-level': 'Level across the track',
    'readout-band': '%(length)s m at %(class)s',
    'readout-passage': 'No-fall passage — %(length)s m',
  });
  var interpolate = self.pwaStrings.interpolate;

  var titleEl = row.querySelector('[data-route-rail-two-title]');
  var figuresEl = row.querySelector('[data-route-rail-two-figures]');
  var lane = row.querySelector('[data-route-rail-two-lane]');
  var ticksEl = row.querySelector('[data-route-rail-two-ticks]');
  var readoutEl = row.querySelector('[data-route-rail-two-readout]');
  var zoomOutEl = row.querySelector('[data-route-rail-two-zoom="out"]');
  var zoomInEl = row.querySelector('[data-route-rail-two-zoom="in"]');

  /**
   * What rail one attached: the route's cursor and the data drawn from.
   *
   * @type {?{cursor: object, slope: ?object, profile: object, legs: Array,
   *   sampleCount: number, spanM: number, onView: ?function, onResize: ?function}}
   */
  var ctx = null;
  /** Removes the subscription to `ctx.cursor`. */
  var unsubscribe = null;
  /** The last cursor state seen, to tell what changed. */
  var lastState = null;

  /** The open leg, or null while rail two is hidden. */
  var leg = null;
  /** The open leg's profile on the sample axis (`legProfile`), which the
   *  identity cell's figures line is summed from. */
  var legLine = null;
  /** The open leg's slope-band runs. */
  var bands = [];
  /** The no-fall passages touching the open leg. */
  var passages = [];
  /** The window, continuous sample units, `to` exclusive. */
  var view = { from: 0, to: 1 };
  /** Samples across the lane: `view.to − view.from`. */
  var span = 1;
  /** The lane's width in px at the last draw. */
  var width = FALLBACK_WIDTH;

  /** Pointers down on the lane, by id → their lane x and client y. */
  var pointers = new Map();
  /** The press in progress, or null (none, or cancelled by a pinch). */
  var press = null;
  /** The pinch in progress, or null. */
  var pinch = null;
  /** The pending animation-frame redraw, or 0. */
  var frame = 0;
  /**
   * True while rail two itself writes to the cursor (its pointer, keys,
   * or the clamp after a pan), so `onState` scrolls the least distance
   * for its own writes and centres for everyone else's (`followView`).
   */
  var ownWrite = false;

  /**
   * Wrap a handler so every cursor write inside it counts as rail two's.
   *
   * @param {function(*): void} handler
   * @returns {function(*): void}
   */
  function asOwnWrite(handler) {
    return function (event) {
      var outer = ownWrite;
      ownWrite = true;
      try {
        handler(event);
      } finally {
        ownWrite = outer;
      }
    };
  }

  /** @returns {object} rail two's pure half. */
  function core() {
    return self.pwaRouteRailTwoCore;
  }

  /**
   * Create one SVG element with attributes.
   *
   * @param {string} tag
   * @param {Object<string, string>} attrs
   * @returns {SVGElement}
   */
  function svgEl(tag, attrs) {
    var el = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs).forEach(function (key) {
      el.setAttribute(key, attrs[key]);
    });
    return el;
  }

  /**
   * Clamp a number into a closed range.
   *
   * @param {number} value
   * @param {number} low
   * @param {number} high
   * @returns {number}
   */
  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  /** @returns {Array<?number>} The route's slope angles, or none. */
  function angles() {
    var slope = ctx && ctx.slope;
    return slope && Array.isArray(slope.angles) ? slope.angles : [];
  }

  /** @returns {Array<?number>} The route's signed bank angles, or none. */
  function banks() {
    var slope = ctx && ctx.slope;
    return slope && Array.isArray(slope.banks) ? slope.banks : [];
  }

  /**
   * The words for a slope class.
   *
   * @param {?number} classIndex An index into `pwaRouteSlopeCore.CLASSES`,
   *   or null for unknown.
   * @returns {string}
   */
  function classLabel(classIndex) {
    var classes = self.pwaRouteSlopeCore ? self.pwaRouteSlopeCore.CLASSES : [];
    if (classIndex === null || !classes[classIndex]) return STRINGS['class-unknown'];
    return STRINGS['class-' + classes[classIndex].id] || classes[classIndex].id;
  }

  /**
   * Classify one angle, or null when the slope core is absent.
   *
   * @param {?number} angle
   * @returns {?number}
   */
  function classify(angle) {
    return self.pwaRouteSlopeCore ? self.pwaRouteSlopeCore.classify(angle) : null;
  }

  /**
   * The label a leg is read out by, rail one's words.
   *
   * @param {{i: number, climbing: boolean}} openLeg
   * @returns {string}
   */
  function legLabel(openLeg) {
    return interpolate(STRINGS[openLeg.climbing ? 'leg-climb' : 'leg-descent'], {
      i: String(openLeg.i),
    });
  }

  /** @returns {number} The lane's width in px, measured now. */
  function measure() {
    var measured = lane.clientWidth || lane.getBoundingClientRect().width;
    return measured > 0 ? measured : FALLBACK_WIDTH;
  }

  /**
   * An event's x across the lane, in px.
   *
   * @param {MouseEvent} event
   * @returns {number}
   */
  function laneX(event) {
    return event.clientX - lane.getBoundingClientRect().left;
  }

  /**
   * An event's pointer id; a synthetic event without one counts as 1.
   *
   * @param {*} event
   * @returns {number}
   */
  function pointerIdOf(event) {
    return typeof event.pointerId === 'number' ? event.pointerId : 1;
  }

  /**
   * Replace the window.
   *
   * @param {{from: number, to: number}} next
   */
  function setView(next) {
    view = next;
    span = next.to - next.from;
  }

  /** Pull the cursor index into the window after a pan or a zoom. */
  function clampIndex() {
    if (!ctx || !leg) return;
    var index = ctx.cursor.state().index;
    if (index === null) return;
    var whole = core().fullyVisible(view);
    var next = clamp(index, whole[0], whole[1]);
    if (next !== index) asOwnWrite(function () { ctx.cursor.setIndex(next); })(null);
  }

  /** Redraw on the next animation frame, once however often it is asked. */
  function scheduleDraw() {
    if (typeof window.requestAnimationFrame !== 'function') {
      draw();
      return;
    }
    if (frame) return;
    frame = window.requestAnimationFrame(function () {
      frame = 0;
      draw();
    });
  }

  // ---- showing a leg ------------------------------------------------------

  /**
   * Open rail two on a leg: its profile, bands, passages and the window
   * it opens at.
   *
   * @param {{i: number, from: number, to: number, climbing: boolean}} openLeg
   */
  function showLeg(openLeg) {
    var c = core();
    var railCore = self.pwaRouteRailCore;
    leg = openLeg;
    legLine = c.legProfile(ctx.profile, leg, ctx.sampleCount, railCore.clipRun);
    bands = c.bandRuns(angles(), classify, leg);
    var slope = ctx.slope;
    passages = (slope && Array.isArray(slope.passages) ? slope.passages : []).filter(
      function (p) {
        return p && Number.isInteger(p.from) && Number.isInteger(p.to)
          && p.to >= leg.from && p.from <= leg.to;
      },
    );
    setView(c.placeView(leg, c.openingSpan(leg, ctx.sampleCount, ctx.spanM), leg.from));

    titleEl.textContent = legLabel(leg);
    figuresEl.textContent = railCore.formatFigures(
      c.legFigures(legLine, leg, ctx.sampleCount, ctx.spanM),
      STRINGS,
    );
    lane.setAttribute('aria-label', interpolate(STRINGS['two-lane-label'], { leg: legLabel(leg) }));
    lane.setAttribute('aria-valuemin', String(leg.from));
    lane.setAttribute('aria-valuemax', String(leg.to));

    var wasHidden = row.hidden;
    row.hidden = false;
    if (wasHidden && ctx.onResize) ctx.onResize();
  }

  /** Hide rail two and forget the leg. */
  function hide() {
    leg = null;
    legLine = null;
    bands = [];
    passages = [];
    press = null;
    pinch = null;
    pointers.clear();
    lane.replaceChildren();
    ticksEl.replaceChildren();
    readoutEl.textContent = '';
    var wasShown = !row.hidden;
    row.hidden = true;
    if (ctx && ctx.onView) ctx.onView(null, null);
    if (wasShown && ctx && ctx.onResize) ctx.onResize();
  }

  /**
   * The part of a selection inside the open leg, or null.
   *
   * @param {?{from: number, to: number}} selection
   * @returns {?{from: number, to: number}}
   */
  function inLeg(selection) {
    if (!selection || !leg) return null;
    var from = Math.max(selection.from, leg.from);
    var to = Math.min(selection.to, leg.to);
    return from <= to ? { from: from, to: to } : null;
  }

  /**
   * Follow the cursor: show or hide, and scroll what changed into view.
   *
   * @param {{index: ?number, openLeg: ?object, selection: ?object}} state
   */
  function onState(state) {
    var previous = lastState;
    lastState = state;
    var open = state.openLeg;
    if (!open) {
      if (leg || !row.hidden) hide();
      return;
    }
    var c = core();
    var fresh = !leg || leg.from !== open.from || leg.to !== open.to;
    if (fresh) showLeg(open);

    // Least distance for rail two's own writes; centred for a write from
    // the map or rail one, so its cursor is mid-lane rather than on the
    // edge (followView).
    var bring = ownWrite ? c.ensureVisible : c.followView;
    if (state.index !== null && (fresh || !previous || previous.index !== state.index)) {
      setView(bring(leg, view, state.index, state.index));
    }
    var selected = inLeg(state.selection);
    if (selected && (fresh || !previous || previous.selection !== state.selection)) {
      setView(bring(leg, view, selected.from, selected.to));
    }
    draw();
  }

  // ---- drawing ------------------------------------------------------------

  /** The two edge-fade gradients, in the card's own colour. */
  function drawDefs() {
    var defs = svgEl('defs', {});
    [['left', '1', '0'], ['right', '0', '1']].forEach(function (spec) {
      var gradient = svgEl('linearGradient', {
        id: 'route-rail-two-fade-' + spec[0],
        x1: '0',
        x2: '1',
        y1: '0',
        y2: '0',
      });
      gradient.appendChild(svgEl('stop', {
        offset: '0',
        'stop-color': 'var(--color-card)',
        'stop-opacity': spec[1],
      }));
      gradient.appendChild(svgEl('stop', {
        offset: '1',
        'stop-color': 'var(--color-card)',
        'stop-opacity': spec[2],
      }));
      defs.appendChild(gradient);
    });
    lane.appendChild(defs);
  }

  /**
   * Whether a range is the one the cursor holds selected.
   *
   * @param {string} kind
   * @param {{from: number, to: number}} range
   * @returns {boolean}
   */
  function isSelected(kind, range) {
    var held = ctx.cursor.state().selection;
    return !!held && held.kind === kind && held.from === range.from && held.to === range.to;
  }

  /** The slope-band strip, one rect per run, unknown runs dashed. */
  function drawBands() {
    var c = core();
    var rows = c.ROWS;
    var classes = self.pwaRouteSlopeCore ? self.pwaRouteSlopeCore.CLASSES : [];
    bands.forEach(function (band) {
      var part = c.clip(band, view);
      if (!part) return;
      var x = c.xOf(part.from, view, width);
      var unknown = band.classIndex === null;
      var token = unknown
        ? '--color-slope-unknown'
        : classes[/** @type {number} */ (band.classIndex)].token;
      var attrs = {
        x: x.toFixed(2),
        y: String(rows.bandTop),
        width: Math.max(0, c.xOf(part.to, view, width) - x).toFixed(2),
        height: String(rows.bandHeight),
        fill: 'var(' + token + ')',
        class: 'route-rail-two-band',
        'data-select-kind': 'band',
        'data-from': String(band.from),
        'data-to': String(band.to),
        'data-class': unknown ? 'unknown' : String(band.classIndex),
        'data-selected': isSelected('band', band) ? 'true' : 'false',
      };
      if (unknown) {
        attrs['fill-opacity'] = '0.35';
        attrs.stroke = 'var(--color-slope-unknown)';
        attrs['stroke-dasharray'] = '3 2';
      }
      lane.appendChild(svgEl('rect', attrs));
    });
  }

  /** The track line, drawn as the bank ribbon. */
  function drawRibbon() {
    var c = core();
    if (!self.pwaBankRibbonCore) return;
    c.ribbonTicks({
      bankTicks: self.pwaBankRibbonCore.bankTicks,
      banks: banks(),
      leg: leg,
      view: view,
      width: width,
      basePitch: BASE_PITCH,
      halfLength: c.ROWS.ribbonHalf,
      y: c.ROWS.ribbonY,
    }).forEach(function (tick) {
      lane.appendChild(svgEl('line', {
        x1: tick.x1.toFixed(2),
        y1: tick.y1.toFixed(2),
        x2: tick.x2.toFixed(2),
        y2: tick.y2.toFixed(2),
        // Muted by default and inked where the bank reaches `strongDeg`, so
        // the eye goes to the lean that matters rather than to a solid
        // hatch of equal ticks — bank_ribbon_core.js leaves this to the mount.
        stroke: tick.strong ? 'var(--color-text-1)' : 'var(--color-text-3)',
        'stroke-width': tick.strong ? '2' : '1.25',
        'stroke-linecap': 'round',
        class: 'route-rail-two-tick',
        'data-strong': tick.strong ? 'true' : 'false',
        'data-index': String(tick.index),
        'pointer-events': 'none',
      }));
    });
  }

  /** One bar per no-fall passage, under the ribbon. */
  function drawPassages() {
    var c = core();
    passages.forEach(function (passage) {
      var part = c.clip(inLeg(passage) || passage, view);
      if (!part) return;
      var x = c.xOf(part.from, view, width);
      lane.appendChild(svgEl('rect', {
        x: x.toFixed(2),
        y: String(c.ROWS.passageTop),
        width: Math.max(0, c.xOf(part.to, view, width) - x).toFixed(2),
        height: String(c.ROWS.passageHeight),
        rx: '1',
        fill: 'var(--color-text-1)',
        class: 'route-rail-two-passage',
        'data-select-kind': 'passage',
        'data-from': String(passage.from),
        'data-to': String(passage.to),
        'data-selected': isSelected('passage', passage) ? 'true' : 'false',
      }));
    });
  }

  /** The selection's outline, the cursor line, and the edge fades. */
  function drawMarks() {
    var c = core();
    var height = c.ROWS.height;
    var state = ctx.cursor.state();
    var selected = inLeg(state.selection);
    var part = selected ? c.clip(selected, view) : null;
    if (part) {
      var x = c.xOf(part.from, view, width);
      lane.appendChild(svgEl('rect', {
        x: x.toFixed(2),
        y: '1',
        width: Math.max(0, c.xOf(part.to, view, width) - x).toFixed(2),
        height: String(height - 2),
        rx: '2',
        fill: 'none',
        stroke: 'var(--color-text-1)',
        'stroke-width': '1.5',
        'pointer-events': 'none',
        'data-route-rail-two-selection': '',
      }));
    }
    if (state.index !== null && state.index + 1 > view.from && state.index < view.to) {
      var cx = c.xOf(state.index + 0.5, view, width).toFixed(2);
      lane.appendChild(svgEl('line', {
        x1: cx,
        x2: cx,
        y1: '0',
        y2: String(height),
        stroke: 'var(--color-text-1)',
        'stroke-opacity': '0.7',
        'pointer-events': 'none',
        'data-route-rail-two-cursor': '',
      }));
    }
    if (view.from > leg.from + 1e-6) {
      lane.appendChild(svgEl('rect', {
        x: '0',
        y: '0',
        width: String(FADE_PX),
        height: String(height),
        fill: 'url(#route-rail-two-fade-left)',
        'pointer-events': 'none',
        'data-route-rail-two-fade': 'left',
      }));
    }
    if (view.to < leg.to + 1 - 1e-6) {
      lane.appendChild(svgEl('rect', {
        x: String(width - FADE_PX),
        y: '0',
        width: String(FADE_PX),
        height: String(height),
        fill: 'url(#route-rail-two-fade-right)',
        'pointer-events': 'none',
        'data-route-rail-two-fade': 'right',
      }));
    }
  }

  /** Distance tick marks along the foot, and their labels under the lane. */
  function drawTicks() {
    var c = core();
    var height = c.ROWS.height;
    ticksEl.replaceChildren();
    c.distanceTicks(
      view,
      ctx.sampleCount,
      ctx.spanM,
      self.pwaRouteRailCore,
      { m: STRINGS['unit-m'], km: STRINGS['unit-km'] },
      width,
    ).forEach(function (tick) {
      var x = tick.x.toFixed(2);
      lane.appendChild(svgEl('line', {
        x1: x,
        x2: x,
        y1: String(height - (tick.major ? 6 : 3)),
        y2: String(height),
        stroke: 'currentColor',
        'stroke-opacity': tick.major ? '0.5' : '0.25',
        'pointer-events': 'none',
        class: 'text-text-3',
      }));
      if (!tick.label) return;
      var label = document.createElement('span');
      // A label near the left edge hangs right of its tick, and one near the
      // right edge hangs left, so neither runs off the lane; none wraps.
      var fraction = tick.x / width;
      var shift = tick.x < LABEL_EDGE_PX
        ? ''
        : tick.x > width - LABEL_EDGE_PX ? ' -translate-x-full' : ' -translate-x-1/2';
      label.className = 'absolute top-0 whitespace-nowrap' + shift;
      label.style.left = (fraction * 100).toFixed(3) + '%';
      label.textContent = tick.label;
      ticksEl.appendChild(label);
    });
  }

  /**
   * One line of the readout.
   *
   * @param {string} text
   * @returns {HTMLElement}
   */
  function readoutLine(text) {
    var line = document.createElement('span');
    line.className = 'block';
    line.textContent = text;
    return line;
  }

  /**
   * The readout: the selection's length and class, or the slope and bank
   * under the cursor, or a hint.
   */
  function paintReadout() {
    var state = ctx.cursor.state();
    var selected = inLeg(state.selection);
    var perSample = ctx.sampleCount > 0 ? ctx.spanM / ctx.sampleCount : 0;
    /** @type {Array<string>} */
    var lines = [];
    if (state.selection && selected) {
      var length = String(
        Math.round((state.selection.to - state.selection.from + 1) * perSample),
      );
      if (state.selection.kind === 'passage') {
        lines.push(interpolate(STRINGS['readout-passage'], { length: length }));
      } else {
        lines.push(interpolate(STRINGS['readout-band'], {
          length: length,
          class: classLabel(classify(angles()[state.selection.from])),
        }));
      }
    } else if (state.index !== null) {
      var angle = angles()[state.index];
      var known = typeof angle === 'number' && isFinite(angle);
      lines.push(known
        ? interpolate(STRINGS['readout-angle'], {
          angle: String(Math.round(angle)),
          class: classLabel(classify(angle)),
        })
        : classLabel(null));
      var bank = banks()[state.index];
      if (typeof bank === 'number' && isFinite(bank)) {
        var deg = String(Math.abs(Math.round(bank)));
        var key = bank > 0 ? 'readout-bank-right' : bank < 0 ? 'readout-bank-left' : 'readout-bank-level';
        lines.push(interpolate(STRINGS[key], { deg: deg }));
      }
    } else {
      lines.push(STRINGS['two-hint']);
    }
    readoutEl.replaceChildren.apply(readoutEl, lines.map(readoutLine));

    var index = state.index === null ? leg.from : state.index;
    lane.setAttribute('aria-valuenow', String(index));
    lane.setAttribute('aria-valuetext', [
      interpolate(STRINGS['two-value'], {
        km: (((index + 0.5) * perSample) / 1000).toFixed(2),
      }),
    ].concat(lines).join('. '));
  }

  /** Draw the whole lane for the current window. */
  function draw() {
    if (!ctx || !leg) return;
    var c = core();
    width = measure();
    lane.setAttribute('viewBox', '0 0 ' + width + ' ' + c.ROWS.height);
    lane.replaceChildren();
    drawDefs();
    drawBands();
    drawRibbon();
    drawPassages();
    drawMarks();
    drawTicks();
    paintReadout();

    var eps = 1e-6;
    zoomInEl.disabled = span <= c.minSpan(leg) + eps;
    zoomOutEl.disabled = span >= c.legLength(leg) - eps;
    if (ctx.onView) ctx.onView({ from: view.from, to: view.to }, leg);
    // The leader line (route_leader.js) follows this rail's cursor point,
    // which a pan or a zoom moves without the cursor changing.
    document.dispatchEvent(new CustomEvent('snowdesk:route-rail-two-drawn', { detail: null }));
  }

  /**
   * Where the cursor line meets the top of the band strip, in viewport px
   * (SNOW-1019) — the leader line's last stop.
   *
   * Null while rail two is hidden, with no index, or with the index
   * outside the window.
   *
   * @returns {?{x: number, y: number}}
   */
  function cursorPoint() {
    if (!ctx || !leg || row.hidden) return null;
    var index = ctx.cursor.state().index;
    if (index === null || index + 1 <= view.from || index >= view.to) return null;
    var c = core();
    var rect = lane.getBoundingClientRect();
    var scale = rect.height > 0 ? rect.height / c.ROWS.height : 1;
    return {
      x: rect.left + (c.xOf(index + 0.5, view, width) / width) * (rect.width || width),
      y: rect.top + c.ROWS.bandTop * scale,
    };
  }

  // ---- zoom and pan -------------------------------------------------------

  /**
   * Scale the span by `factor`, anchored on the cursor when it is in the
   * window and on the window's centre otherwise.
   *
   * @param {number} factor Below 1 zooms in, above 1 zooms out.
   */
  function zoomBy(factor) {
    if (!ctx || !leg) return;
    var index = ctx.cursor.state().index;
    var anchor = index !== null && index + 1 > view.from && index < view.to
      ? index + 0.5
      : (view.from + view.to) / 2;
    var fraction = (anchor - view.from) / span;
    setView(core().zoom(leg, span, span * factor, anchor, fraction).view);
    clampIndex();
    draw();
  }

  /**
   * Select a band or passage, or clear it when it is already selected.
   *
   * @param {string} kind
   * @param {number} from
   * @param {number} to
   */
  function toggleSelection(kind, from, to) {
    var cursor = ctx.cursor;
    if (isSelected(kind, { from: from, to: to })) {
      cursor.clearSelection();
    } else {
      cursor.select({ kind: kind, from: from, to: to });
    }
  }

  /**
   * A tap: select what was under it, and move the cursor there.
   *
   * @param {number} x The lane x the press went down at.
   * @param {?Element} target What it went down on.
   */
  function tap(x, target) {
    var hit = target && target.closest ? target.closest('[data-select-kind]') : null;
    ctx.cursor.setIndex(clamp(core().indexAt(x, view, width), leg.from, leg.to));
    if (hit) {
      toggleSelection(
        hit.getAttribute('data-select-kind'),
        Number(hit.getAttribute('data-from')),
        Number(hit.getAttribute('data-to')),
      );
    }
  }

  /** Start a pinch from the two pointers down. */
  function startPinch() {
    var points = Array.from(pointers.values());
    var a = points[0];
    var b = points[1];
    var mid = (a.x + b.x) / 2;
    pinch = {
      startDist: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
      startSpan: span,
      anchor: core().sampleAt(mid, view, width),
    };
  }

  lane.addEventListener('pointerdown', function (event) {
    if (!ctx || !leg) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    var id = pointerIdOf(event);
    var x = laneX(event);
    pointers.set(id, { x: x, y: event.clientY });
    if (pointers.size === 1) {
      press = { id: id, x0: x, from0: view.from, moved: false, target: event.target };
      if (lane.setPointerCapture) {
        try {
          lane.setPointerCapture(id);
        } catch (_err) {
          // A synthetic pointer has nothing to capture.
        }
      }
    } else if (pointers.size === 2) {
      // A second finger: this is a pinch, and the press that started it
      // must not end as a pan, a scrub or a selection.
      press = null;
      startPinch();
    }
  });

  lane.addEventListener('pointermove', asOwnWrite(function (event) {
    if (!ctx || !leg) return;
    var id = pointerIdOf(event);
    var x = laneX(event);
    if (pointers.has(id)) pointers.set(id, { x: x, y: event.clientY });
    var c = core();

    if (pinch && pointers.size >= 2) {
      var points = Array.from(pointers.values());
      var dist = Math.max(1, Math.hypot(points[0].x - points[1].x, points[0].y - points[1].y));
      var mid = (points[0].x + points[1].x) / 2;
      var next = pinch.startSpan * (pinch.startDist / dist);
      setView(c.zoom(leg, span, next, pinch.anchor, mid / width).view);
      scheduleDraw();
      return;
    }
    if (press && press.id === id) {
      var dx = x - press.x0;
      if (!press.moved && Math.abs(dx) > DRAG_PX) press.moved = true;
      if (press.moved) {
        setView(c.placeView(leg, span, press.from0 - (dx / width) * span));
        scheduleDraw();
      }
      return;
    }
    if (!press && !pinch && event.pointerType === 'mouse') {
      ctx.cursor.setIndex(clamp(c.indexAt(x, view, width), leg.from, leg.to));
    }
  }));

  /**
   * A pointer lifted or lost.
   *
   * @param {PointerEvent} event
   * @param {boolean} lifted True for a lift, false for a cancel.
   */
  function release(event, lifted) {
    if (!ctx || !leg) {
      pointers.clear();
      press = null;
      pinch = null;
      return;
    }
    var id = pointerIdOf(event);
    var ended = press && press.id === id ? press : null;
    pointers.delete(id);
    if (ended) press = null;
    if (pinch && pointers.size < 2) {
      pinch = null;
      clampIndex();
      draw();
    }
    if (!ended) return;
    if (ended.moved) {
      clampIndex();
      draw();
    } else if (lifted) {
      tap(ended.x0, ended.target);
    }
  }

  lane.addEventListener('pointerup', asOwnWrite(function (event) {
    release(/** @type {PointerEvent} */ (event), true);
  }));
  lane.addEventListener('pointercancel', function (event) {
    release(/** @type {PointerEvent} */ (event), false);
  });

  lane.addEventListener('wheel', function (event) {
    if (!ctx || !leg) return;
    var c = core();
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      var x = laneX(event);
      var anchor = c.sampleAt(x, view, width);
      setView(c.zoom(leg, span, span * Math.exp(event.deltaY * WHEEL_ZOOM), anchor, x / width).view);
      clampIndex();
      scheduleDraw();
      return;
    }
    // A horizontal wheel or trackpad swipe pans; Shift turns a vertical
    // wheel horizontal, as the platforms do for a scroll area.
    var dx = event.shiftKey && !event.deltaX ? event.deltaY : event.deltaX;
    if (Math.abs(dx) <= Math.abs(event.shiftKey ? 0 : event.deltaY)) return;
    event.preventDefault();
    setView(c.placeView(leg, span, view.from + (dx / width) * span));
    clampIndex();
    scheduleDraw();
  }, { passive: false });

  lane.addEventListener('keydown', asOwnWrite(function (event) {
    if (!ctx || !leg) return;
    var cursor = ctx.cursor;
    var index = cursor.state().index;
    var base = index === null ? Math.floor((view.from + view.to) / 2) : index;
    var step = event.shiftKey ? 10 : 1;
    switch (event.key) {
      case 'ArrowLeft':
        cursor.setIndex(clamp(base - step, leg.from, leg.to));
        break;
      case 'ArrowRight':
        cursor.setIndex(clamp(base + step, leg.from, leg.to));
        break;
      case 'Home':
        cursor.setIndex(leg.from);
        break;
      case 'End':
        cursor.setIndex(leg.to);
        break;
      case 'Enter':
      case ' ': {
        if (index === null) return;
        var band = bands.find(function (b) { return index >= b.from && index <= b.to; });
        if (!band) return;
        toggleSelection('band', band.from, band.to);
        break;
      }
      case '-':
      case '_':
        zoomBy(2);
        break;
      case '+':
      case '=':
        zoomBy(0.5);
        break;
      default:
        return;
    }
    event.preventDefault();
  }));

  row.addEventListener('click', function (event) {
    if (!ctx) return;
    var target = /** @type {Element} */ (event.target);
    if (!target || !target.closest) return;
    if (target.closest('[data-route-rail-two-close]')) {
      ctx.cursor.closeLeg();
      return;
    }
    var zoomEl = target.closest('[data-route-rail-two-zoom]');
    if (zoomEl && !zoomEl.disabled) {
      zoomBy(zoomEl.getAttribute('data-route-rail-two-zoom') === 'in' ? 0.5 : 2);
    }
  });

  if (typeof window.ResizeObserver === 'function') {
    new window.ResizeObserver(function () {
      if (leg && measure() !== width) scheduleDraw();
    }).observe(lane);
  } else {
    window.addEventListener('resize', function () {
      if (leg) scheduleDraw();
    });
  }

  // ---- the published surface --------------------------------------------

  /**
   * Follow one route's cursor.
   *
   * @param {{cursor: object, slope: ?object, profile: object, legs: Array,
   *   sampleCount: number, spanM: number,
   *   onView?: function(?{from: number, to: number}, ?object): void,
   *   onResize?: function(): void}} options `onView` hears the window
   *   after every draw, and null when rail two hides; `onResize` hears
   *   rail two show or hide, which changes the rail's height.
   */
  function attach(options) {
    detach();
    ctx = {
      cursor: options.cursor,
      slope: options.slope || null,
      profile: options.profile,
      legs: options.legs || [],
      sampleCount: options.sampleCount,
      spanM: options.spanM,
      onView: options.onView || null,
      onResize: options.onResize || null,
    };
    lastState = null;
    unsubscribe = ctx.cursor.subscribe(onState);
    onState(ctx.cursor.state());
  }

  /** Stop following the cursor and hide. */
  function detach() {
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    if (frame && typeof window.cancelAnimationFrame === 'function') {
      window.cancelAnimationFrame(frame);
    }
    frame = 0;
    if (ctx) hide();
    ctx = null;
    document.dispatchEvent(new CustomEvent('snowdesk:route-rail-two-drawn', { detail: null }));
    lastState = null;
  }

  /**
   * Centre the window on a sample, clamped to the open leg's ends.
   *
   * @param {number} index A sample index.
   */
  function centreOn(index) {
    if (!ctx || !leg || !Number.isFinite(index)) return;
    setView(core().placeView(leg, span, index + 0.5 - span / 2));
    clampIndex();
    draw();
  }

  window.pwaRouteRailTwo = Object.freeze({
    attach: attach,
    detach: detach,
    centreOn: centreOn,
    cursorPoint: cursorPoint,
    view: function () {
      return leg ? { from: view.from, to: view.to } : null;
    },
  });
}());
