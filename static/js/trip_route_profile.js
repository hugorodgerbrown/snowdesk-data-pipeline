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
 * No point is placed: the Plan page is for choosing, not for reading
 * terrain, so the profile here is the drawing alone.
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
  }

  /** Draw every profile on the page. */
  function init() {
    if (!self.pwaRouteProfile || !self.pwaRouteRailCore || !self.pwaElevationProfileCore) return;
    var strings = self.pwaStrings.read('route-profile-strings-template', PROFILE_STRINGS);
    document.querySelectorAll('[data-trip-route-profile]').forEach(function (root) {
      drawOne(/** @type {HTMLElement} */ (root), strings);
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
