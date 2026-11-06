// @ts-check
/*
 * static/js/map_compass.js — the map's compass (SNOW-1449).
 *
 * The roundel directly under the zoom roundel at the top of the bottom-left
 * stack (public/partials/_map_compass.html), for every reader and in both
 * views, flat and 3-D. It is shown whenever the map is not north-up and
 * hidden when it is: a map turned by a two-finger twist or a right-drag
 * otherwise gives no sign that it has turned, nor a way back that does not
 * take the same gesture.
 *
 * The needle points north, so it turns against the map's bearing, on every
 * `rotate`. A press eases the map back to north and leaves the tilt alone,
 * so a 3-D view stays tilted.
 *
 * LOAD ORDER: runs at parse time and reads the map from
 * window.snowdeskMapState, so it loads after map.js; outside the map
 * bundle's contiguous run (nothing in the bundle reads it), after
 * map_zoom_control.js, whose roundel it sits under.
 */
(function mapCompassInit() {
  'use strict';

  const button = document.getElementById('map-compass');
  const state = window.snowdeskMapState;
  const map = state ? state.map : null;
  if (!button || !map) return;
  const needle = /** @type {?SVGElement} */ (button.querySelector('[data-compass-needle]'));

  /** Closer to north than this, in degrees, counts as north-up. */
  const NORTH_WITHIN_DEG = 0.5;
  const EASE_MS = 400;

  /** Show the compass while the map is turned, its needle at north. */
  const render = () => {
    const bearing = map.getBearing();
    button.hidden = Math.abs(bearing) < NORTH_WITHIN_DEG;
    if (needle) needle.style.transform = `rotate(${-bearing}deg)`;
  };

  map.on('rotate', render);
  render();

  button.addEventListener('click', () => {
    map.easeTo({ bearing: 0, duration: EASE_MS });
  });
})();
