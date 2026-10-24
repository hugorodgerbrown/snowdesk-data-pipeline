/*
 * static/js/trip_meeting_picker.js — placing the meeting point (SNOW-820,
 * SNOW-1214).
 *
 * The meeting point began life as two number inputs prefilled with the
 * route's first coordinate. That works only for someone who already knows
 * what 46.080012, 7.318197 means, which is nobody: a group agrees to meet
 * at the top car park, not at a decimal degree. This module makes the map
 * the control.
 *
 * ## The pin stays in the middle; the map moves (SNOW-1214)
 *
 * SNOW-820 drew a draggable marker. On an iPad it did not always land
 * where it was tapped, the finger aiming it covered it, and SNOW-840 left
 * no way to move it from a keyboard. The pin is now an element laid over
 * the centre of the frame and the map moves under it: drag, pinch, the
 * arrow keys, a tap (which eases the map to that spot) or the two quick
 * picks, Route start and Route end. Whatever moved it, `moveend` writes
 * the map's centre into the form.
 *
 * ## The coordinate is never shown
 *
 * Under the map: the spot's three word address, asked for each time the
 * map settles (debounced, from `trips:what3words`), and one sentence
 * placing the pin against the route from `meeting_place_core.js`. While
 * the map moves the address line says "Finding the address…"; if the
 * lookup fails it says the address is added on save, which is true —
 * the server converts the saved point again.
 *
 * ## The inputs are still the source of truth
 *
 * The pin WRITES to the form's own hidden `latitude` / `longitude` fields
 * and never holds the value itself, so the form posts and validates
 * exactly as before. A visitor with no JavaScript submits the prefilled
 * route start rather than an empty required field.
 *
 * ## It rebuilds itself after an HTMX swap
 *
 * An invalid submission comes back as a fresh copy of the whole form, so
 * the container this map was built in is GONE and a new one has taken its
 * place. `init` therefore tears the previous map down and builds on
 * whatever container is in the document now. Re-binding to the old element
 * would leave a live MapLibre canvas attached to a detached node, drawing
 * nothing and holding a WebGL context open.
 *
 * ## Exports (frozen `self.pwaTripMeetingPicker`)
 *
 *   readPayload(doc)            → the picker payload, or null
 *   roundCoordinate(value)      → 6 dp, or null when not finite
 *   clampLatitude(value)        → value bounded to [-90, 90], or null
 *   clampLongitude(value)       → value bounded to [-180, 180], or null
 *   routeEnds(payload)          → {start, end} as [lon, lat], or null
 *   boundsAroundPin(box, lat, lon) → the route's box mirrored about the pin
 *   createAddressLookup(opts)   → the debounced address fetcher
 *
 * All unit-tested in tests/js/test_trip_meeting_picker.js with no browser
 * and no WebGL.
 */

(function () {
  'use strict';

  // Six decimal places is ~11 cm at the equator — far finer than anyone
  // can point at on a phone. Posting the raw camera float would put
  // fifteen digits in the value for no gain.
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

  // How long the map must sit still before its address is asked for. Long
  // enough that a pan made of several drags asks once, short enough that
  // the words arrive while the organiser is still looking at the pin.
  var ADDRESS_DEBOUNCE_MS = 400;

  // How far a quick pick or a tap travels, in ms. Short: the organiser
  // asked to be somewhere and should see the map go there, not watch it.
  var EASE_MS = 300;

  // Every string this module writes, in English, as the fallback for the
  // translated copies _trip_meeting_picker.html renders. Named rather than
  // inline so tests/test_js_strings_are_translatable.py can read its keys.
  var PICKER_STRINGS = {
    'meeting-point': 'Meeting point',
    'map-failed': "The map could not be loaded. The group will meet at the route's start.",
    finding: 'Finding the address…',
    'address-later': 'The address is added when you save',
    start: "At the route's start",
    end: "At the route's end",
    on_route: 'On the route, {distance} in',
    from_start: "{distance} {direction} of the route's start",
    from_end: "{distance} {direction} of the route's end",
    'octant-N': 'north',
    'octant-NE': 'north-east',
    'octant-E': 'east',
    'octant-SE': 'south-east',
    'octant-S': 'south',
    'octant-SW': 'south-west',
    'octant-W': 'west',
    'octant-NW': 'north-west',
  };

  var STRINGS = null;

  // The live map, so a rebuild after an HTMX swap can dispose of it. Module
  // scope rather than a property on the container precisely because the
  // container is what goes away.
  var currentMap = null;
  var currentLookup = null;

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
   * came from a tampered field, not from a real place, and wrapping 200
   * to -160 would silently move the meeting point half a world away.
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
   * The route's two ends, for the quick picks.
   *
   * @param {?Object} payload The picker payload.
   * @returns {?{start: Array<number>, end: Array<number>}} `[lon, lat]` each.
   */
  function routeEnds(payload) {
    var coords = payload && payload.route && payload.route.geometry
      ? payload.route.geometry.coordinates
      : null;
    if (!coords || !coords.length) return null;
    var first = coords[0];
    var last = coords[coords.length - 1];
    return { start: [first[0], first[1]], end: [last[0], last[1]] };
  }

  /**
   * Build the debounced address lookup.
   *
   * Each `request(lat, lon)` cancels the one before it, waits `delay` ms
   * and asks `url` for the spot's three word address. Only the LATEST
   * request may report, so a slow answer about where the map was a second
   * ago can never overwrite the address of where it is now.
   *
   * `onState` is called with one of:
   *   {state: 'finding'}                 — at once, on every request
   *   {state: 'found', words, url}       — the address arrived
   *   {state: 'failed'}                  — offline, an error, or no words
   *
   * @param {{url: string, onState: function(Object): void,
   *          fetch?: Function, delay?: number}} options
   * @returns {{request: function(number, number): void, cancel: function(): void}}
   */
  function createAddressLookup(options) {
    var fetcher = options.fetch || (typeof fetch === 'function' ? fetch : null);
    var delay = typeof options.delay === 'number' ? options.delay : ADDRESS_DEBOUNCE_MS;
    var timer = null;
    var controller = null;
    var serial = 0;

    function cancel() {
      if (timer !== null) clearTimeout(timer);
      timer = null;
      if (controller) controller.abort();
      controller = null;
      serial += 1;
    }

    function request(lat, lon) {
      cancel();
      var mine = serial;
      options.onState({ state: 'finding' });
      timer = setTimeout(function () {
        timer = null;
        // Offline is a known answer, not a reason to wait out a timeout.
        if (!fetcher || (typeof navigator !== 'undefined' && navigator.onLine === false)) {
          options.onState({ state: 'failed' });
          return;
        }
        controller = typeof AbortController === 'function' ? new AbortController() : null;
        var query = '?lat=' + encodeURIComponent(String(lat))
          + '&lon=' + encodeURIComponent(String(lon));
        Promise.resolve()
          .then(function () {
            return fetcher(options.url + query, {
              credentials: 'same-origin',
              headers: { Accept: 'application/json' },
              signal: controller ? controller.signal : undefined,
            });
          })
          .then(function (response) {
            if (!response.ok) throw new Error('address lookup ' + response.status);
            return response.json();
          })
          .then(function (body) {
            if (mine !== serial) return;
            if (body && typeof body.words === 'string' && body.words) {
              options.onState({ state: 'found', words: body.words, url: body.url || '' });
            } else {
              options.onState({ state: 'failed' });
            }
          })
          .catch(function () {
            if (mine !== serial) return;
            options.onState({ state: 'failed' });
          });
      }, delay);
    }

    return { request: request, cancel: cancel };
  }

  /**
   * Write a coordinate into the form's hidden fields.
   *
   * The inputs are queried per call rather than cached: the form is
   * replaced wholesale by an HTMX swap on a validation error, so a cached
   * node would be writing into a form that is no longer on the page.
   *
   * @param {Document} doc The document.
   * @param {number} lat Latitude.
   * @param {number} lon Longitude.
   * @returns {void}
   */
  function publish(doc, lat, lon) {
    var latInput = doc.querySelector('[data-meeting-latitude]');
    var lonInput = doc.querySelector('[data-meeting-longitude]');
    var a = clampLatitude(lat);
    var b = clampLongitude(lon);
    if (!latInput || !lonInput || a === null || b === null) return;
    latInput.value = String(a);
    lonInput.value = String(b);
  }

  /**
   * Show the address line's state.
   *
   * @param {Document} doc The document.
   * @param {Object} update An `onState` value from the lookup.
   * @returns {void}
   */
  function showAddress(doc, update) {
    var line = doc.querySelector('[data-meeting-address]');
    if (!line) return;
    line.setAttribute('data-state', update.state);
    if (update.state === 'found') {
      // The words are what3words' own and reach the page as text, never
      // markup; the slashes are presentation, added here as on the server.
      line.textContent = '///' + update.words;
    } else if (update.state === 'finding') {
      line.textContent = STRINGS.finding;
    } else {
      line.textContent = STRINGS['address-later'];
    }
  }

  /**
   * Show the sentence placing the pin against the route.
   *
   * @param {Document} doc The document.
   * @param {?Object} payload The picker payload.
   * @param {number} lat Latitude.
   * @param {number} lon Longitude.
   * @returns {void}
   */
  function showPlace(doc, payload, lat, lon) {
    var line = doc.querySelector('[data-meeting-place]');
    var place = self.pwaMeetingPlaceCore;
    if (!line || !place) return;
    var coords = payload && payload.route && payload.route.geometry
      ? payload.route.geometry.coordinates
      : null;
    line.textContent = place.sentence(place.describe(coords, lat, lon), STRINGS);
  }

  /**
   * Resolve this reader's basemap, through `trip_map.js`'s own helper.
   *
   * SNOW-829. Delegated rather than reimplemented: `pwaTripMapCore` is
   * already a dependency of this module, and two copies of this
   * resolution are exactly how the picker and the trip page would come to
   * disagree about which basemap the organiser is looking at.
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

  /**
   * Draw the route beneath the pin.
   *
   * SNOW-1212: the map's own drawing (`route_drawing_core.js`), as the
   * trip page draws it — legs below z14, slope classes from z14 — so the
   * organiser can see where the climbs start when choosing where to meet.
   * Guarded on the source so a style reload cannot double it.
   *
   * @param {Object} map A MapLibre map.
   * @param {?Object} payload The picker payload.
   * @returns {void}
   */
  function installRoute(map, payload) {
    if (!payload || map.getSource('picker-route')) return;
    var core = self.pwaTripMapCore;
    var drawing = self.pwaRouteDrawingCore;
    if (!core || !drawing) return;
    drawing.install(map, core.routeSourceData(payload), { prefix: 'picker-route' });
  }

  /**
   * The route's box, mirrored about the pin so the pin is its centre.
   *
   * The pin is the map's centre, so the route cannot simply be fitted: a
   * fit centres on the route's middle and would move the meeting point.
   * Reflecting the route's box through the pin gives the smallest box
   * that is centred on the pin and still holds the whole track.
   *
   * @param {?Array<Array<number>>} camera The route's `[[w, s], [e, n]]`.
   * @param {number} lat The pin's latitude.
   * @param {number} lon The pin's longitude.
   * @returns {?Array<Array<number>>} The box, or null with no route box.
   */
  function boundsAroundPin(camera, lat, lon) {
    if (!camera) return null;
    var halfLon = Math.max(Math.abs(camera[0][0] - lon), Math.abs(camera[1][0] - lon));
    var halfLat = Math.max(Math.abs(camera[0][1] - lat), Math.abs(camera[1][1] - lat));
    return [
      [lon - halfLon, lat - halfLat],
      [lon + halfLon, lat + halfLat],
    ];
  }

  /**
   * Centre the map on the pin with the whole route in view.
   *
   * With no usable box, or a container of zero height (which has no zoom
   * to fit), it falls back to a fixed close zoom on the pin.
   *
   * @param {Object} map A MapLibre map.
   * @param {?Array<Array<number>>} camera The route's bounds, or null.
   * @param {number} lat The pin's latitude.
   * @param {number} lon The pin's longitude.
   * @returns {void}
   */
  function frame(map, camera, lat, lon) {
    var zoom = FALLBACK_ZOOM;
    var box = boundsAroundPin(camera, lat, lon);
    if (box && map.cameraForBounds) {
      var fitted = map.cameraForBounds(box, { padding: FIT_PADDING });
      if (fitted && typeof fitted.zoom === 'number' && isFinite(fitted.zoom)) {
        zoom = fitted.zoom;
      }
    }
    map.jumpTo({ center: [lon, lat], zoom: zoom });
  }

  /**
   * Build the picker, tearing down any previous one.
   *
   * @returns {Promise<void>}
   */
  async function init() {
    var doc = document;
    var container = doc.querySelector('[data-trip-meeting-picker]');

    // The previous map, if any, is attached to a container that an HTMX
    // swap has just detached. Dispose of it before looking at the new one
    // — MapLibre holds a WebGL context and a resize observer per map, and
    // neither is released by the node going away.
    if (currentLookup) {
      currentLookup.cancel();
      currentLookup = null;
    }
    if (currentMap) {
      currentMap.remove();
      currentMap = null;
    }
    if (!container) return;

    STRINGS = self.pwaStrings
      ? self.pwaStrings.read('trip-picker-strings-template', PICKER_STRINGS)
      : PICKER_STRINGS;

    var payload = readPayload(doc);
    var latInput = doc.querySelector('[data-meeting-latitude]');
    var lonInput = doc.querySelector('[data-meeting-longitude]');
    var startLat = latInput ? clampLatitude(latInput.value) : null;
    var startLon = lonInput ? clampLongitude(lonInput.value) : null;
    if (startLat === null || startLon === null) {
      var fallback = payload && payload.meeting;
      startLat = fallback ? clampLatitude(fallback[1]) : 0;
      startLon = fallback ? clampLongitude(fallback[0]) : 0;
    }
    showPlace(doc, payload, startLat, startLon);

    if (typeof maplibregl === 'undefined') {
      container.textContent = STRINGS['map-failed'];
      return;
    }

    var addressUrl = container.getAttribute('data-what3words-url');
    var lookup = addressUrl
      ? createAddressLookup({
          url: addressUrl,
          onState: function (update) { showAddress(doc, update); },
        })
      : null;
    currentLookup = lookup;

    var core = self.pwaTripMapCore;
    var camera = core && payload ? core.fitBoundsFor(payload.bounds) : null;

    // SNOW-829: the same resolution the trip page makes, through the same
    // core, from the same catalogue. The organiser places the pin on THIS
    // canvas and reads it back on that one, and the same track over two
    // different basemaps reads as two different places.
    //
    // No blank-canvas notice here, unlike the trip page. This map is a
    // CONTROL, and the notice's escape hatch — swapping the style — would
    // move the ground under the pin mid-placement.
    var basemap = resolveBasemapFor(container, doc);
    var style = basemap ? basemap.url : '';
    if (basemap && self.pwaBasemapStyleCore) {
      try {
        style = await self.pwaBasemapStyleCore.resolveStyle(basemap.key, basemap.url);
      } catch (err) {
        style = basemap.url;
      }
    }

    // Built on the pin, never on `bounds`: a fit would centre on the
    // route's middle, and the centre IS the meeting point. `frame` then
    // sets the zoom that shows the whole route around it.
    var map = new maplibregl.Map({
      container: container,
      style: style,
      center: [startLon, startLat],
      zoom: FALLBACK_ZOOM,
      attributionControl: { compact: true },
    });
    currentMap = map;
    frame(map, camera, startLat, startLon);

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    var pin = doc.querySelector('[data-meeting-pin]');

    // The address is of where the pin WAS the moment the map moves, so it
    // gives way to "Finding the address…" at once, not when it settles.
    map.on('movestart', function () {
      if (pin) pin.setAttribute('data-moving', 'true');
      if (lookup) showAddress(doc, { state: 'finding' });
    });

    // Whatever moved the map — a drag, a pinch, the arrow keys, a tap, a
    // quick pick — its centre is now the meeting point.
    map.on('moveend', function () {
      if (pin) pin.setAttribute('data-moving', 'false');
      var at = map.getCenter();
      var lat = clampLatitude(at.lat);
      var lon = clampLongitude(at.lng);
      if (lat === null || lon === null) return;
      publish(doc, lat, lon);
      showPlace(doc, payload, lat, lon);
      if (lookup) lookup.request(lat, lon);
    });

    // A tap says "there": the map eases until that spot is under the pin.
    map.on('click', function (event) {
      map.easeTo({ center: [event.lngLat.lng, event.lngLat.lat], duration: EASE_MS });
    });

    map.on('load', function () {
      installRoute(map, payload);
    });

    var ends = routeEnds(payload);
    [['trip-meeting-start', 'start'], ['trip-meeting-end', 'end']].forEach(function (pick) {
      var button = doc.getElementById(pick[0]);
      if (!button) return;
      if (!ends) {
        button.hidden = true;
        return;
      }
      button.addEventListener('click', function () {
        map.easeTo({ center: ends[pick[1]], duration: EASE_MS });
      });
    });

    // On the trip page the edit form lives inside a closed <details>, so
    // MapLibre measures a container of zero height on construction and
    // paints nothing — and it has no reason to measure again, because
    // opening a <details> fires no resize. Ask for one on open, and frame
    // again, since a zoom computed against a zero-height box framed nothing.
    var panel = container.closest('details');
    if (panel) {
      panel.addEventListener('toggle', function () {
        if (!panel.open) return;
        map.resize();
        var at = map.getCenter();
        frame(map, camera, at.lat, at.lng);
      });
    }

    publish(doc, startLat, startLon);
    if (lookup) lookup.request(startLat, startLon);
  }

  self.pwaTripMeetingPicker = Object.freeze({
    readPayload: readPayload,
    roundCoordinate: roundCoordinate,
    clampLatitude: clampLatitude,
    clampLongitude: clampLongitude,
    routeEnds: routeEnds,
    boundsAroundPin: boundsAroundPin,
    createAddressLookup: createAddressLookup,
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
