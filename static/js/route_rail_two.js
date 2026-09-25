/*
 * static/js/route_rail_two.js — rail two's DOM half: one leg, zoomed, with
 * the ground under it (SNOW-1019). Fills `[data-route-rail-two]` in
 * templates/includes/_route_rail.html.
 *
 * Rail one (route_rail.js) ATTACHES this to each route it opens, handing
 * over the route's cursor, slope record, profile and legs. From then on
 * rail two follows the CURSOR: it draws the leg when the cursor has an open
 * leg and falls back to its EMPTY state when it has none, so a leg opened
 * or closed from any surface opens or closes it here without anyone
 * telling it.
 *
 * THE EMPTY STATE (SNOW-1024). While attached with no open leg the row
 * still shows, marked `data-empty`: its TERRAIN eyebrow, the placeholder
 * "Select a route leg to view terrain" in place of a leg's title, and an
 * empty lane at its full 44 px. The zoom and close buttons are `hidden`,
 * not disabled — there is nothing to zoom or close — and so is the
 * readout, so the empty row reserves no space for it. `detach()` hides the
 * row outright.
 *
 * WHAT IT DRAWS. Three rows on one x-axis (route_rail_two_core.js's module
 * comment has the axis): the strip of slope bands, the track line drawn as
 * the bank ribbon (bank_ribbon_core.js), and one bar per no-fall passage;
 * then the cursor line, the selection's outline, and edge fades where more
 * leg lies beyond the window. The leg's elevation profile was a fourth row
 * above the bands until SNOW-1019 removed it: at a 2 km window it drew
 * near-flat and added nothing rail one's highlighted leg does not show.
 * SNOW-1024 removed the distance ticks and their labels: rail one's
 * bracket already says where the window sits on the route.
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
 * PRESSES. A one-finger (or pen) press that moves past `DRAG_PX` SCRUBS:
 * the cursor follows it, which is what the idle hint "Drag to read a
 * point" promises. A touch drag used to pan, so the hint pointed at a
 * gesture that never read anything (SNOW-1024). Two fingers pan and zoom
 * together — the pinch keeps the sample under their midpoint beneath it,
 * so moving both fingers moves the window. A mouse drag still pans, since
 * a mouse already reads a point by hovering. A tap selects the band or
 * passage under it (`cursor.select`) — tapping the same one again clears
 * it — and moves the cursor there. A second pointer starts a pinch and
 * cancels the press in progress, so a pinch never scrubs or selects.
 *
 * THE READOUT sits under the lane, ANCHORED to what it reads
 * (SNOW-1024). Under the cursor it is two lines: a word for the terrain —
 * Flat, Gentle descent / ascent (by the leg's direction), Fall line,
 * Ground falls away left / right, Traverse · falls away left / right
 * (`trackAttitude` in route_rail_two_core.js, from the slope angle and the
 * signed bank) — then the figures, "37° slope · 36° bank". It names no
 * slope class and no distance there: the band under the cursor already
 * shows the class. A 1 px stem (`[data-route-rail-two-stem]`) carries the
 * cursor line on below the lane and stops just above the text. With a
 * band or passage selected it is one line under the selection box, the
 * length to the nearest 25 m then the class ("600 m under 30°"). Either
 * way it steps between left-aligned, centred and right-aligned by where
 * its anchor sits across the lane (`readoutAnchor`), never clamping
 * smoothly. With neither it offers a hint, left-aligned.
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
 *   detach()            — stop following it and hide the row
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
  /** The readout's alignment classes, by `readoutAnchor`'s `align`. */
  var ALIGN_CLASSES = Object.freeze({
    left: 'text-left',
    center: '-translate-x-1/2 text-center',
    right: '-translate-x-full text-right',
  });

  // Server-translated copy; the literals are the English fallback (see
  // static/js/i18n_strings.js).
  var STRINGS = self.pwaStrings.read('route-rail-two-strings-template', {
    'figure-distance': '%(km)s km',
    'figure-ascent': '▲ %(m)s m',
    'figure-descent': '▼ %(m)s m',
    'figure-range': '%(start)s → %(end)s m',
    'leg-climb': 'Leg %(i)s — climb',
    'leg-descent': 'Leg %(i)s — descent',
    'two-lane-label': 'Slope and bank along %(leg)s',
    'two-value': '%(km)s km along the route',
    'two-placeholder': 'Select a route leg to view terrain',
    'two-hint': 'Drag to read a point. Tap a band or passage to select it.',
    'class-slope-gentle': 'under 30°',
    'class-slope-30': '30–35°',
    'class-slope-35': '35–40°',
    'class-slope-40': '40–45°',
    'class-slope-45': '45–50°',
    'class-slope-50': 'over 50°',
    'class-unknown': 'slope not known',
    'attitude-flat': 'Flat',
    'attitude-gentle-descent': 'Gentle descent',
    'attitude-gentle-ascent': 'Gentle ascent',
    'attitude-fall-line': 'Fall line',
    'attitude-falls-away-left': 'Ground falls away left',
    'attitude-falls-away-right': 'Ground falls away right',
    'attitude-traverse-left': 'Traverse · falls away left',
    'attitude-traverse-right': 'Traverse · falls away right',
    'attitude-traverse': 'Traverse',
    'readout-slope': '%(angle)s° slope',
    'readout-slope-bank': '%(angle)s° slope · %(bank)s° bank',
    'readout-band': '%(length)s m %(class)s',
    'readout-passage': 'No-fall passage · %(length)s m',
  });
  var interpolate = self.pwaStrings.interpolate;

  var titleEl = row.querySelector('[data-route-rail-two-title]');
  var figuresEl = row.querySelector('[data-route-rail-two-figures]');
  var lane = row.querySelector('[data-route-rail-two-lane]');
  var readoutBoxEl = row.querySelector('[data-route-rail-two-readout-box]');
  var readoutEl = row.querySelector('[data-route-rail-two-readout]');
  var stemEl = row.querySelector('[data-route-rail-two-stem]');
  var zoomOutEl = row.querySelector('[data-route-rail-two-zoom="out"]');
  var zoomInEl = row.querySelector('[data-route-rail-two-zoom="in"]');
  var closeEl = row.querySelector('[data-route-rail-two-close]');

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

    var changed = row.hidden || row.hasAttribute('data-empty');
    setEmpty(false);
    row.hidden = false;
    if (changed && ctx.onResize) ctx.onResize();
  }

  /**
   * Mark the row empty or not: the placeholder title in the muted weight,
   * the zoom and close buttons and the readout hidden, and the lane taken
   * out of the tab order and the accessibility tree while it holds
   * nothing.
   *
   * @param {boolean} empty
   */
  function setEmpty(empty) {
    row.toggleAttribute('data-empty', empty);
    titleEl.classList.toggle('font-semibold', !empty);
    titleEl.classList.toggle('text-text-1', !empty);
    titleEl.classList.toggle('font-normal', empty);
    titleEl.classList.toggle('text-text-2', empty);
    zoomOutEl.hidden = empty;
    zoomInEl.hidden = empty;
    closeEl.hidden = empty;
    readoutBoxEl.hidden = empty;
    lane.setAttribute('tabindex', empty ? '-1' : '0');
    if (empty) {
      lane.setAttribute('aria-hidden', 'true');
    } else {
      lane.removeAttribute('aria-hidden');
    }
  }

  /** Forget the open leg and every press on it, and clear the drawing. */
  function forgetLeg() {
    leg = null;
    legLine = null;
    bands = [];
    passages = [];
    press = null;
    pinch = null;
    pointers.clear();
    lane.replaceChildren();
    readoutEl.replaceChildren();
    stemEl.hidden = true;
  }

  /**
   * Show the empty row: attached to a route, with no leg open. Rail one
   * hears `onView(null, null)` and drops its bracket.
   */
  function showEmpty() {
    forgetLeg();
    var changed = row.hidden || !row.hasAttribute('data-empty');
    titleEl.textContent = STRINGS['two-placeholder'];
    figuresEl.textContent = '';
    ['aria-label', 'aria-valuemin', 'aria-valuemax', 'aria-valuenow', 'aria-valuetext']
      .forEach(function (name) { lane.removeAttribute(name); });
    setEmpty(true);
    row.hidden = false;
    if (ctx && ctx.onView) ctx.onView(null, null);
    if (changed && ctx && ctx.onResize) ctx.onResize();
  }

  /** Hide rail two outright and forget the leg, on detach. */
  function hide() {
    forgetLeg();
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
   * Follow the cursor: draw the open leg or show the empty row, and
   * scroll what changed into view.
   *
   * @param {{index: ?number, openLeg: ?object, selection: ?object}} state
   */
  function onState(state) {
    var previous = lastState;
    lastState = state;
    var open = state.openLeg;
    if (!open) {
      if (leg || row.hidden || !row.hasAttribute('data-empty')) showEmpty();
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
   * Place the readout under its anchor, stepped by `readoutAnchor`.
   *
   * With no anchor — the idle hint — it spans the lane and wraps: the hint
   * is a sentence, wider than a phone's lane, and describes no one place.
   *
   * @param {?number} x The anchor's px across the lane, or null.
   */
  function placeReadout(x) {
    if (x === null) {
      readoutEl.className = 'absolute inset-x-0 top-1 text-left';
      readoutEl.style.left = '';
      return;
    }
    var anchor = core().readoutAnchor(x, width);
    readoutEl.className = 'absolute top-1 whitespace-nowrap ' + ALIGN_CLASSES[anchor.align];
    readoutEl.style.left = anchor.left.toFixed(2) + 'px';
  }

  /**
   * The terrain word for one segment, or null when it cannot be said.
   *
   * @param {?{term: string, side: ?string}} attitude A `trackAttitude`.
   * @returns {?string}
   */
  function attitudeLabel(attitude) {
    if (!attitude) return null;
    var sided = attitude.term === 'falls-away' || attitude.term === 'traverse';
    var key = 'attitude-' + attitude.term + (sided && attitude.side ? '-' + attitude.side : '');
    // 'falls-away' always has a side when the bank is known on ground of
    // 10° or more (trackAttitude's docstring); the fall line stands in.
    return STRINGS[key] || STRINGS['attitude-fall-line'];
  }

  /**
   * The readout: the selection's length and class, under the selection;
   * or the terrain and its figures, under the cursor; or a hint.
   * `aria-valuetext` carries the same lines.
   */
  function paintReadout() {
    var c = core();
    var state = ctx.cursor.state();
    var selected = inLeg(state.selection);
    var perSample = ctx.sampleCount > 0 ? ctx.spanM / ctx.sampleCount : 0;
    /** @type {Array<string>} */
    var lines = [];
    /** The anchor's px, or null to sit left at 0. */
    var anchorX = null;
    var stem = false;
    var cursorIn = state.index !== null && state.index + 1 > view.from && state.index < view.to;
    if (state.selection && selected) {
      var length = String(c.roundStretch(
        (state.selection.to - state.selection.from + 1) * perSample,
      ));
      if (state.selection.kind === 'passage') {
        lines.push(interpolate(STRINGS['readout-passage'], { length: length }));
      } else {
        lines.push(interpolate(STRINGS['readout-band'], {
          length: length,
          class: classLabel(classify(angles()[state.selection.from])),
        }));
      }
      var part = c.clip(selected, view);
      if (part) anchorX = (c.xOf(part.from, view, width) + c.xOf(part.to, view, width)) / 2;
    } else if (state.index !== null) {
      // What the ground is doing under the track, then its figures. No
      // class name and no distance: the band under the cursor shows the
      // class, and rail one's bracket where the window sits.
      var angle = angles()[state.index];
      var roll = banks()[state.index];
      if (typeof angle !== 'number' || !isFinite(angle)) {
        lines.push(classLabel(null));
      } else {
        var attitude = c.trackAttitude(angle, roll, !!leg.climbing);
        var word = attitudeLabel(attitude);
        if (word) lines.push(word);
        // Flat ground has no side to lean to, so its bank is not said (the
        // design review's "Flat / 3° slope").
        var bankKnown = typeof roll === 'number' && isFinite(roll)
          && !(attitude && attitude.term === 'flat');
        lines.push(interpolate(
          STRINGS[bankKnown ? 'readout-slope-bank' : 'readout-slope'],
          {
            angle: String(Math.round(angle)),
            bank: bankKnown ? String(Math.round(Math.abs(roll))) : '',
          },
        ));
      }
      if (cursorIn) {
        anchorX = c.xOf(state.index + 0.5, view, width);
        stem = true;
      }
    } else {
      lines.push(STRINGS['two-hint']);
    }
    readoutEl.replaceChildren.apply(readoutEl, lines.map(readoutLine));
    placeReadout(anchorX);
    stemEl.hidden = !stem;
    if (stem && anchorX !== null) stemEl.style.left = (anchorX - 0.5).toFixed(2) + 'px';

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
      press = {
        id: id,
        x0: x,
        from0: view.from,
        moved: false,
        target: event.target,
        // A mouse drag pans; a finger or a pen drag scrubs the cursor.
        scrubs: event.pointerType !== 'mouse',
      };
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
      if (press.moved && press.scrubs) {
        ctx.cursor.setIndex(clamp(c.indexAt(clamp(x, 0, width), view, width), leg.from, leg.to));
      } else if (press.moved) {
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
   *   after every draw, and null when rail two empties or hides;
   *   `onResize` hears rail two show, empty or hide, which changes the
   *   rail's height.
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

  /** Stop following the cursor and hide the row. */
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
