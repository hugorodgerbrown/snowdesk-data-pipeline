/*
 * static/js/route_point_card.js — the point card's DOM half: fills
 * templates/includes/_route_point_card.html for the open route's cursor
 * (SNOW-1064).
 *
 * The rail attaches the card when a route opens (route_rail.js's `open`)
 * and detaches it when the route closes, so the card opens and closes with
 * the rail. While attached it follows the route cursor
 * (route_cursor_core.js): with no index it shows its empty state — the
 * wheel unlit, "Select a point on the route to view terrain data" — and
 * with one it shows that segment's wheel and words. Every surface that
 * places a point already writes the cursor: a tap on the open route's
 * line and a mouse over it (map.js), and a drag or hover along the
 * profile (route_rail.js). Nothing here is placement.
 *
 * The per-route arrays — each segment's path and gradient — are worked out
 * once on attach, so a cursor move is one lookup and one redraw. All the
 * arithmetic and the words are route_point_card_core.js's; all the copy is
 * the partial's strings template.
 *
 * The × clears the point and keeps the route.
 *
 * Depends on i18n_strings.js, route_slope_core.js, aspect_wheel_core.js
 * and route_point_card_core.js, loaded before it.
 *
 * Publishes (frozen `window.pwaRoutePointCard`):
 *
 *   attach({cursor, slope, coordinates, profile, legs, sampleCount, spanM})
 *              — show the card for one route and follow its cursor
 *   detach()   — stop following and hide the card
 *   element    — the card itself
 */

(function routePointCardInit() {
  'use strict';

  var card = document.getElementById('route-point-card');
  if (!card) return;

  /** The wheel's size on the card, CSS px: the mockup's. */
  var WHEEL_SIZE = 48;

  /** An unlit wheel: no heading, no gradient, ground that faces nowhere. */
  var EMPTY_STATE = Object.freeze({
    track: [],
    gradeDeg: null,
    prev: null,
    next: null,
    terrain: Object.freeze({ kind: 'flat' }),
  });

  // Server-translated copy; the literals are the English fallback (see
  // static/js/i18n_strings.js).
  var STRINGS = self.pwaStrings.read('route-point-card-strings-template', {
    empty: 'Select a point on the route to view terrain data',
    label: '%(headline)s; %(ground)s',
    'label-empty': 'No point selected',
    'steepness-gentle': 'Gentle',
    'steepness-moderate': 'Moderate',
    'steepness-steep': 'Steep',
    'steepness-very-steep': 'Very steep',
    'headline-fall-descent': '%(steepness)s fall line descent',
    'headline-fall-climb': '%(steepness)s fall line climb',
    'headline-rising-traverse': '%(steepness)s rising traverse',
    'headline-descending-traverse': '%(steepness)s descending traverse',
    'headline-level-traverse': 'Level traverse',
    'headline-climb-turning': '%(steepness)s climb, turning',
    'headline-descent-turning': '%(steepness)s descent, turning',
    'headline-climb': '%(steepness)s climb',
    'headline-descent': '%(steepness)s descent',
    'headline-level': 'Level track',
    'headline-no-height': 'No height data',
    'ground-flat': 'Flat ground',
    'ground-moderate': 'Moderate slope',
    'ground-steep': 'Steep slope',
    'ground-very-steep': 'Very steep slope',
    'ground-extremely-steep': 'Extremely steep slope',
    'ground-unknown': 'No terrain data',
  });

  var wheelEl = card.querySelector('[data-route-point-card-wheel]');
  var headlineEl = card.querySelector('[data-route-point-card-headline]');
  var groundEl = card.querySelector('[data-route-point-card-ground]');
  var clearEl = card.querySelector('[data-route-point-card-clear]');

  /** The open route's cursor, or null while detached. */
  var cursor = null;
  /** Removes the card's subscription to `cursor`. */
  var unsubscribe = null;
  /** The open route's arrays, worked out once on attach. */
  var route = null;

  /**
   * Draw the wheel into the card.
   *
   * @param {*} state The wheel's state.
   * @param {string} label Its accessible name.
   */
  function drawWheel(state, label) {
    var wheel = self.pwaAspectWheelCore;
    if (!wheel || !wheelEl) return;
    wheelEl.innerHTML = wheel.aspectWheelSvg({ size: WHEEL_SIZE, state: state, label: label });
  }

  /** Show the empty state: the wheel unlit and the prompt. */
  function paintEmpty() {
    card.setAttribute('data-empty', '');
    drawWheel(EMPTY_STATE, STRINGS['label-empty']);
    headlineEl.textContent = '';
    groundEl.textContent = STRINGS.empty;
    if (clearEl) clearEl.hidden = true;
  }

  /**
   * Bring the card in line with the cursor.
   *
   * @param {?{index: ?number}} state The cursor's state.
   */
  function paint(state) {
    var core = self.pwaRoutePointCardCore;
    var index = state ? state.index : null;
    if (!core || !route || typeof index !== 'number') {
      paintEmpty();
      return;
    }
    var words = core.reading({
      index: index,
      paths: route.paths,
      gradients: route.gradients,
      angles: route.angles,
      aspects: route.aspects,
    }, STRINGS);
    card.removeAttribute('data-empty');
    drawWheel(words.state || EMPTY_STATE, words.label);
    headlineEl.textContent = words.headline;
    groundEl.textContent = words.ground;
    if (clearEl) clearEl.hidden = false;
  }

  /**
   * Show the card for one route and follow its cursor.
   *
   * @param {{cursor: *, slope: *, coordinates: *, profile: *,
   *   legs: Array<{from: number, to: number}>, sampleCount: number,
   *   spanM: number}} options What the rail opened the route with.
   */
  function attach(options) {
    detach();
    if (!options || !options.cursor) return;
    var slopeCore = self.pwaRouteSlopeCore;
    var core = self.pwaRoutePointCardCore;
    var slope = options.slope && typeof options.slope === 'object' ? options.slope : null;
    route = {
      paths: slopeCore && slope ? slopeCore.segmentPaths(slope, options.coordinates) : [],
      gradients: core
        ? core.segmentGradients(options.profile, options.sampleCount, options.spanM, options.legs)
        : [],
      angles: slope && Array.isArray(slope.angles) ? slope.angles : [],
      // Undefined, not [], when the payload predates SNOW-976: the wheel
      // and the words tell "no aspect data" from "flat" by its absence.
      aspects: slope ? slope.aspects : undefined,
    };
    cursor = options.cursor;
    unsubscribe = cursor.subscribe(paint);
    paint(cursor.state());
    card.hidden = false;
  }

  /** Stop following the cursor and hide the card. */
  function detach() {
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    cursor = null;
    route = null;
    card.hidden = true;
    paintEmpty();
  }

  if (clearEl) {
    clearEl.addEventListener('click', function () {
      if (cursor) cursor.setIndex(null);
    });
  }

  paintEmpty();

  window.pwaRoutePointCard = Object.freeze({
    attach: attach,
    detach: detach,
    element: card,
  });
}());
