/*
 * static/js/trip_map.js — the trip page's map and elevation profile
 * (SNOW-820).
 *
 * One MapLibre canvas: the trip's route drawn as a line, a marker where the
 * group meets, and the viewport fitted to the snapshot's own bbox. Beneath
 * it, the same elevation profile a route popup draws on the map page — the
 * picture the distance and vertical figures summarise.
 *
 * ## What this module owns, and what it deliberately does not
 *
 * It owns ONE camera and ONE source, built from a payload the page already
 * holds (`#trip-map-payload`, written by `apps.trips.views._trip_map_payload`).
 * It does not fetch anything, it registers no overlay with
 * `window.pwaMapOverlays`, and it never touches `window.snowdeskMapState` —
 * the map page's shared state belongs to the map page, and a trip page is a
 * document with a picture in it rather than a second map application.
 *
 * The profile and the meeting marker are `self.pwaElevationProfileCore` and
 * `self.pwaRouteMarkersCore`, reused UNCHANGED. Both already publish frozen
 * globals and own no map state; the profile a trip draws is the same picture
 * of the same geometry, and re-deriving it here would be a second, slightly
 * different set of curves for the same track.
 *
 * ## The two rules a trip's figures inherit from Route
 *
 * `createProfileSvg` returns `null` for a track with no elevation anywhere,
 * and the caller then draws NOTHING. A flat line at zero would be a picture
 * of terrain the source file never recorded — the drawn form of the
 * "0 m ascent for an unknown" lie `Trip.ascent_m`'s null exists to refuse.
 *
 * `bounds` is stored FLAT (`[w, s, e, n]`, a GeoJSON bbox) and `fitBounds`
 * wants it nested (`[[w, s], [e, n]]`). The reshape happens once, here.
 *
 * ## Colours
 *
 * Named constants mirroring `--color-route-line` and
 * `--color-route-line-casing` in `src/css/main.css`, kept in step with them
 * by hand — MapLibre paint properties cannot reference a CSS `@theme` token
 * at all, which is the same idiom and the same reason `map.js` states beside
 * its own copy of these two values.
 *
 * ## Exports (frozen `self.pwaTripMapCore`)
 *
 *   readPayload(doc)              → the parsed payload, or null
 *   fitBoundsFor(bounds)          → [[w, s], [e, n]], or null
 *   routeSourceData(payload)      → the LineString FeatureCollection
 *   meetingSourceData(payload)    → the Point FeatureCollection
 *   profileFor(payload)           → the profile data, or null
 *
 * Everything above is a pure function of the payload, so it is unit-tested
 * directly (tests/js/test_trip_map.js) with no browser and no WebGL.
 */

(function () {
  'use strict';

  /**
   * The route line's two colours, named once. See the header for why they
   * are literals here rather than reads of the stylesheet.
   */
  var ROUTE_LINE_COLOUR = '#c026d3';
  var ROUTE_CASING_COLOUR = '#1a1916';

  /** Padding, in pixels, around the fitted route. */
  var FIT_PADDING = 40;

  /** The image ids the meeting marker is registered under. */
  var MEETING_ICON = 'trip-meeting-point';

  var STRINGS = self.pwaStrings
    ? self.pwaStrings.read('trip-strings-template', {
        'meeting-point': 'Meeting point',
        'elevation-profile': 'Elevation profile of the route',
        'map-failed':
          "The map couldn't be loaded. The route details above are unaffected.",
      })
    : {
        'meeting-point': 'Meeting point',
        'elevation-profile': 'Elevation profile of the route',
        'map-failed':
          "The map couldn't be loaded. The route details above are unaffected.",
      };

  /**
   * Read and parse the inline payload.
   *
   * Defensive about what it finds: the element is written by
   * `json_script`, so a parse failure means the page is not the page this
   * module was written for, and drawing nothing is the right answer.
   *
   * @param {Document} [doc] Injectable for tests.
   * @returns {?Object} The payload, or null.
   */
  function readPayload(doc) {
    var d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d) return null;
    var el = d.getElementById('trip-map-payload');
    if (!el) return null;
    try {
      var parsed = JSON.parse(el.textContent || 'null');
      return parsed && typeof parsed === 'object' ? parsed : null;
    } catch (err) {
      return null;
    }
  }

  /**
   * Reshape a stored flat bbox into the nested pair `fitBounds` takes.
   *
   * `[w, s, e, n]` is what `Route.bounds` and therefore `Trip.bounds`
   * store (a GeoJSON bbox, RFC 7946 §5); MapLibre's `fitBounds` wants
   * `[[w, s], [e, n]]`. Returns null for anything that is not four
   * finite numbers, so a malformed snapshot leaves the map at its
   * default frame rather than throwing inside the constructor.
   *
   * @param {*} bounds
   * @returns {?Array<Array<number>>}
   */
  function fitBoundsFor(bounds) {
    if (!Array.isArray(bounds) || bounds.length !== 4) return null;
    for (var i = 0; i < 4; i += 1) {
      if (typeof bounds[i] !== 'number' || !isFinite(bounds[i])) return null;
    }
    return [
      [bounds[0], bounds[1]],
      [bounds[2], bounds[3]],
    ];
  }

  /**
   * Wrap the payload's route Feature in the FeatureCollection a GeoJSON
   * source takes.
   *
   * A collection rather than the bare Feature so the source's shape
   * matches every other GeoJSON source in this project, and so an empty
   * one is expressible (a payload with no geometry draws no line rather
   * than a source MapLibre rejects).
   *
   * @param {?Object} payload
   * @returns {Object} A FeatureCollection, possibly empty.
   */
  function routeSourceData(payload) {
    var feature = payload && payload.route;
    var coordinates =
      feature && feature.geometry && feature.geometry.coordinates;
    var usable = Array.isArray(coordinates) && coordinates.length > 1;
    return { type: 'FeatureCollection', features: usable ? [feature] : [] };
  }

  /**
   * Wrap the payload's meeting-point Feature the same way.
   *
   * @param {?Object} payload
   * @returns {Object} A FeatureCollection, possibly empty.
   */
  function meetingSourceData(payload) {
    var feature = payload && payload.meeting;
    var coordinates =
      feature && feature.geometry && feature.geometry.coordinates;
    var usable =
      Array.isArray(coordinates) &&
      coordinates.length >= 2 &&
      typeof coordinates[0] === 'number' &&
      typeof coordinates[1] === 'number';
    return { type: 'FeatureCollection', features: usable ? [feature] : [] };
  }

  /**
   * Derive the elevation profile from the payload's route geometry.
   *
   * The elevation series is already the third ordinate of every stored
   * coordinate — `Trip.points` snapshots `Route.points`, which stores
   * `[lon, lat, ele]` — so this is a pure derivation of what the page
   * already holds, exactly as the map page's route popup is.
   *
   * @param {?Object} payload
   * @returns {?Object} The profile, or null when there is nothing to draw.
   */
  function profileFor(payload) {
    if (!self.pwaElevationProfileCore) return null;
    var feature = payload && payload.route;
    var coordinates =
      feature && feature.geometry && feature.geometry.coordinates;
    if (!Array.isArray(coordinates) || coordinates.length < 2) return null;
    return self.pwaElevationProfileCore.readProfile(coordinates);
  }

  /**
   * Draw the elevation profile into its container, or leave it empty.
   *
   * A track with no elevation anywhere yields no paths, `createProfileSvg`
   * returns null, and nothing is drawn — see the header for why a flat
   * line at zero is not an acceptable substitute.
   *
   * @param {?Object} payload
   * @param {Document} doc
   * @returns {void}
   */
  function drawProfile(payload, doc) {
    var host = doc.querySelector('[data-trip-profile]');
    if (!host) return;
    var profile = profileFor(payload);
    if (!profile) return;
    var svg = self.pwaElevationProfileCore.createProfileSvg(profile, {
      doc: doc,
      label: STRINGS['elevation-profile'],
    });
    if (!svg) return;
    host.appendChild(svg);
  }

  /**
   * Register the meeting-point icon, reusing the map page's start dot.
   *
   * The start dot rather than a bespoke glyph: a filled disc is already
   * this project's "a point on a route" symbol, and inventing a second one
   * for a point that IS on the route would say the two are different kinds
   * of thing. The casing colour paints it, so it reads against both a pale
   * basemap and the line it sits on.
   *
   * @param {Object} map A MapLibre map.
   * @returns {void}
   */
  function ensureMeetingIcon(map) {
    if (!self.pwaRouteMarkersCore || map.hasImage(MEETING_ICON)) return;
    var core = self.pwaRouteMarkersCore;
    // 0xc0, 0x26, 0xd3 — ROUTE_LINE_COLOUR's channels. The core owns the
    // pixels and takes the colour in, which is the split it documents.
    //
    // The core's return value is passed STRAIGHT to addImage, exactly as
    // map.js's ensureRouteMarkerImages does. `startDotPixels` already
    // returns `{width, height, data}`; re-wrapping it in a second object
    // put the whole image where MapLibre expects the raw pixel buffer and
    // threw "mismatched image size", which killed the meeting marker (and
    // everything after it in installLayers) on every trip page.
    map.addImage(MEETING_ICON, core.startDotPixels(0xc0, 0x26, 0xd3), {
      pixelRatio: core.PIXEL_RATIO,
    });
  }

  /**
   * Install the route line, its casing and the meeting marker.
   *
   * Idempotent, guarded on the source, so a style reload cannot duplicate
   * layers. The CASING IS ADDED FIRST so MapLibre paints it underneath: a
   * single stroke is unreadable somewhere, and the dark under-stroke is
   * what lets one line colour work over both a pale basemap and a dark
   * one. The same install order and the same reasoning `map.js` states for
   * its own routes layer.
   *
   * @param {Object} map A MapLibre map.
   * @param {?Object} payload
   * @returns {void}
   */
  function installLayers(map, payload) {
    if (map.getSource('trip-route')) return;

    map.addSource('trip-route', {
      type: 'geojson',
      data: routeSourceData(payload),
    });
    map.addLayer({
      id: 'trip-route-casing',
      type: 'line',
      source: 'trip-route',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ROUTE_CASING_COLOUR,
        'line-opacity': 0.55,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 3, 12, 7, 16, 11],
      },
    });
    map.addLayer({
      id: 'trip-route-line',
      type: 'line',
      source: 'trip-route',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ROUTE_LINE_COLOUR,
        'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 4, 16, 7],
      },
    });

    ensureMeetingIcon(map);
    map.addSource('trip-meeting', {
      type: 'geojson',
      data: meetingSourceData(payload),
    });
    map.addLayer({
      id: 'trip-meeting-point',
      type: 'symbol',
      source: 'trip-meeting',
      layout: {
        'icon-image': MEETING_ICON,
        // The whole point of this marker is that it is visible; MapLibre's
        // default collision would drop it where it sits on the line's own
        // start.
        'icon-allow-overlap': true,
        'icon-ignore-placement': true,
        'icon-anchor': 'center',
      },
    });
  }

  /**
   * Boot the trip map, once, if this page has one.
   *
   * @returns {void}
   */
  function init() {
    var doc = document;
    var container = doc.querySelector('[data-trip-map]');
    if (!container) return;

    var payload = readPayload(doc);
    drawProfile(payload, doc);

    if (typeof maplibregl === 'undefined') {
      container.textContent = STRINGS['map-failed'];
      return;
    }

    var camera = fitBoundsFor(payload && payload.bounds);
    var map = new maplibregl.Map({
      container: container,
      style: container.getAttribute('data-basemap-url') || '',
      // `bounds` + `fitBoundsOptions` rather than center/zoom: the
      // snapshot knows exactly what has to be on screen, and a centre plus
      // a guessed zoom would frame a 2 km valley tour and a 40 km traverse
      // identically. map.js's constructor makes the same choice.
      bounds: camera || undefined,
      fitBoundsOptions: { padding: FIT_PADDING },
      attributionControl: { compact: true },
    });

    // Bound to the constructor's return rather than to a `load` handler:
    // `load` never fires when the basemap style fails to fetch, and a trip
    // page that showed no route because a third-party CDN was slow would
    // be a page about nothing. `style.load` fires for the real style AND
    // for a style swapped in later, so the idempotent install is what
    // makes a second firing harmless.
    map.on('style.load', function () {
      installLayers(map, payload);
    });
  }

  self.pwaTripMapCore = Object.freeze({
    readPayload: readPayload,
    fitBoundsFor: fitBoundsFor,
    routeSourceData: routeSourceData,
    meetingSourceData: meetingSourceData,
    profileFor: profileFor,
    // Exported for its test rather than for a second caller. It is the one
    // function here that hands MapLibre a structure MapLibre validates at
    // runtime, and getting that structure wrong threw inside installLayers
    // and took the meeting marker out with it — a failure no assertion
    // about the payload arithmetic could ever have seen.
    ensureMeetingIcon: ensureMeetingIcon,
  });

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }
  }
})();
