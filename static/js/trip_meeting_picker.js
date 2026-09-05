/*
 * static/js/trip_meeting_picker.js — dropping the meeting pin (SNOW-820).
 *
 * The meeting point began life as two number inputs prefilled with the
 * route's first coordinate. That works only for someone who already knows
 * what 46.080012, 7.318197 means, which is nobody: a group agrees to meet
 * at the top car park, not at a decimal degree. This module makes the map
 * the control — the route is drawn, a marker sits on it, and you drag it
 * or click where you mean.
 *
 * ## The inputs are still the source of truth
 *
 * The marker WRITES to the form's own `latitude` / `longitude` fields and
 * never holds the value itself. That keeps three things true at once: the
 * form posts and validates exactly as it did before this module existed, a
 * visitor with no JavaScript still submits the prefilled route start, and
 * the manual-entry fields stay live for anyone who has a coordinate from
 * somewhere else and would rather type it. The two directions are kept in
 * step — dragging updates the fields, editing a field moves the marker.
 *
 * ## It rebuilds itself after an HTMX swap
 *
 * An invalid submission comes back as a fresh copy of the whole form, so
 * the container this map was built in is GONE and a new one has taken its
 * place. `init` is therefore idempotent in the only way that works here:
 * it tears the previous map down and builds on whatever container is in
 * the document now. Re-binding to the old element would leave a live
 * MapLibre canvas attached to a detached node, drawing nothing and holding
 * a WebGL context open.
 *
 * ## Exports (frozen `self.pwaTripMeetingPicker`)
 *
 *   readPayload(doc)            → the picker payload, or null
 *   roundCoordinate(value)      → 6 dp, or null when not finite
 *   clampLatitude(value)        → value bounded to [-90, 90], or null
 *   clampLongitude(value)       → value bounded to [-180, 180], or null
 *   formatCoordinate(lat, lon)  → the readout string, or ""
 *
 * Everything above is a pure function, unit-tested in
 * tests/js/test_trip_meeting_picker.js with no browser and no WebGL.
 */

(function () {
  'use strict';

  // Mirrors --color-route-line and --color-route-line-casing in
  // src/css/main.css, and the same two constants in trip_map.js. MapLibre
  // paint properties cannot reference a CSS @theme token at all, so these
  // are kept in step by hand — the idiom map.js documents at its own
  // ROUTE_LINE_COLOUR.
  var ROUTE_LINE_COLOUR = '#c026d3';
  var ROUTE_CASING_COLOUR = '#1a1916';

  // Six decimal places is ~11 cm at the equator — far finer than anyone
  // can point at on a phone, and short enough that the number in the
  // manual field stays readable. Storing the raw drag float would put
  // fifteen digits in a text input for no gain.
  var COORDINATE_DP = 6;

  var LATITUDE_MAX = 90;
  var LONGITUDE_MAX = 180;

  // The padding a fit leaves around the route. Matches trip_map.js: this is
  // the same track the organiser reads back on the trip page one screen
  // later, and framing it differently would read as a different route.
  //
  // There is deliberately NO
  // maxZoom beside it. A cap here does not stop the camera zooming in past
  // the track, it stops the fit ZOOMING OUT far enough to contain it: a
  // 6 km tour capped at z14 frames about two of those kilometres and the
  // route runs off the bottom of the box. The organiser has to see the
  // whole track to place a pin on it.
  var FIT_PADDING = 40;

  // Used only when the payload carries no usable bounds, so there is no
  // track to frame and the pin is all there is to look at. Close enough to
  // recognise a car park, wide enough to see the road to it.
  var FALLBACK_ZOOM = 13;

  var STRINGS = null;

  // The live map, so a rebuild after an HTMX swap can dispose of it. Module
  // scope rather than a property on the container precisely because the
  // container is what goes away.
  var currentMap = null;
  var currentMarker = null;

  /**
   * Read the picker's inline payload.
   *
   * @param {Document} doc The document to read from.
   * @returns {?Object} The parsed payload, or null when absent or malformed.
   */
  function readPayload(doc) {
    var el = doc.getElementById('trip-meeting-payload');
    if (!el) return null;
    try {
      return JSON.parse(el.textContent || '');
    } catch (err) {
      return null;
    }
  }

  /**
   * Round a coordinate to the stored precision.
   *
   * @param {*} value Any candidate value.
   * @returns {?number} The rounded number, or null when not finite.
   */
  function roundCoordinate(value) {
    var n = typeof value === 'string' ? parseFloat(value) : value;
    if (typeof n !== 'number' || !isFinite(n)) return null;
    return parseFloat(n.toFixed(COORDINATE_DP));
  }

  /**
   * Bound a latitude to the WGS-84 range.
   *
   * @param {*} value Any candidate value.
   * @returns {?number} The bounded latitude, or null when not a number.
   */
  function clampLatitude(value) {
    var n = roundCoordinate(value);
    if (n === null) return null;
    return Math.max(-LATITUDE_MAX, Math.min(LATITUDE_MAX, n));
  }

  /**
   * Bound a longitude to the WGS-84 range.
   *
   * Clamped rather than wrapped: a longitude past the antimeridian here
   * came from a typo in the manual field, not from a real place, and
   * wrapping 200 to -160 would silently move the meeting point half a
   * world away rather than pinning it at the edge where it is visible.
   *
   * @param {*} value Any candidate value.
   * @returns {?number} The bounded longitude, or null when not a number.
   */
  function clampLongitude(value) {
    var n = roundCoordinate(value);
    if (n === null) return null;
    return Math.max(-LONGITUDE_MAX, Math.min(LONGITUDE_MAX, n));
  }

  /**
   * The human readout under the map.
   *
   * @param {*} lat Latitude.
   * @param {*} lon Longitude.
   * @returns {string} "lat, lon" at stored precision, or "" if either is unusable.
   */
  function formatCoordinate(lat, lon) {
    var a = clampLatitude(lat);
    var b = clampLongitude(lon);
    if (a === null || b === null) return '';
    return a.toFixed(COORDINATE_DP) + ', ' + b.toFixed(COORDINATE_DP);
  }

  /**
   * The form's two coordinate inputs, looked up fresh every time.
   *
   * Queried per call rather than cached: the form is replaced wholesale by
   * an HTMX swap on a validation error, so a cached node would be writing
   * into a form that is no longer on the page.
   *
   * @param {Document} doc The document to read from.
   * @returns {?{latitude: HTMLInputElement, longitude: HTMLInputElement}}
   */
  function coordinateInputs(doc) {
    var lat = doc.querySelector('[data-meeting-latitude]');
    var lon = doc.querySelector('[data-meeting-longitude]');
    if (!lat || !lon) return null;
    return { latitude: lat, longitude: lon };
  }

  /**
   * Write a coordinate into the form and the readout.
   *
   * @param {Document} doc The document.
   * @param {number} lat Latitude.
   * @param {number} lon Longitude.
   * @returns {void}
   */
  function publish(doc, lat, lon) {
    var inputs = coordinateInputs(doc);
    var a = clampLatitude(lat);
    var b = clampLongitude(lon);
    if (!inputs || a === null || b === null) return;
    inputs.latitude.value = String(a);
    inputs.longitude.value = String(b);
    var readout = doc.querySelector('[data-meeting-readout]');
    if (readout) readout.textContent = formatCoordinate(a, b);
  }

  /**
   * Move the marker and recentre if it has been dragged off screen.
   *
   * @param {number} lat Latitude.
   * @param {number} lon Longitude.
   * @returns {void}
   */
  function moveMarker(lat, lon) {
    if (!currentMarker) return;
    currentMarker.setLngLat([lon, lat]);
  }

  /**
   * Draw the route beneath the pin.
   *
   * The same two-layer casing-under-line treatment trip_map.js uses, for
   * the same reason: one stroke alone is unreadable over some part of any
   * basemap. Guarded on the source so a style reload cannot double it.
   *
   * @param {Object} map A MapLibre map.
   * @param {?Object} payload The picker payload.
   * @returns {void}
   */
  /**
   * Resolve this reader's basemap, through `trip_map.js`'s own helper.
   *
   * SNOW-829. Delegated rather than reimplemented: `pwaTripMapCore` is
   * already a dependency of this module (it loads first and this file
   * reuses `routeSourceData` and `fitBoundsFor` from it), and two copies
   * of this resolution are exactly how the picker and the trip page would
   * come to disagree about which basemap the organiser is looking at.
   *
   * @param {Element} container The picker container, carrying the default.
   * @param {Document} doc The document.
   * @returns {?Object} `{key, url}`, or null when nothing resolves.
   */
  function resolveBasemapFor(container, doc) {
    var core = self.pwaTripMapCore;
    if (!core || !core.resolveBasemapFor) return null;
    return core.resolveBasemapFor(container, doc);
  }

  function installRoute(map, payload) {
    if (!payload || map.getSource('picker-route')) return;
    var core = self.pwaTripMapCore;
    if (!core) return;
    var data = core.routeSourceData(payload);
    if (!data) return;

    map.addSource('picker-route', { type: 'geojson', data: data });
    map.addLayer({
      id: 'picker-route-casing',
      type: 'line',
      source: 'picker-route',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ROUTE_CASING_COLOUR,
        'line-opacity': 0.55,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 3, 12, 7, 16, 11],
      },
    });
    map.addLayer({
      id: 'picker-route-line',
      type: 'line',
      source: 'picker-route',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ROUTE_LINE_COLOUR,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 4, 16, 7],
      },
    });
  }

  /**
   * Build the picker, tearing down any previous one.
   *
   * @returns {void}
   */
  async function init() {
    var doc = document;
    var container = doc.querySelector('[data-trip-meeting-picker]');

    // The previous map, if any, is attached to a container that an HTMX
    // swap has just detached. Dispose of it before looking at the new one
    // — MapLibre holds a WebGL context and a resize observer per map, and
    // neither is released by the node going away.
    if (currentMap) {
      currentMap.remove();
      currentMap = null;
      currentMarker = null;
    }
    if (!container) return;

    STRINGS = self.pwaStrings
      ? self.pwaStrings.read('trip-picker-strings-template', {
          'meeting-point': 'Meeting point',
          'map-failed': 'The map could not be loaded. You can still type the coordinates below.',
        })
      : { 'meeting-point': 'Meeting point', 'map-failed': 'The map could not be loaded.' };

    var payload = readPayload(doc);
    if (typeof maplibregl === 'undefined') {
      container.textContent = STRINGS['map-failed'];
      return;
    }

    var inputs = coordinateInputs(doc);
    var startLat = inputs ? clampLatitude(inputs.latitude.value) : null;
    var startLon = inputs ? clampLongitude(inputs.longitude.value) : null;
    if (startLat === null || startLon === null) {
      var fallback = payload && payload.meeting;
      startLat = fallback ? clampLatitude(fallback[1]) : 0;
      startLon = fallback ? clampLongitude(fallback[0]) : 0;
    }

    var core = self.pwaTripMapCore;
    var camera = core && payload ? core.fitBoundsFor(payload.bounds) : null;

    // The camera keys are ADDED CONDITIONALLY rather than set to undefined
    // on the branch that does not want them. MapLibre spreads the caller's
    // options over its own defaults, so an explicit `zoom: undefined`
    // REPLACES the default zoom instead of falling back to it, and the
    // transform it then builds is NaN — which surfaces as "failed to invert
    // matrix" from the first render, with a canvas present and nothing on
    // it. `bounds` and `center`/`zoom` are alternatives, and this is the
    // shape that says so.
    // SNOW-829: the same resolution the trip page makes, through the same
    // core, from the same catalogue. The organiser drops the pin on THIS
    // canvas and reads it back on that one, and the same track over two
    // different basemaps reads as two different places.
    //
    // No blank-canvas notice here, unlike the trip page. This map is a
    // CONTROL: the organiser is dragging a pin onto terrain they chose the
    // basemap for, and the notice's escape hatch — swapping the style —
    // would move the ground under a pin mid-placement.
    var basemap = resolveBasemapFor(container, doc);
    var style = basemap ? basemap.url : '';
    if (basemap && self.pwaBasemapStyleCore) {
      try {
        style = await self.pwaBasemapStyleCore.resolveStyle(
          basemap.key,
          basemap.url,
        );
      } catch (err) {
        style = basemap.url;
      }
    }

    var options = {
      container: container,
      style: style,
      attributionControl: { compact: true },
    };
    if (camera) {
      options.bounds = camera;
      options.fitBoundsOptions = { padding: FIT_PADDING };
    } else {
      // No bounds means no route to frame; fall back to the pin itself so
      // the map is never looking at open ocean.
      options.center = [startLon, startLat];
      options.zoom = FALLBACK_ZOOM;
    }
    var map = new maplibregl.Map(options);
    currentMap = map;

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    var marker = new maplibregl.Marker({ draggable: true, color: ROUTE_LINE_COLOUR })
      .setLngLat([startLon, startLat])
      .addTo(map);
    currentMarker = marker;

    marker.on('dragend', function () {
      var at = marker.getLngLat();
      publish(doc, at.lat, at.lng);
    });

    // Click-to-place as well as drag. Dragging a pin on a phone means
    // finding it first; tapping where you mean is one gesture, and it is
    // the one people reach for.
    map.on('click', function (event) {
      publish(doc, event.lngLat.lat, event.lngLat.lng);
      moveMarker(event.lngLat.lat, event.lngLat.lng);
    });

    map.on('load', function () {
      installRoute(map, payload);
    });

    // On the trip page the edit form lives inside a closed <details>, so
    // MapLibre measures a container of zero height on construction and
    // paints nothing — and it has no reason to measure again, because
    // opening a <details> fires no resize. Ask for one on open, and re-fit,
    // since a camera computed against a zero-height box framed nothing.
    var panel = container.closest('details');
    if (panel) {
      panel.addEventListener('toggle', function () {
        if (!panel.open) return;
        map.resize();
        if (camera) {
          map.fitBounds(camera, { padding: FIT_PADDING });
        }
      });
    }

    // Typing in the manual fields moves the pin, so the two controls can
    // never disagree about where the group is meeting.
    if (inputs) {
      var syncFromInputs = function () {
        var lat = clampLatitude(inputs.latitude.value);
        var lon = clampLongitude(inputs.longitude.value);
        if (lat === null || lon === null) return;
        moveMarker(lat, lon);
        map.easeTo({ center: [lon, lat], duration: 200 });
        // Through `publish`, so the CLAMPED value goes back into the field
        // the typo was typed into. Updating only the pin and the readout
        // would leave a field reading 999 under a readout reading 180 and
        // a pin standing at 180 — three answers to one question, and the
        // one that posts is the one nothing on screen agrees with.
        // Assigning .value fires no `change`, so this cannot recurse.
        publish(doc, lat, lon);
      };
      inputs.latitude.addEventListener('change', syncFromInputs);
      inputs.longitude.addEventListener('change', syncFromInputs);
    }

    // The reset returns the pin to where the route starts — the default
    // the form arrived with, and the answer for someone who moved the pin
    // to look at something and wants the original back.
    var reset = doc.querySelector('[data-meeting-reset]');
    if (reset && payload && payload.meeting) {
      reset.addEventListener('click', function () {
        var lat = clampLatitude(payload.meeting[1]);
        var lon = clampLongitude(payload.meeting[0]);
        if (lat === null || lon === null) return;
        publish(doc, lat, lon);
        moveMarker(lat, lon);
        map.easeTo({ center: [lon, lat], duration: 200 });
      });
    }

    publish(doc, startLat, startLon);
  }

  self.pwaTripMeetingPicker = Object.freeze({
    readPayload: readPayload,
    roundCoordinate: roundCoordinate,
    clampLatitude: clampLatitude,
    clampLongitude: clampLongitude,
    formatCoordinate: formatCoordinate,
  });

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }
    // The form comes back as a fresh copy when a submission fails
    // validation, taking the picker's container with it.
    document.body?.addEventListener('htmx:afterSwap', function (event) {
      if (event.target && event.target.id === 'trip-form') init();
    });
  }
})();
