/*
 * static/js/aspect_wheel.js — draws every aspect wheel host on the page
 * (SNOW-1063).
 *
 * A host is `[data-aspect-wheel]` from templates/includes/_aspect_wheel.html:
 * it carries the wheel's state as JSON in `data-state` and its size in
 * `data-size`. This module reads the strings template back through
 * `window.pwaStrings`, draws the SVG with `self.pwaAspectWheelCore`, and
 * writes the one-line summary into the sibling `[data-aspect-wheel-line]`.
 *
 * It runs on DOMContentLoaded and again on `htmx:afterSettle`, because the
 * component library swaps its panels in. Nothing here is placement: the
 * surface that mounts the wheel on a route (SNOW-1063's follow-up) calls
 * the core directly with the cursor's state.
 *
 * Depends on route_slope_core.js, i18n_strings.js and aspect_wheel_core.js,
 * loaded before it.
 */

(function () {
  'use strict';

  /** The English fallbacks; the partial's template overrides them. */
  var FALLBACKS = {
    'compass-0': 'N',
    'compass-1': 'NE',
    'compass-2': 'E',
    'compass-3': 'SE',
    'compass-4': 'S',
    'compass-5': 'SW',
    'compass-6': 'W',
    'compass-7': 'NW',
    'heading-pair': '%(first)s then %(second)s',
    label: '%(track)s; %(terrain)s',
    'label-climbing': 'Heading %(heading)s, climbing %(grade)s°',
    'label-descending': 'Heading %(heading)s, descending %(grade)s°',
    'label-level': 'Heading %(heading)s, level',
    'label-heading': 'Heading %(heading)s',
    'label-no-heading': 'No heading',
    'label-faces': 'slope faces %(aspect)s, %(slope)s°',
    'label-flat': 'flat ground',
    'label-unknown': 'no terrain data',
    line: '%(track)s · %(terrain)s',
    'line-heading': 'Heading %(heading)s',
    'line-faces': 'slope faces %(aspect)s',
    'line-flat': 'flat ground',
    'line-unknown': 'no terrain data',
  };

  /**
   * Draw every host under `root`.
   *
   * @param {ParentNode} root Where to look.
   */
  function render(root) {
    var core = self.pwaAspectWheelCore;
    if (!core || !self.pwaStrings || !root || !root.querySelectorAll) return;
    var strings = self.pwaStrings.read('aspect-wheel-strings-template', FALLBACKS);
    root.querySelectorAll('[data-aspect-wheel]').forEach(function (host) {
      var state;
      try {
        state = JSON.parse(host.getAttribute('data-state') || '');
      } catch (_error) {
        return;
      }
      if (!state || typeof state !== 'object') return;
      var size = Number(host.getAttribute('data-size')) || 48;
      host.innerHTML = core.aspectWheelSvg({
        size: size,
        state: state,
        label: core.wheelLabel(state, strings),
      });
      var line = host.parentElement && host.parentElement.querySelector('[data-aspect-wheel-line]');
      if (line) line.textContent = core.headingLine(state, strings);
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    render(document);
  });
  document.addEventListener('htmx:afterSettle', function (event) {
    render(/** @type {ParentNode} */ (event.target));
  });
})();
