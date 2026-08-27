/*
 * static/js/map_viewport_core.js — the map camera the visitor left behind,
 * and the validation that decides whether it is still usable.
 *
 * SNOW-737. Every other piece of map chrome already survives a cold boot —
 * the basemap under `snowdesk.map.basemap`, every overlay under the
 * `snowdesk.map.overlay.*` table — so a returning visitor got their layers
 * and their basemap back and then had to pan and zoom to their own mountains
 * again. The camera was the one thing thrown away, because `map.js` handed
 * the MapLibre constructor a hardcoded frame around Switzerland.
 *
 * WHY THE VALIDATION IS THE INTERESTING HALF. A stored camera is not data
 * this codebase produced this week: it was written by whatever build the
 * visitor last ran, and the camera limits are constructor options that have
 * been retuned before — SNOW-442 moved `maxZoom` from 12 to 18, and
 * `maxBounds` east went from 17° to 23° when the Austrian and Slovenian arc
 * was brought into frame. So a viewport written months ago can name a zoom
 * or a centre the current map will not accept.
 *
 * MapLibre would absorb most of that silently: `maxBounds` clamps the centre
 * without complaint, and an out-of-range zoom is pinned to the nearest limit.
 * Silent absorption is exactly the failure worth avoiding — it turns "your
 * stored viewport is stale" into "the map opened somewhere you have never
 * been", with nothing anywhere to say why. `restore` therefore rejects
 * outright and returns null, and the caller falls back to the default frame,
 * which is a place the visitor at least recognises.
 *
 * The five numbers are stored as ONE JSON blob rather than five keys because
 * they are only meaningful together. A half-written camera — a longitude from
 * this session and a zoom from the last — is worse than no camera at all, and
 * five independent keys make that state representable.
 *
 * Exports (frozen `self.pwaViewportCore`):
 *
 *   serialise(camera)      — {lng, lat, zoom, bearing, pitch} → string
 *   restore(raw, limits)   — string → a MapLibre camera, or null
 */

(function () {
  'use strict';

  /**
   * Serialise a camera for storage.
   *
   * Takes the five numbers rather than a MapLibre `Map`, so that both this
   * function and its test are free of the library: the caller in `map.js`
   * does the `getCenter()` / `getZoom()` reading, which is the part there is
   * nothing to get wrong about.
   *
   * @param {{lng: number, lat: number, zoom: number, bearing: number,
   *          pitch: number}} camera - the current view.
   * @returns {string} A JSON string suitable for localStorage.
   */
  function serialise(camera) {
    return JSON.stringify({
      lng: camera.lng,
      lat: camera.lat,
      zoom: camera.zoom,
      bearing: camera.bearing,
      pitch: camera.pitch,
    });
  }

  /**
   * Whether a value is a number that can be used as a coordinate.
   *
   * `typeof NaN` is 'number' and `JSON.parse` will hand back null, strings
   * and objects for any of the five fields, so both halves are load-bearing.
   *
   * @param {*} value - the parsed field.
   * @returns {boolean} True when it is a finite number.
   */
  function isFiniteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  /**
   * Parse and validate a stored camera against the map's current limits.
   *
   * Returns null — never a partially-trusted camera — whenever anything is
   * wrong: absent, malformed, missing a field, non-finite, or naming a zoom
   * or centre outside what this build of the map allows. The caller treats
   * null as "no stored viewport" and uses the default frame.
   *
   * `bearing` and `pitch` are validated for finiteness but not for range:
   * MapLibre normalises bearing modulo 360 and clamps pitch to its own
   * `maxPitch`, and neither can put the visitor somewhere unrecognisable the
   * way a bad centre can. Rejecting a whole camera over a pitch of 61° would
   * throw away a good position for a field that cannot mislead.
   *
   * @param {?string} raw - the stored string, or null when the key is absent.
   * @param {{minZoom: number, maxZoom: number,
   *          maxBounds: number[][]}} limits - the map's constructor limits,
   *     `maxBounds` as MapLibre's [[west, south], [east, north]].
   * @returns {?{center: number[], zoom: number, bearing: number,
   *             pitch: number}} A camera, or null.
   */
  function restore(raw, limits) {
    if (!raw) return null;

    let stored;
    try {
      stored = JSON.parse(raw);
    } catch (_) {
      return null;
    }
    // A JSON document can legitimately be a number, a string or null, none of
    // which have the fields below — reading `.lng` off them would throw for
    // null and yield undefined for the rest.
    if (!stored || typeof stored !== 'object') return null;

    const { lng, lat, zoom, bearing, pitch } = stored;
    if (![lng, lat, zoom, bearing, pitch].every(isFiniteNumber)) return null;

    if (zoom < limits.minZoom || zoom > limits.maxZoom) return null;

    const [[west, south], [east, north]] = limits.maxBounds;
    if (lng < west || lng > east || lat < south || lat > north) return null;

    return { center: [lng, lat], zoom: zoom, bearing: bearing, pitch: pitch };
  }

  self.pwaViewportCore = Object.freeze({
    serialise: serialise,
    restore: restore,
  });
})();
