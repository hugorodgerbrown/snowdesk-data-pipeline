// @ts-check
/*
 * static/js/map_compass.js — the map's compass (SNOW-1449).
 *
 * The roundel directly under the zoom roundel at the top of the bottom-left
 * stack (public/partials/_map_compass.html), for every reader and in both
 * views, flat and 3-D. It is always shown, north-up or not, so the reader
 * always knows which way the map faces; a map turned by a two-finger twist
 * or a right-drag otherwise gives no sign that it has turned, nor a way back
 * that does not take the same gesture. (SNOW-1449 hid it at north; Hugo
 * asked for it always, 6 Nov 2026.)
 *
 * The needle points north, so it turns against the map's bearing, on every
 * `rotate`. A press eases the map back to north and leaves the tilt alone,
 * so a 3-D view stays tilted. At north a press is harmless: the ease has
 * nowhere to go.
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

  const EASE_MS = 400;

  /** Turn the needle against the map's bearing, so it points north. */
  const render = () => {
    if (needle) needle.style.transform = `rotate(${-map.getBearing()}deg)`;
  };

  map.on('rotate', render);
  render();

  button.addEventListener('click', () => {
    map.easeTo({ bearing: 0, duration: EASE_MS });
  });
})();
