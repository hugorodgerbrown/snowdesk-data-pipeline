// @ts-check
/*
 * static/js/map_3d_flight.js — a 3D fly-past on /map/ (SNOW-1466).
 *
 * Loaded after map_view_3d.js, for a reader in the map_3d flag
 * (public/map.html). It flies the camera along a route through /map/'s own
 * 3D view, one frame at a time, so the promo video's recorder
 * (bin/record-3d-flyby) can screenshot each frame once its tiles are in
 * (docs/decisions/a-fly-past-is-rendered-frame-by-frame.md). The path
 * itself is map_3d_flight_core.js; this is only the part that needs a map.
 *
 * Publishes window.pwaMap3dFlight:
 *
 *   load(geojson, options = {}) — builds the flight from the first
 *     LineString or MultiLineString in a Feature, FeatureCollection or
 *     bare geometry, with options.flight merged over the feature's
 *     properties.flight as the core's options. Enters 3D through
 *     window.pwaMapView.beginTest(), which saves the camera and the view and
 *     rests the watchdog, and throws if 3D cannot be entered here, naming
 *     canTest()'s reason. Adds the `map-3d-flight` source and its casing,
 *     line and head layers on top, in the route colours
 *     window.pwaRouteDrawingCore publishes. options.chrome = false sets
 *     body.map-recording, which lays the map over the whole page with no
 *     controls (src/css/main.css); the default leaves the page alone.
 *   frame(t) — draws the frame t of the way through (0..1) and resolves
 *     with its phase once the map is idle with every tile loaded (or every
 *     tile has stayed loaded for QUIET_MS, for a camera where MapLibre
 *     never goes idle), jumping
 *     a second time if MapLibre moved the camera to keep it out of ground
 *     whose height had not loaded yet. If that
 *     has not happened within FRAME_CAP_MS (or load's options.capMs, which
 *     the recorder raises for software rendering) it rejects, so a half-loaded
 *     frame cannot reach a video. A reveal frame fits the route's bounds
 *     with map.cameraForBounds at the core's pitch and bearing, and blends
 *     the centre and zoom towards that by the frame's fit weight.
 *   unload() — removes the layers and the source, clears map-recording
 *     and puts the view and camera back (pwaMapView.endTest()).
 *
 * The page shows no words of its own, so nothing here is translated.
 */
(function map3dFlightInit() {
  'use strict';

  const core = self.pwaMap3dFlightCore;
  const mapState = window.snowdeskMapState;
  const map = mapState ? mapState.map : null;
  if (!core || !map) return;

  /** The flight's source and its layers, bottom to top. */
  const SOURCE_ID = 'map-3d-flight';
  const LAYER_IDS = Object.freeze(['map-3d-flight-casing', 'map-3d-flight-line', 'map-3d-flight-head']);
  /** How long a frame may take to settle before it fails. */
  const FRAME_CAP_MS = 60000;
  /**
   * A frame whose tiles have all stayed in this long has settled, though
   * the map never goes idle (settled()); checked this often.
   */
  const QUIET_MS = 500;
  const QUIET_POLL_MS = 250;
  /** Room left round the route when the reveal fits it, in CSS pixels. */
  const REVEAL_PADDING = 120;
  /** The body class a recording sets. */
  const RECORDING_CLASS = 'map-recording';

  /** @type {any} The flight loaded now, if one is. */
  let flight = null;
  /** @type {any} The source's data for the last frame drawn. */
  let lastData = null;
  /** How long a frame may take to settle for this flight. */
  let capMs = FRAME_CAP_MS;

  /**
   * The first line in a GeoJSON object, as {coords, properties}.
   *
   * @param {any} geojson
   * @returns {{coords: any, properties: any} | null}
   */
  function firstLine(geojson) {
    if (!geojson || typeof geojson !== 'object') return null;
    if (geojson.type === 'FeatureCollection') {
      for (const feature of geojson.features || []) {
        const found = firstLine(feature);
        if (found) return found;
      }
      return null;
    }
    if (geojson.type === 'Feature') {
      const found = firstLine(geojson.geometry);
      return found ? { coords: found.coords, properties: geojson.properties || {} } : null;
    }
    if (geojson.type === 'LineString' || geojson.type === 'MultiLineString') {
      return { coords: geojson.coordinates, properties: {} };
    }
    return null;
  }

  /** The route colours, from the module that owns them. */
  function colours() {
    const routeCore = /** @type {any} */ (window).pwaRouteDrawingCore;
    if (!routeCore) throw new Error('pwaRouteDrawingCore is not loaded');
    return { line: routeCore.ROUTE_LINE_COLOUR, casing: routeCore.ROUTE_CASING_COLOUR };
  }

  /** Take the flight's layers and source off the map, if they are there. */
  function removeLayers() {
    for (const id of [...LAYER_IDS].reverse()) if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(SOURCE_ID)) map.removeSource(SOURCE_ID);
  }

  /** Put the flight's source and layers on the map, on top. */
  function addLayers() {
    removeLayers();
    const { line, casing } = colours();
    map.addSource(SOURCE_ID, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    const isLine = ['==', ['geometry-type'], 'LineString'];
    map.addLayer({
      id: LAYER_IDS[0], type: 'line', source: SOURCE_ID, filter: isLine,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': casing, 'line-width': 9, 'line-opacity': 0.8 },
    });
    map.addLayer({
      id: LAYER_IDS[1], type: 'line', source: SOURCE_ID, filter: isLine,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: { 'line-color': line, 'line-width': 5.5 },
    });
    map.addLayer({
      id: LAYER_IDS[2], type: 'circle', source: SOURCE_ID, filter: ['==', ['geometry-type'], 'Point'],
      paint: {
        'circle-radius': 7,
        'circle-color': line,
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 3,
        'circle-pitch-alignment': 'map',
      },
    });
  }

  /**
   * Put the layers back if a setStyle took them, and draw the last frame's
   * data. Entering 3D from another basemap swaps the style after load()
   * has added the layers, and the swap wipes them (SNOW-1466).
   */
  function draw() {
    if (!flight) return;
    if (!map.getSource(SOURCE_ID)) {
      try {
        addLayers();
      } catch (_err) {
        // The new style is still loading; its style.load draws again.
        return;
      }
    }
    const source = map.getSource(SOURCE_ID);
    if (source && lastData) source.setData(lastData);
  }

  /**
   * The source's data for a frame: the drawn line, if there is one, and the head.
   *
   * @param {any} frame
   */
  function frameData(frame) {
    const features = [];
    if (frame.drawn.length >= 2) {
      features.push({ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: frame.drawn } });
    }
    features.push({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: frame.head } });
    return { type: 'FeatureCollection', features };
  }

  /**
   * The camera to jump to for a frame: the core's, or in the reveal its
   * blend towards the fitted bounds.
   *
   * @param {any} frame
   * @returns {{center: [number, number], zoom: number, pitch: number, bearing: number}}
   */
  function cameraFor(frame) {
    const cam = frame.camera;
    if (!frame.fit || frame.fit.weight <= 0) return cam;
    const fitted = map.cameraForBounds(frame.fit.bounds, {
      padding: REVEAL_PADDING, pitch: cam.pitch, bearing: cam.bearing,
    });
    if (!fitted) return cam;
    const c = fitted.center;
    const centre = Array.isArray(c) ? c : [c.lng, c.lat];
    const w = frame.fit.weight;
    return {
      center: [cam.center[0] + (centre[0] - cam.center[0]) * w, cam.center[1] + (centre[1] - cam.center[1]) * w],
      zoom: cam.zoom + (fitted.zoom - cam.zoom) * w,
      pitch: cam.pitch,
      bearing: cam.bearing,
    };
  }

  /**
   * Let MapLibre read the centre's height again.
   *
   * MapLibre 4.7 sets `_elevationFreeze` when a camera ease starts over
   * terrain and clears it only at the end of an ease asked to freeze, or of a
   * gesture. After any other ease over terrain (the page's own camera
   * moves) it stays set, so the centre keeps the height it had then, and a
   * jump to the route draws the camera at that height: under the mountain,
   * the ground streaked and the sky missing. A reader's first drag clears
   * it; a recording makes none.
   * The property is MapLibre's own, so this is checked against the vendored
   * version (static/js/maplibre-gl.min.js) and does nothing if it is gone.
   */
  function thawElevation() {
    const internal = /** @type {any} */ (map);
    if (internal._elevationFreeze === true) internal._elevationFreeze = false;
  }

  /**
   * Whether the map's pitch or zoom is not the camera it was sent to.
   *
   * @param {{zoom: number, pitch: number}} camera
   * @returns {boolean}
   */
  function drifted(camera) {
    return Math.abs(map.getPitch() - camera.pitch) > 0.01 || Math.abs(map.getZoom() - camera.zoom) > 0.001;
  }

  /**
   * Whether every source in the style has all its tiles in.
   *
   * @returns {boolean}
   */
  function tilesIn() {
    const style = map.getStyle();
    const ids = Object.keys((style && style.sources) || {});
    return ids.every((id) => map.isSourceLoaded(id)) && map.areTilesLoaded();
  }

  /**
   * What a frame that has not settled is still waiting for, for its error:
   * the sources with tiles still loading, then the map's own state.
   *
   * @param {{renders: number, idles: number}} seen Events since the jump.
   * @returns {string}
   */
  function waitingOn(seen) {
    const style = map.getStyle();
    const loading = Object.keys((style && style.sources) || {}).filter((id) => !map.isSourceLoaded(id));
    const state = `loaded=${map.loaded()} moving=${map.isMoving()} tiles=${map.areTilesLoaded()}`
      + ` renders=${seen.renders} idles=${seen.idles}`;
    if (loading.length) return `tiles still loading: ${loading.join(', ')}; ${state}`;
    return `every source loaded; ${state}`;
  }

  /**
   * Resolve once the map has drawn the frame with every tile loaded; reject
   * after the cap.
   *
   * Settled means an idle with every tile loaded, or every tile staying
   * loaded for QUIET_MS, checked every QUIET_POLL_MS whatever the map is
   * doing. MapLibre fires idle only once a render leaves nothing to do, and
   * at some cameras over terrain it never gets there, so idle never comes
   * (frame 349 of the Breithorn, 7 Nov 2026, on a Mac GPU and under
   * SwiftShader alike). The tiles are what a frame waits for; a label still
   * fading is not worth failing the run. The wait starts only after a
   * render since the jump, the one that asks for the new camera's tiles,
   * and every tile load asks for another, so a frame whose tiles have been
   * in for QUIET_MS has been drawn with them.
   *
   * @param {number} t For the error.
   * @returns {Promise<void>}
   */
  function settled(t) {
    return new Promise((resolve, reject) => {
      let done = false;
      let quietSince = 0;
      const seen = { renders: 0, idles: 0 };
      /** @param {Error} [error] */
      const finish = (error) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        clearInterval(poll);
        map.off('idle', onIdle);
        map.off('render', onRender);
        if (error) reject(error);
        else resolve();
      };
      const timer = setTimeout(() => {
        finish(new Error(`frame t=${t} not settled after ${capMs / 1000} s (${waitingOn(seen)})`));
      }, capMs);
      const poll = setInterval(() => {
        // Until the map has drawn once since the jump it has not asked for
        // the new camera's tiles, so the old ones would count as in.
        if (!seen.renders || !tilesIn()) {
          quietSince = 0;
          return;
        }
        if (!quietSince) quietSince = Date.now();
        else if (Date.now() - quietSince >= QUIET_MS) finish();
      }, QUIET_POLL_MS);
      const onIdle = () => {
        seen.idles += 1;
        if (map.areTilesLoaded()) finish();
      };
      const onRender = () => {
        seen.renders += 1;
      };
      map.on('idle', onIdle);
      map.on('render', onRender);
      map.triggerRepaint();
    });
  }

  /**
   * Load a route to fly.
   *
   * @param {any} geojson
   * @param {{chrome?: boolean, flight?: any, capMs?: number}} [options]
   * @returns {{length: number, phases: readonly string[]}}
   */
  function load(geojson, options = {}) {
    const line = firstLine(geojson);
    if (!line) throw new Error('No LineString or MultiLineString to fly');
    // The caller's options go over the route's own, so a recorder's
    // seconds do not drop the route's camera settings.
    const built = core.buildFlight(line.coords, { ...(line.properties.flight || {}), ...(options.flight || {}) });
    const view = /** @type {any} */ (window).pwaMapView;
    if (!view) throw new Error('3D is not available here: no 3D view on this page');
    if (!flight && !view.beginTest()) {
      const why = view.canTest();
      throw new Error(`3D is not available here: ${(why && why.reason) || 'the view would not enter 3D'}`);
    }
    flight = built;
    capMs = options.capMs && options.capMs > 0 ? options.capMs : FRAME_CAP_MS;
    addLayers();
    if (options.chrome === false) {
      document.body.classList.add(RECORDING_CLASS);
      map.resize();
    } else if (document.body.classList.contains(RECORDING_CLASS)) {
      document.body.classList.remove(RECORDING_CLASS);
      map.resize();
    }
    return { length: built.length, phases: core.PHASES };
  }

  /**
   * Draw the frame t of the way through, once its tiles are in.
   *
   * @param {number} t
   * @returns {Promise<{phase: string}>}
   */
  async function frame(t) {
    if (!flight) throw new Error('No flight loaded');
    const f = core.flightFrame(flight, t);
    lastData = frameData(f);
    draw();
    const camera = cameraFor(f);
    thawElevation();
    map.jumpTo(camera);
    await settled(t);
    // A basemap swap that landed while this frame settled took the line
    // with it; draw it again on the new style before the screenshot.
    if (!map.getSource(SOURCE_ID)) {
      draw();
      await settled(t);
    }
    // MapLibre keeps the camera out of the ground by lowering its pitch and
    // zoom, judged against the centre's height at the jump. After a long
    // jump that height is the old centre's, or 0 before its tile is in, so
    // the camera can be pushed down. Once settled the height is right, and
    // the same jump lands where it should (SNOW-1466).
    if (drifted(camera)) {
      map.jumpTo(camera);
      await settled(t);
    }
    return { phase: f.phase };
  }

  /** Take the flight off the map and put the view and camera back. */
  function unload() {
    removeLayers();
    if (document.body.classList.contains(RECORDING_CLASS)) {
      document.body.classList.remove(RECORDING_CLASS);
      map.resize();
    }
    if (flight) {
      flight = null;
      lastData = null;
      const view = /** @type {any} */ (window).pwaMapView;
      if (view) view.endTest({});
    }
  }

  map.on('style.load', draw);

  window.pwaMap3dFlight = Object.freeze({ load, frame, unload, FRAME_CAP_MS, QUIET_MS });
})();
