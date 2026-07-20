/*
 * static/js/place_picker.js — SNOW-475 touch-friendly place-picker.
 *
 * Replaces the draggable-marker pattern favourites.js and report.js each
 * used independently (a maplibregl.Marker({draggable: true}) whose dragend
 * wrote the chosen coord) — on touch screens the dragged finger occludes
 * the pin it is supposed to be placing, making fine positioning unreliable.
 *
 * Instead, a single pin (#map-place-pin, added in
 * public/partials/_map_embed.html) is fixed at the exact centre of the
 * MapLibre viewport via CSS (static/css/map.css). The user pans the map
 * underneath it; the chosen coordinate is read live from MAP.getCenter()
 * on every 'moveend', so the pin itself never moves relative to the
 * screen — only the map does.
 *
 * Exposes window.PlacePicker with three methods:
 *
 *   activate({ onChange, recenterTo })
 *     Reveals the pin and starts tracking the map centre. onChange(lat, lon)
 *     fires once immediately (the centre is always a valid coordinate — this
 *     is what lets callers unblock a "confirm" affordance right away, with
 *     no separate "tap to place" step) and again on every subsequent
 *     'moveend'. recenterTo (optional [lon, lat]) re-centres the map before
 *     the first read — used by report.js's GPS-refine flow so the pin
 *     starts on the GPS fix rather than wherever the map already was.
 *
 *   deactivate()
 *     Hides the pin and stops tracking. Idempotent — safe to call whether
 *     or not activate() was ever called.
 *
 *   isActive()
 *     True between a call to activate() and the matching deactivate().
 *
 * Loaded before favourites.js/report.js on every page that includes either
 * surface (see _favourites_surface.html / _report_surface.html) — both
 * script tags are `defer`, so document-order execution guarantees
 * window.PlacePicker exists by the time those modules run.
 */

(function () {
  'use strict';

  /** Module-level 'moveend' handler, so deactivate() can remove exactly
   * the listener activate() registered.
   * @type {(() => void)|null}
   */
  let moveEndHandler = null;

  /** Whether the picker is currently armed. @type {boolean} */
  let active = false;

  /** Return the global MAP object if available, else null. Guards against
   * the case where map.js has not yet initialised.
   * @returns {maplibregl.Map|null}
   */
  function getMap() {
    return typeof MAP !== 'undefined' ? MAP : null; // eslint-disable-line no-undef
  }

  /** Reveal the fixed centre-pin element, if present in the DOM. */
  function showPin() {
    const pin = document.getElementById('map-place-pin');
    if (pin) pin.removeAttribute('hidden');
  }

  /** Hide the fixed centre-pin element, if present in the DOM. */
  function hidePin() {
    const pin = document.getElementById('map-place-pin');
    if (pin) pin.setAttribute('hidden', '');
  }

  window.PlacePicker = {
    /**
     * Arm the place-picker: show the centre pin and start reporting the map
     * centre on every pan.
     * @param {object} options
     * @param {(lat: number, lon: number) => void} options.onChange — called
     *   immediately with the current centre, then again on every 'moveend'.
     * @param {[number, number]} [options.recenterTo] — optional [lon, lat]
     *   to pan the map to before the first onChange read.
     */
    activate: function activate(options) {
      const map = getMap();
      if (!map) return;

      const onChange = options && options.onChange;
      const recenterTo = options && options.recenterTo;

      // Guard against a stray previous activation (mirrors the
      // disarmPlacement-before-arm pattern the old marker code used).
      this.deactivate();

      if (recenterTo) {
        map.setCenter(recenterTo);
      }

      showPin();
      active = true;

      const readCentre = function () {
        if (!onChange) return;
        const centre = map.getCenter();
        onChange(centre.lat, centre.lng);
      };

      readCentre();

      moveEndHandler = readCentre;
      map.on('moveend', moveEndHandler);
    },

    /** Disarm the place-picker: hide the pin and stop tracking the map
     * centre. Idempotent.
     */
    deactivate: function deactivate() {
      const map = getMap();
      if (map && moveEndHandler) {
        map.off('moveend', moveEndHandler);
      }
      moveEndHandler = null;
      active = false;
      hidePin();
    },

    /**
     * @returns {boolean} True while the picker is armed.
     */
    isActive: function isActive() {
      return active;
    },
  };
}());
