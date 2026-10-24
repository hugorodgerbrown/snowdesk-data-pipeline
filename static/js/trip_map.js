/*
 * static/js/trip_map.js — the trip page's map and elevation profile
 * (SNOW-820).
 *
 * One MapLibre canvas: the trip's route drawn as the map draws it (its legs
 * below z14, slope classes from z14 — `route_drawing_core.js`, SNOW-1212), a
 * marker where the group meets, and the viewport fitted to the snapshot's
 * own bbox, with +/− and cooperative gestures so the page still scrolls
 * past it. Beneath it, in the same card, the map's own route profile
 * (route_profile.js, drawn by trip_route_profile.js — SNOW-1216); a press
 * or a drag on the profile places a point there, and this module draws
 * the same point on the line.
 *
 * ## What this module owns, and what it deliberately does not
 *
 * It owns ONE camera and the route's and meeting point's sources, built
 * from a payload the page already holds (`#trip-map-payload`, written by `apps.trips.views._trip_map_payload`).
 * It does not fetch anything, it registers no overlay with
 * `window.pwaMapOverlays`, and it never touches `window.snowdeskMapState` —
 * the map page's shared state belongs to the map page, and a trip page is a
 * document with a picture in it rather than a second map application.
 *
 * The meeting marker is `self.pwaRouteMarkersCore`, reused UNCHANGED.
 *
 * ## The profile's point, on the map (SNOW-1216)
 *
 * The profile owns no map and this module owns no profile. The profile
 * announces the sample index a press placed as a `trip:route-cursor`
 * event on the document (`{index}`, null to clear), and this module turns
 * the index into a place with route_cursor_map_core.js — the map page's
 * own conversion, from the slope record's boundary points on a sampled
 * route, by equal shares of the line on one never sampled — and draws a
 * dot there, panning only when the dot would land off the canvas. The
 * index, never a distance, crosses the boundary, for the reason that
 * module's header gives: the profile's distances and the sampler's do not
 * agree.
 *
 * ## The rule a trip's figures inherit from Route
 *
 * `bounds` is stored FLAT (`[w, s, e, n]`, a GeoJSON bbox) and `fitBounds`
 * wants it nested (`[[w, s], [e, n]]`). The reshape happens once, here.
 *
 * ## Colours
 *
 * The route's are the shared drawing's, which names the tokens they mirror.
 * The meeting dot is the route line's fuchsia, passed to the markers core
 * as channels.
 *
 * ## The reader's own basemap (SNOW-829)
 *
 * This canvas renders on whatever the reader picked on the map page, not
 * on the site default. `localStorage` is scoped per ORIGIN, so the key the
 * map's picker writes (`snowdesk.map.basemap`) is readable here; the
 * catalogue to resolve it against is emitted by the page
 * (`trips/partials/_trip_basemaps.html`). Read ONCE at boot — there is no
 * picker on this page, so no `snowdesk:basemap-changed` will ever fire.
 *
 * The national basemaps cover one country each and render BLANK outside
 * it, and unlike the map page this one has no picker in front of the
 * reader to fix that with. So after the map goes idle it asks the canvas
 * what it actually painted (`pwaBasemapStyleCore.drewNothing`) and, if
 * only this page's own two sources drew, reveals a notice offering the
 * standard map. Coverage is detected and never declared: the providers'
 * own extents are unusable for it — swisstopo's declared box CONTAINS
 * Chamonix, and IGN's is the whole world (see that module's header).
 *
 * ## Exports (frozen `self.pwaTripMapCore`)
 *
 *   readPayload(doc)              → the parsed payload, or null
 *   fitBoundsFor(bounds)          → [[w, s], [e, n]], or null
 *   routeSourceData(payload)      → the LineString FeatureCollection
 *   meetingSourceData(payload)    → the Point FeatureCollection
 *   installLayers(map, payload)   → adds the sources and layers (its test)
 *   cursorFeature(payload, index) → the profile's point as a Point
 *                                   Feature, or null
 *   readBasemaps(doc)             → the {key: url} catalogue, or null
 *   resolveBasemapFor(el, doc)    → {key, url} for this reader, or null
 *
 * SNOW-1019 took the fall-line arrows and no-fall passage split line off
 * this map, as off the map page (where rail two's bank ribbon and passage
 * bars stood in for them until SNOW-1065 retired it). The snapshot still
 * carries both records.
 *
 * Everything above is a pure function of the payload, so it is unit-tested
 * directly (tests/js/test_trip_map.js) with no browser and no WebGL.
 */

(function () {
  'use strict';

  /**
   * The prefix the shared route drawing names this page's sources and
   * layers with (`trip-route`, `trip-route-legs`, …).
   */
  var ROUTE_PREFIX = 'trip-route';

  /** Padding, in pixels, around the fitted route. */
  var FIT_PADDING = 40;

  /** The image ids the meeting marker is registered under. */
  var MEETING_ICON = 'trip-meeting-point';

  /** The source, and the one layer, the profile's point is drawn from. */
  var CURSOR_SOURCE = 'trip-route-cursor';

  /** The route line's colour, the dot's fill where no class colours it. */
  var ROUTE_LINE_COLOUR = (self.pwaRouteDrawingCore && self.pwaRouteDrawingCore.ROUTE_LINE_COLOUR) || '#c026d3';

  /** The ring round the dot, so it reads over the line it sits on. */
  var CURSOR_HALO = '#ffffff';

  /**
   * The profile's last index, kept so a style swapped in later reinstalls
   * the dot where it was.
   *
   * @type {?number}
   */
  var cursorIndex = null;

  // The English fallbacks are the only copy of these strings a reader of
  // this file can see, so they double as documentation of what each key
  // means. `i18n_strings.js` is loaded first by `_trip_map.html`, in
  // document order, so `self.pwaStrings` is here.
  var STRINGS = self.pwaStrings.read('trip-strings-template', {
    'meeting-point': 'Meeting point',
    'map-failed':
      "The map couldn't be loaded. The route details above are unaffected.",
  });

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

  /** An unsampled route's share midpoints, worked out once per feature. */
  var MIDPOINTS = new WeakMap();

  /**
   * The profile's point as a place on the route, or null.
   *
   * The sample index is converted as the map page converts it
   * (route_cursor_map_core.js): on a sampled route, the middle of the
   * index's segment of the slope record, carrying the segment's class
   * colour; on a route never sampled, the middle of the index's equal
   * share of the line, out of the count the profile used — the last leg's
   * `to` plus one (route_profile.js `sampleCountOf`).
   *
   * @param {?Object} payload
   * @param {?number} index The profile's sample index; null for none.
   * @returns {?Object} A Point Feature, or null.
   */
  function cursorFeature(payload, index) {
    var core = self.pwaRouteCursorMapCore;
    var feature = payload && payload.route;
    if (!core || !feature || index === null || index === undefined) return null;
    var props = feature.properties || {};
    var coordinates = (feature.geometry && feature.geometry.coordinates) || [];
    if (core.sampleCount(props.slope)) {
      return core.cursorPoint(props.slope, index, coordinates);
    }
    var legs = Array.isArray(props.legs) ? props.legs : [];
    var last = legs.length ? legs[legs.length - 1] : null;
    if (!last || !Number.isInteger(last.to)) return null;
    var midpoints = MIDPOINTS.get(feature);
    if (!midpoints) {
      // Every share's midpoint at once, as map.js's bindRouteCursor does:
      // shareMidpoints walks the whole line, and a drag asks per move.
      midpoints = core.shareMidpoints(coordinates, last.to + 1);
      MIDPOINTS.set(feature, midpoints);
    }
    var middle = midpoints[index];
    if (!middle) return null;
    return {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: middle },
      properties: { index: index },
    };
  }

  /**
   * The cursor source's data for a feature, or the empty collection.
   *
   * @param {?Object} point
   * @returns {Object}
   */
  function cursorSourceData(point) {
    return { type: 'FeatureCollection', features: point ? [point] : [] };
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
    // 0xc0, 0x26, 0xd3 — the route line's channels. The core owns the
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
   * Install the route and the meeting marker.
   *
   * SNOW-1212: the route is the map page's own drawing
   * (`route_drawing_core.js`): its legs below z14, slope classes from z14,
   * the numbered transitions, the start dot and the finish flag. One track
   * reads the same on the trip page as on the map. This page has nothing
   * to put between the route's layers, so it hands the drawing the whole
   * install.
   *
   * Idempotent, guarded on the route source, so a style reload cannot
   * duplicate layers. The meeting marker goes on last, over the route it
   * usually sits on.
   *
   * @param {Object} map A MapLibre map.
   * @param {?Object} payload
   * @returns {void}
   */
  function installLayers(map, payload) {
    if (map.getSource(ROUTE_PREFIX)) return;
    var drawing = self.pwaRouteDrawingCore;
    if (drawing) {
      drawing.install(map, routeSourceData(payload), { prefix: ROUTE_PREFIX });
    }

    ensureMeetingIcon(map);
    map.addSource('trip-meeting', {
      type: 'geojson',
      data: meetingSourceData(payload),
    });
    // SNOW-1216: the profile's point. Under the meeting marker, which is
    // the plan and must never be hidden by a reader scrubbing past it.
    // Filled in the colour of the ground under it (the segment's class,
    // as on the map page) inside a white ring, at every zoom: this map
    // draws no cursor segment, so the dot never gives way to one.
    map.addSource(CURSOR_SOURCE, {
      type: 'geojson',
      data: cursorSourceData(cursorFeature(payload, cursorIndex)),
    });
    map.addLayer({
      id: CURSOR_SOURCE,
      type: 'circle',
      source: CURSOR_SOURCE,
      paint: {
        'circle-color': ['coalesce', ['get', 'colour'], ROUTE_LINE_COLOUR],
        'circle-radius': 6,
        'circle-stroke-color': CURSOR_HALO,
        'circle-stroke-width': 2,
      },
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
   * The source ids this page adds itself.
   *
   * Subtracted from `queryRenderedFeatures()` so what remains is the
   * basemap's own drawing — see `basemapDrewNothing` below.
   *
   * EVERY SOURCE THIS MODULE INSTALLS HAS TO BE LISTED. `drewNothing`
   * answers false the moment it sees a source that is not here, so an
   * omission does not degrade the check — it disables it, and silently:
   * the blank-basemap notice and its switch-to-the-standard-map escape
   * hatch would simply stop appearing, on exactly the trips that have the
   * omitted source. Since SNOW-1212 the route's five sources are the
   * shared drawing's, so they are read off it rather than listed here.
   */
  var OUR_SOURCE_IDS = (function () {
    var drawing = self.pwaRouteDrawingCore;
    var ids = drawing ? drawing.sourceIds(ROUTE_PREFIX) : { route: ROUTE_PREFIX };
    return Object.keys(ids)
      .map(function (key) { return ids[key]; })
      .concat(['trip-meeting', CURSOR_SOURCE]);
  })();

  /**
   * Read the {key: url} basemap catalogue the page emitted.
   *
   * Defensive for the reason `readPayload` is: a missing or unparseable
   * element means this is not the page this module was written for, and
   * `resolveBasemap` answers with the site default for a null catalogue,
   * which is the pre-SNOW-829 behaviour rather than a failure.
   *
   * @param {Document} [doc] Injectable for tests.
   * @returns {?Object} The catalogue, or null.
   */
  function readBasemaps(doc) {
    var d = doc || (typeof document !== 'undefined' ? document : null);
    if (!d) return null;
    var el = d.getElementById('trip-basemaps');
    if (!el) return null;
    try {
      var parsed = JSON.parse(el.textContent || 'null');
      return parsed && typeof parsed === 'object' ? parsed : null;
    } catch (err) {
      return null;
    }
  }

  /**
   * Resolve which basemap this reader gets on this page.
   *
   * SNOW-829. `localStorage` is scoped per ORIGIN, so the key the map
   * page's picker writes is readable here — a reader who chose swisstopo
   * keeps it on the page where the route is actually being read. Read ONCE
   * at boot: `snowdesk:basemap-changed` is dispatched by `map.js` after a
   * swap on the map page, and there is no picker here to fire it.
   *
   * The storage read is wrapped because a browser set to block site data
   * throws on access rather than answering null.
   *
   * @param {Element} container The map container, carrying the default key.
   * @param {Document} [doc] Injectable for tests.
   * @returns {?Object} `{key, url}`, or null when nothing resolves.
   */
  function resolveBasemapFor(container, doc) {
    var core = self.pwaBasemapStyleCore;
    if (!core) return null;
    var stored = null;
    try {
      stored = localStorage.getItem(core.STORAGE_KEY);
    } catch (err) {
      stored = null;
    }
    return core.resolveBasemap(
      stored,
      readBasemaps(doc),
      container.getAttribute('data-default-basemap-key') || '',
    );
  }

  /**
   * Reveal the blank-canvas notice, and wire its one way out.
   *
   * The national basemaps cover one country each and render blank outside
   * it. On the map page a reader can see the picker and fix it; here they
   * cannot, so the page says what happened and offers the swap. NOT a
   * picker — one escape from a broken state, the same family as the
   * `map-failed` message.
   *
   * The notice stays hidden when the page did not render one, so a caller
   * needs no guard of its own.
   *
   * @param {Object} map The MapLibre map.
   * @param {Element} container The map container, carrying the default key.
   * @param {Document} doc The document.
   * @returns {void}
   */
  function revealBlankNotice(map, container, doc) {
    var notice = doc.getElementById('trip-basemap-blank');
    if (!notice) return;
    var core = self.pwaBasemapStyleCore;
    var catalogue = readBasemaps(doc);
    var defaultKey = container.getAttribute('data-default-basemap-key') || '';
    var fallback = core && core.resolveBasemap(null, catalogue, defaultKey);
    var button = notice.querySelector('[data-blank-switch]');
    if (button && fallback) {
      button.addEventListener('click', function () {
        notice.hidden = true;
        // The layers go with the style; `style.load` reinstalls them, which
        // is why that handler is idempotent.
        map.setStyle(fallback.url);
      });
    } else if (button) {
      // Nothing to switch TO. Saying what happened still beats a silent
      // blank canvas, so the notice is shown without its control.
      button.hidden = true;
    }
    notice.hidden = false;
  }

  /**
   * Ask the canvas whether the basemap painted anything, once it is idle.
   *
   * `idle` and not `load`: before every tile has rendered, "nothing drawn"
   * is indistinguishable from "not drawn YET", which is the one wrong
   * answer available. It binds once and unbinds on the first firing —
   * panning to a genuinely empty corner of a basemap that does cover this
   * area is not the condition this reports.
   *
   * Skipped entirely when the reader is on the site default: that style is
   * global, so a blank canvas under it is a network failure, which
   * `map-failed` and MapLibre's own error path already speak for.
   *
   * @param {Object} map The MapLibre map.
   * @param {Element} container The map container.
   * @param {?Object} basemap The resolved `{key, url}`.
   * @param {Document} doc The document.
   * @returns {void}
   */
  function watchForBlankBasemap(map, container, basemap, doc) {
    var core = self.pwaBasemapStyleCore;
    var defaultKey = container.getAttribute('data-default-basemap-key') || '';
    if (!core || !basemap || basemap.key === defaultKey) return;
    var onIdle = function () {
      map.off('idle', onIdle);
      var drawn;
      try {
        drawn = map.queryRenderedFeatures();
      } catch (err) {
        return;
      }
      if (core.drewNothing(drawn, OUR_SOURCE_IDS)) {
        revealBlankNotice(map, container, doc);
      }
    };
    map.on('idle', onIdle);
  }

  /**
   * Boot the trip map, once, if this page has one.
   *
   * @returns {void}
   */
  async function init() {
    var doc = document;
    var container = doc.querySelector('[data-trip-map]');
    if (!container) return;

    var payload = readPayload(doc);

    if (typeof maplibregl === 'undefined') {
      container.textContent = STRINGS['map-failed'];
      return;
    }

    // SNOW-829: the READER's basemap, not the site default. `resolveStyle`
    // answers with the URL for a native style and a Promise of a rewritten
    // style object for an ESRI one (basemap.at), so it is awaited before
    // the constructor rather than swapped in afterwards — a style set after
    // construction paints the default first and then flashes.
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

    var camera = fitBoundsFor(payload && payload.bounds);
    var map = new maplibregl.Map({
      container: container,
      style: style,
      // `bounds` + `fitBoundsOptions` rather than center/zoom: the
      // snapshot knows exactly what has to be on screen, and a centre plus
      // a guessed zoom would frame a 2 km valley tour and a 40 km traverse
      // identically. map.js's constructor makes the same choice.
      bounds: camera || undefined,
      fitBoundsOptions: { padding: FIT_PADDING },
      attributionControl: { compact: true },
      // SNOW-1212: a READING surface in a scrolling page. A one-finger
      // drag (or a plain wheel) scrolls the page past the map, and two
      // fingers (or ctrl + wheel) move it — MapLibre's cooperative
      // gestures, which show the reader how. The meeting picker is the
      // opposite case, a control, and stays fully interactive.
      cooperativeGestures: true,
    });
    // SNOW-1212: +/− for a reader who cannot pinch, or who would rather
    // not learn the two-finger rule to zoom in on one stretch. No compass:
    // the map never rotates, so there is nothing for it to reset.
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    // Bound to the constructor's return rather than to a `load` handler:
    // `load` never fires when the basemap style fails to fetch, and a trip
    // page that showed no route because a third-party CDN was slow would
    // be a page about nothing. `style.load` fires for the real style AND
    // for a style swapped in later, so the idempotent install is what
    // makes a second firing harmless.
    map.on('style.load', function () {
      installLayers(map, payload);
    });

    // SNOW-1216: the profile's point, drawn on the line, and brought into
    // view when it would land off the canvas.
    doc.addEventListener('trip:route-cursor', function (event) {
      var detail = /** @type {CustomEvent} */ (event).detail || {};
      cursorIndex = typeof detail.index === 'number' ? detail.index : null;
      var point = cursorFeature(payload, cursorIndex);
      var source = map.getSource(CURSOR_SOURCE);
      if (source) source.setData(cursorSourceData(point));
      if (point && !map.getBounds().contains(point.geometry.coordinates)) {
        map.panTo(point.geometry.coordinates);
      }
    });

    // SNOW-829: and notice if that basemap drew nothing here.
    watchForBlankBasemap(map, container, basemap, doc);
  }

  self.pwaTripMapCore = Object.freeze({
    readPayload: readPayload,
    fitBoundsFor: fitBoundsFor,
    routeSourceData: routeSourceData,
    meetingSourceData: meetingSourceData,
    // SNOW-1216. The index-to-place conversion, exported for its test:
    // a wrong answer is a dot on the wrong bit of the line, which nothing
    // else would notice.
    cursorFeature: cursorFeature,
    // Exported for its test rather than for a second caller. It is the one
    // function here that hands MapLibre a structure MapLibre validates at
    // runtime, and getting that structure wrong threw inside installLayers
    // and took the meeting marker out with it — a failure no assertion
    // about the payload arithmetic could ever have seen.
    ensureMeetingIcon: ensureMeetingIcon,
    // SNOW-1019 / SNOW-1212. Exported for its test: the one place that
    // can show this map draws the route as the map does, and that the
    // fall-line arrows stay off it.
    installLayers: installLayers,
    // SNOW-829. The catalogue read and the resolution built on it. The
    // arithmetic itself lives in `basemap_style_core.js` and is tested
    // there; these two are exported because they are what decides which
    // style the constructor is handed, and getting that wrong is a blank
    // map rather than a thrown error.
    readBasemaps: readBasemaps,
    resolveBasemapFor: resolveBasemapFor,
  });

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', init);
    } else {
      init();
    }
  }
})();
