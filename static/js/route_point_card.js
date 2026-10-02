/*
 * static/js/route_point_card.js — the point header's DOM half: fills
 * templates/includes/_route_point_card.html for the open route's cursor
 * (SNOW-1064; a header of the route panel since SNOW-1068).
 *
 * The rail attaches the header when a route opens (route_rail.js's `open`)
 * and detaches it when the route closes. While attached it follows the
 * route cursor (route_cursor_core.js): with no index it is hidden and the
 * panel shows the route or the open leg, and with one it shows that
 * segment's wheel and words, while the rail hides its own title and meta
 * line in its place (route_rail.js's `paintState`). Every surface that
 * places a point already writes the cursor: a tap on the open route's
 * line and a mouse over it (map.js), and a drag or hover along the
 * profile (route_rail.js). Nothing here is placement.
 *
 * The per-route arrays — each segment's path and gradient — are worked out
 * once on attach, so a cursor move is one lookup and one redraw. All the
 * arithmetic and the words are route_point_card_core.js's; all the copy is
 * the partial's strings template.
 *
 * Clearing the point is the panel's × and Escape (route_rail.js).
 *
 * Depends on i18n_strings.js, route_slope_core.js, aspect_wheel_core.js
 * and route_point_card_core.js, loaded before it.
 *
 * Publishes (frozen `window.pwaRoutePointCard`):
 *
 *   attach({cursor, slope, coordinates, profile, legs, sampleCount, spanM})
 *              — follow one route's cursor, showing the header for a point
 *   detach()   — stop following and hide the header
 *   element    — the header itself
 */

(function routePointCardInit() {
  'use strict';

  var card = document.getElementById('route-point-card');
  if (!card) return;

  /** The wheel's size in the header, CSS px: the mockup's. */
  var WHEEL_SIZE = 48;

  // Server-translated copy; the literals are the English fallback (see
  // static/js/i18n_strings.js).
  var STRINGS = self.pwaStrings.read('route-point-card-strings-template', {
    label: '%(headline)s; %(ground)s',
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
    'ground-falling-left': "%(ground)s, falling skier's left",
    'ground-falling-right': "%(ground)s, falling skier's right",
  });

  var wheelEl = card.querySelector('[data-route-point-card-wheel]');
  var headlineEl = card.querySelector('[data-route-point-card-headline]');
  var groundEl = card.querySelector('[data-route-point-card-ground]');

  /** The open route's cursor, or null while detached. */
  var cursor = null;
  /** Removes the header's subscription to `cursor`. */
  var unsubscribe = null;
  /** The open route's arrays, worked out once on attach. */
  var route = null;

  /**
   * Draw the wheel into the header.
   *
   * @param {*} state The wheel's state.
   * @param {string} label Its accessible name.
   */
  function drawWheel(state, label) {
    var wheel = self.pwaAspectWheelCore;
    if (!wheel || !wheelEl) return;
    wheelEl.innerHTML = wheel.aspectWheelSvg({ size: WHEEL_SIZE, state: state, label: label });
  }

  /** Hide the header and empty it, for the panel's route or leg header. */
  function clear() {
    card.hidden = true;
    if (wheelEl) wheelEl.innerHTML = '';
    headlineEl.textContent = '';
    groundEl.textContent = '';
  }

  /**
   * Bring the header in line with the cursor: shown and filled for a
   * point, hidden without one.
   *
   * @param {?{index: ?number}} state The cursor's state.
   */
  function paint(state) {
    var core = self.pwaRoutePointCardCore;
    var index = state ? state.index : null;
    if (!core || !route || typeof index !== 'number') {
      clear();
      return;
    }
    var words = core.reading({
      index: index,
      paths: route.paths,
      gradients: route.gradients,
      angles: route.angles,
      aspects: route.aspects,
    }, STRINGS);
    if (words.state) drawWheel(words.state, words.label);
    headlineEl.textContent = words.headline;
    groundEl.textContent = words.ground;
    card.hidden = false;
  }

  /**
   * Follow one route's cursor.
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
  }

  /** Stop following the cursor and hide the header. */
  function detach() {
    if (unsubscribe) unsubscribe();
    unsubscribe = null;
    cursor = null;
    route = null;
    clear();
  }

  clear();

  window.pwaRoutePointCard = Object.freeze({
    attach: attach,
    detach: detach,
    element: card,
  });
}());
