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
 *   routeSlopeSourceData(payload) → its per-segment slope collection
 *   isSlopeColoured(payload)      → whether the flat line is suppressed
 *   meetingSourceData(payload)    → the Point FeatureCollection
 *   installLayers(map, payload)   → adds the sources and layers (its test)
 *   profileFor(payload)           → the profile data, or null
 *   drawProfileRange(profile, doc) → writes the profile's scale caption
 *   readBasemaps(doc)             → the {key: url} catalogue, or null
 *   resolveBasemapFor(el, doc)    → {key, url} for this reader, or null
 *
 * SNOW-1019 took the crux rings, fall-line arrows and no-fall passage
 * split line off this map, as off the map page: the crux is deferred to a
 * later ticket, the bank ribbon replaced the arrows, and the passages are
 * drawn as bars on the map page's rail two. The snapshot still carries
 * all three records.
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

  // The English fallbacks are the only copy of these strings a reader of
  // this file can see, so they double as documentation of what each key
  // means. `i18n_strings.js` is loaded first by `_trip_map.html`, in
  // document order, so `self.pwaStrings` is here.
  var STRINGS = self.pwaStrings.read('trip-strings-template', {
    'meeting-point': 'Meeting point',
    'elevation-profile': 'Elevation profile of the route',
    // The profile's y-axis is fitted to this track's own lowest and
    // highest point, so the curve says nothing without the pair that
    // bounds it. The map page's route popup carried the same string until
    // SNOW-1018 moved its profile to rail one, which captions none.
    'elevation-range': '%(low)s–%(high)s m',
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
   * The MapLibre `step` expression painting a segment by its slope class.
   *
   * Built from the core's CLASSES rather than written out, so this page
   * and the map page cannot drift: both read one list. A `step` takes the
   * first colour, then a (stop, colour) pair per class after it — the
   * stops are the CLASS INDICES the core assigns, not angles, because the
   * angle was classified server-side and the expression only looks the
   * colour up.
   *
   * @returns {Array<*>|string} The expression, or the flat route colour
   *   when the core is unavailable and there is nothing to classify by.
   */
  function slopeColourExpression() {
    var core = self.pwaRouteSlopeCore;
    if (!core) return ROUTE_LINE_COLOUR;
    var expression = ['step', ['get', 'slope_class'], core.CLASSES[0].hex];
    for (var i = 1; i < core.CLASSES.length; i += 1) {
      expression.push(i, core.CLASSES[i].hex);
    }
    return expression;
  }

  /**
   * The trip's track as one two-point LineString per sampled segment.
   *
   * SNOW-962. The snapshot carries its own copy of the terrain record
   * (`Trip.slope_samples`), and `route_slope_core.js` turns the wire form
   * of it into the segments MapLibre paints — the same module, the same
   * palette and the same three states the map page's routes layer uses,
   * so one track reads identically on both surfaces.
   *
   * An empty collection whenever there is nothing to colour: no core
   * loaded, or a trip nothing has sampled. Never null — `setData` throws
   * on one.
   *
   * @param {?Object} payload
   * @returns {Object} A FeatureCollection, possibly empty.
   */
  function routeSlopeSourceData(payload) {
    var core = self.pwaRouteSlopeCore;
    var feature = payload && payload.route;
    if (!core || !feature) return { type: 'FeatureCollection', features: [] };
    return { type: 'FeatureCollection', features: core.segmentFeatures(feature) };
  }

  /**
   * Whether the trip's line is drawn segment by segment rather than flat.
   *
   * The flat line has to be SUPPRESSED when it is, or the two paint over
   * each other and the route colour shows through at every butt-capped
   * join — the same reason `map.js` filters its own flat layer on
   * `['!', ['has', 'slope']]`.
   *
   * Asked of the produced segments rather than of the property, because
   * the segments are what actually draw: with no core loaded the property
   * is there and nothing is painted from it, and suppressing the flat
   * line on that would leave the page with no track at all.
   *
   * @param {?Object} payload
   * @returns {boolean} True when the slope layers will draw the track.
   */
  function isSlopeColoured(payload) {
    return routeSlopeSourceData(payload).features.length > 0;
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
      // SNOW-962: the same record the line above is coloured from, so the
      // two pictures of one track agree. Undefined for a trip nothing has
      // sampled, which draws the plain curve it always did.
      slope: (payload && payload.route && payload.route.properties || {}).slope,
    });
    if (!svg) return;
    host.appendChild(svg);
    drawProfileRange(profile, doc);
  }

  /**
   * Caption the drawn profile with the elevation range it is scaled to.
   *
   * SNOW-840. The chart's y-axis is fitted to THIS track's highest and
   * lowest point, so an unlabelled curve's height carries no quantity: two
   * routes 200 m and 2000 m apart top to bottom draw the same picture. The
   * map page's route popup has always captioned it for that reason, and
   * this is that caption on the trip page's own card.
   *
   * Only called once a chart has actually been drawn — a track with no
   * elevation anywhere draws nothing, and a range under nothing would be
   * a scale for a picture that is not there.
   *
   * NO DURATION SEGMENT, unlike the popup's caption. The popup reads
   * ``duration_s`` off the route feature; a Trip's snapshot does not copy
   * it (see Trip's docstring for what the snapshot carries), so there is
   * no elapsed time on this page to state and inventing one from distance
   * and ascent would be a guess dressed as a measurement.
   *
   * @param {Object} profile The drawn profile, from `readProfile`.
   * @param {Document} doc The document to write into.
   * @returns {void}
   */
  function drawProfileRange(profile, doc) {
    var host = doc.querySelector('[data-trip-profile-range]');
    if (!host) return;
    if (typeof profile.minEle !== 'number' || typeof profile.maxEle !== 'number') {
      return;
    }
    // UNGROUPED, and deliberately: the design draws "1,680–3,020 m", the
    // spelling the map page's route popup used for the same measurement
    // until SNOW-1018 retired it.
    host.textContent = self.pwaStrings.interpolate(STRINGS['elevation-range'], {
      low: String(Math.round(profile.minEle)),
      high: String(Math.round(profile.maxEle)),
    });
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
    // SNOW-962: the flat line is drawn only where the slope layers below
    // will not. The casing above stays either way — it is what makes one
    // stroke readable over both a pale basemap and a dark one, and that
    // is as true of six colours as of one.
    var coloured = isSlopeColoured(payload);
    if (!coloured) {
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
    }

    if (coloured) {
      var slopeCore = self.pwaRouteSlopeCore;
      map.addSource('trip-route-slopes', {
        type: 'geojson',
        data: routeSlopeSourceData(payload),
      });
      map.addLayer({
        id: 'trip-route-slope-line',
        type: 'line',
        source: 'trip-route-slopes',
        // `step` needs a number and an unknown segment has no class at
        // all, so unknowns are excluded here rather than left to fall on
        // the expression's first stop — which is the GENTLE colour, and
        // painting unsurveyed ground as gentle is the one outcome this
        // whole feature exists to prevent.
        filter: ['!=', ['get', 'unknown'], true],
        // Butt caps, not round: a round cap on a 25 m segment overlaps
        // its neighbour and smears each colour a few metres into the next.
        layout: { 'line-cap': 'butt', 'line-join': 'round' },
        paint: {
          'line-color': slopeColourExpression(),
          // The same widths as the flat line: a sampled track and an
          // unsampled one are the same object and must read as the same
          // weight of thing.
          'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 4, 16, 7],
        },
      });
      map.addLayer({
        id: 'trip-route-slope-unknown',
        type: 'line',
        source: 'trip-route-slopes',
        filter: ['==', ['get', 'unknown'], true],
        layout: { 'line-cap': 'butt', 'line-join': 'round' },
        paint: {
          'line-color': (slopeCore || {}).UNKNOWN_COLOUR || '#94a3b8',
          'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 4, 16, 7],
          // In line-widths, so the dash keeps its proportions as the line
          // thickens with zoom.
          'line-dasharray': [2, 1.5],
        },
      });
    }

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
   * omitted source. `trip-route-slopes` (SNOW-962) is installed only for
   * a SAMPLED trip, which is the subset that would have lost the notice.
   */
  var OUR_SOURCE_IDS = [
    'trip-route', 'trip-route-slopes', 'trip-meeting',
  ];

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
    drawProfile(payload, doc);

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

    // SNOW-829: and notice if that basemap drew nothing here.
    watchForBlankBasemap(map, container, basemap, doc);
  }

  self.pwaTripMapCore = Object.freeze({
    readPayload: readPayload,
    fitBoundsFor: fitBoundsFor,
    routeSourceData: routeSourceData,
    // SNOW-962. Pure functions of the payload, and the pair that decides
    // whether the flat line is drawn at all — a wrong answer there is
    // either a track painted twice or no track at all, neither of which
    // any server-side assertion can see.
    routeSlopeSourceData: routeSlopeSourceData,
    isSlopeColoured: isSlopeColoured,
    meetingSourceData: meetingSourceData,
    profileFor: profileFor,
    // SNOW-840. Writes into the DOM rather than returning a value, so it
    // is exported to be exercised in jsdom: the caption is the only thing
    // that gives the drawn curve a quantity, and a silently-empty one
    // looks exactly like a track that carries no elevation.
    drawProfileRange: drawProfileRange,
    // Exported for its test rather than for a second caller. It is the one
    // function here that hands MapLibre a structure MapLibre validates at
    // runtime, and getting that structure wrong threw inside installLayers
    // and took the meeting marker out with it — a failure no assertion
    // about the payload arithmetic could ever have seen.
    ensureMeetingIcon: ensureMeetingIcon,
    // SNOW-1019. Exported for its test: the one place that can show the
    // crux rings and fall-line arrows stay off this map.
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
