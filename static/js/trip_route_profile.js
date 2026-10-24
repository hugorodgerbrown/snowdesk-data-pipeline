/*
 * static/js/trip_route_profile.js — draws a route's profile on a trip page
 * (SNOW-1215).
 *
 * Finds every `[data-trip-route-profile]` and draws the route named by its
 * `data-payload` — the id of a `json_script` block whose `route` key is a
 * route Feature — with route_profile.js, the drawing the map's route
 * panel uses. The Plan page's route card is the first caller; it reads the
 * meeting picker's payload, which already carries the route.
 *
 * THE PLAN PAGE PLACES NO POINT: it is for choosing, not for reading
 * terrain, so its profile is the drawing alone.
 *
 * THE TRIP PAGE DOES (SNOW-1216). A profile marked `data-trip-route-scrub`
 * takes a press or a drag anywhere on the lane: the point follows the
 * pointer's x for as long as it is held, with any pointer, and stays where
 * it was let go. Each placement draws the profile's cursor line and
 * readout and is announced as a `trip:route-cursor` event on the document,
 * `{index}`, which trip_map.js draws on the line. Unlike the map's panel
 * (route_rail.js) there is no "a tap must be on the outline" rule and no
 * click to swallow: nothing else on this lane answers a press. The lane
 * is `touch-action: pan-y`, so a vertical swipe still scrolls the page.
 *
 * Publishes (frozen `window.pwaTripRouteProfile`), for tests:
 *
 *   bindScrub(view, lane) — wire one drawn profile's presses
 */

(function tripRouteProfileInit() {
  'use strict';

  // Server-translated copy; the literals are the English fallback (see
  // static/js/i18n_strings.js).
  var PROFILE_STRINGS = {
    'unit-m': '%(value)s m',
    'unit-km': '%(value)s km',
    'lane-label': 'Elevation profile of %(name)s',
  };

  /**
   * Draw one profile from its payload.
   *
   * @param {HTMLElement} root The `[data-trip-route-profile]` element.
   * @param {Object<string, string>} strings The profile's copy.
   */
  function drawOne(root, strings) {
    var source = document.getElementById(root.dataset.payload || '');
    if (!source) return;
    var payload;
    try {
      payload = JSON.parse(source.textContent || 'null');
    } catch (_err) {
      return;
    }
    var feature = payload && payload.route;
    if (!feature) return;
    var view = self.pwaRouteProfile.mount(root, strings);
    if (!view) return;
    var read = self.pwaRouteProfile.read(feature);
    view.draw(read.profile, read.legs, read.sampleCount, read.spanM, root.dataset.name || '');
    if (root.hasAttribute('data-trip-route-scrub')) bindScrub(view, view.lane);
  }

  /**
   * Place the point at a sample index, on the profile and (by the event)
   * on the map.
   *
   * @param {{cursor: function(?number): void}} view The drawn profile.
   * @param {?number} index The index, or null.
   */
  function place(view, index) {
    view.cursor(index);
    document.dispatchEvent(new CustomEvent('trip:route-cursor', { detail: { index: index } }));
  }

  /**
   * Wire a drawn profile's presses: a press places the point and a drag
   * moves it.
   *
   * @param {{cursor: function(?number): void, indexAt: function(number): ?number}} view
   * @param {Element} lane The view's lane.
   */
  function bindScrub(view, lane) {
    /** The pointer holding the point, or null. @type {?number} */
    var held = null;
    var last = null;

    /** @param {number} clientX */
    function follow(clientX) {
      var index = view.indexAt(clientX);
      if (index === null || index === last) return;
      last = index;
      place(view, index);
    }

    lane.addEventListener('pointerdown', function (event) {
      var press = /** @type {PointerEvent} */ (event);
      if (press.isPrimary === false || press.button !== 0) return;
      held = press.pointerId;
      if (lane.setPointerCapture) {
        try {
          lane.setPointerCapture(press.pointerId);
        } catch (_err) {
          // A synthetic or already-released pointer cannot be captured.
        }
      }
      follow(press.clientX);
    });
    lane.addEventListener('pointermove', function (event) {
      var move = /** @type {PointerEvent} */ (event);
      if (held === move.pointerId) follow(move.clientX);
    });
    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(function (type) {
      lane.addEventListener(type, function (event) {
        if (held === /** @type {PointerEvent} */ (event).pointerId) held = null;
      });
    });
    /** @type {HTMLElement} */ (lane).style.cursor = 'ew-resize';
  }

  /** Draw every profile on the page. */
  function init() {
    if (!self.pwaRouteProfile || !self.pwaRouteRailCore || !self.pwaElevationProfileCore) return;
    var strings = self.pwaStrings.read('route-profile-strings-template', PROFILE_STRINGS);
    document.querySelectorAll('[data-trip-route-profile]').forEach(function (root) {
      drawOne(/** @type {HTMLElement} */ (root), strings);
    });
  }

  self.pwaTripRouteProfile = Object.freeze({ bindScrub: bindScrub });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
