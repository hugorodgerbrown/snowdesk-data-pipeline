// @ts-check
/*
 * static/js/map_3d_core.js — the 3-D terrain map's pure parts (SNOW-1438).
 *
 * static/js/map_3d.js draws /map/3d/, a sandbox behind the map_3d flag: the
 * relief in 3-D with MapLibre's own terrain, and Steep terrain, SAC's ski
 * tours, the reader's own routes and the resorts draped over it
 * (docs/decisions/a-3d-map-is-maplibre-terrain.md). What can be decided
 * without a map or a DOM is here, so Vitest can pin it:
 *
 *   - the elevation source and terrain spec handed to MapLibre;
 *   - the opening camera;
 *   - the box of tours asked for around the camera — a tilted view's own
 *     bounds reach the horizon, far past what /api/ski-tours.geojson
 *     answers (SKI_NETWORK_MAX_SPAN_DEG, 0.5°), so the box is the centre
 *     plus a fixed half-span;
 *   - the bounds of a tapped line, which the camera flies to. A tapped
 *     MapLibre feature is only the drawn fragment (clipped at tile edges)
 *     and a tour arrives as one feature per leg, so the bounds are the
 *     union of every loaded feature carrying the tapped uuid;
 *   - the layer switches' defaults, and FOEN's wildlife areas as swisstopo
 *     WMTS rasters;
 *   - the shared view: the camera and the switches, written to and read
 *     from the page's query string (`?at=lng,lat,zoom,pitch,bearing&layers=…`),
 *     so a copied link opens on the same view for whoever follows it.
 *
 * Exports (frozen `self.pwaMap3dCore`):
 *
 *   DEM_SOURCE_ID / TERRAIN_EXAGGERATION / OPENING_CAMERA / MAX_PITCH / MAX_PIXEL_RATIO
 *   prefetchDemTiles(centre, mapZoom) / tileUrl(template, tile)
 *   skySpec() / sunlightPaint()
 *   TOURS_MIN_ZOOM / TOURS_HALF_SPAN_DEG / COLOURS
 *   demSource(tileUrl)
 *   terrainSpec()
 *   toursBbox(centre, halfSpan?)
 *   lineBounds(geometry)
 *   boundsForUuid(collection, uuid)
 *   LAYER_KEYS / DEFAULT_LAYERS / WILDLIFE_LAYERS
 *   wildlifeTileUrl(layer)
 *   viewQuery(camera, layers)
 *   parseView(search)
 */
(function () {
  'use strict';

  /** The raster-dem source's id. */
  const DEM_SOURCE_ID = 'map-3d-dem';

  /** The elevation tiles' size in pixels, and the deepest zoom asked for. */
  const DEM_TILE_SIZE = 512;
  const DEM_MAX_ZOOM = 15;

  /**
   * How much the relief is stretched upwards. 1 is true scale; a little
   * more reads better on a phone-sized screen, and more than 1.5 starts to
   * make 30° look like 40°, which on a map about steepness is a lie.
   */
  const TERRAIN_EXAGGERATION = 1.3;

  /** Where the page opens: over Saas-Fee, looking up the Allalin. */
  const OPENING_CAMERA = Object.freeze({
    center: Object.freeze([7.87, 46.07]),
    zoom: 12.6,
    pitch: 64,
    bearing: 215,
  });

  /**
   * The canvas's pixel-ratio ceiling. A 3x phone draws 2.25x the pixels of
   * 2x for detail nobody sees on a tilted map, and every frame pays for it.
   */
  const MAX_PIXEL_RATIO = 2;

  /** MapLibre's ceiling is 85; past this the horizon eats the screen. */
  const MAX_PITCH = 80;

  /** Tours are asked for from here up, as on /map/ (ski_tours_core MINZOOM is 9; the box is small). */
  const TOURS_MIN_ZOOM = 10;

  /**
   * Half the side of the box of tours asked for, in degrees. The endpoint
   * refuses a span over 0.5°, so this stays under 0.25.
   */
  const TOURS_HALF_SPAN_DEG = 0.24;

  /**
   * Line and pin colours. MapLibre paint values, so they are hex here
   * (AGENTS.md design-system rule 3 exempts paint properties). They match
   * /map/: SAC's climb and descent blues (route_legs_core), a reader's own
   * route in fuchsia (route_drawing_core) and the resort pin.
   */
  const COLOURS = Object.freeze({
    tourClimb: '#1d4ed8',
    tourDescent: '#38bdf8',
    route: '#c026d3',
    casing: '#ffffff',
    resort: '#1a1a1a',
  });

  /**
   * The sky and the haze toward the horizon: an alpine blue overhead fading
   * to a pale horizon, with the haze kept off the near ground so the
   * foreground stays crisp. MapLibre paint values, so hex here.
   *
   * @returns {Record<string, unknown>}
   */
  function skySpec() {
    return {
      'sky-color': '#5b9bd5',
      'horizon-color': '#dbe9f6',
      'fog-color': '#e6eef7',
      'sky-horizon-blend': 0.8,
      'horizon-fog-blend': 0.9,
      'fog-ground-blend': 0.15,
      'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 10, 1, 14, 0.6],
    };
  }

  /**
   * The sunlight layer's paint: a north-west sun fixed to the map (so the
   * light does not swing as the camera turns), strong enough to model the
   * ridges without hiding swisstopo's hachures under it.
   *
   * @returns {Record<string, unknown>}
   */
  function sunlightPaint() {
    return {
      'hillshade-exaggeration': 0.45,
      'hillshade-illumination-anchor': 'map',
      'hillshade-illumination-direction': 315,
      'hillshade-shadow-color': '#33415c',
      'hillshade-highlight-color': '#ffffff',
      'hillshade-accent-color': '#5a6b80',
    };
  }

  /**
   * The elevation source: Mapterhorn's Terrarium-encoded 512 px tiles.
   * They go to z17 over Switzerland; z15 (about 2 m a pixel) is past what
   * the terrain mesh can show, and each zoom past it quadruples the tiles.
   *
   * @param {string} tileUrl An XYZ template.
   * @returns {{type: 'raster-dem', tiles: string[], encoding: 'terrarium', tileSize: number, maxzoom: number, attribution: string}}
   */
  function demSource(tileUrl) {
    return {
      type: 'raster-dem',
      tiles: [tileUrl],
      encoding: 'terrarium',
      tileSize: DEM_TILE_SIZE,
      maxzoom: DEM_MAX_ZOOM,
      attribution: '<a href="https://mapterhorn.com/attribution" target="_blank" rel="noopener">© Mapterhorn</a>',
    };
  }

  /**
   * The elevation tiles a 3-D view around a point will ask for first, so
   * /map/'s 3D roundel can warm the browser's cache before the page opens.
   * MapLibre draws 512 px terrain about two zooms below the map's own, so
   * this is the 3x3 block at that zoom around the centre.
   *
   * @param {{lng: number, lat: number}} centre
   * @param {number} mapZoom The camera zoom the 3-D map will open at.
   * @returns {Array<{z: number, x: number, y: number}>}
   */
  function prefetchDemTiles(centre, mapZoom) {
    const z = Math.max(0, Math.min(DEM_MAX_ZOOM, Math.round(mapZoom) - 2));
    const n = 2 ** z;
    const lat = Math.max(-85, Math.min(85, centre.lat)) * Math.PI / 180;
    const cx = Math.floor(((centre.lng + 180) / 360) * n);
    const cy = Math.floor(((1 - Math.asinh(Math.tan(lat)) / Math.PI) / 2) * n);
    /** @type {Array<{z: number, x: number, y: number}>} */
    const out = [];
    for (let dy = -1; dy <= 1; dy += 1) {
      for (let dx = -1; dx <= 1; dx += 1) {
        const y = cy + dy;
        if (y < 0 || y >= n) continue;
        out.push({ z, x: (((cx + dx) % n) + n) % n, y });
      }
    }
    return out;
  }

  /**
   * One tile's URL from an XYZ template.
   *
   * @param {string} template
   * @param {{z: number, x: number, y: number}} tile
   * @returns {string}
   */
  function tileUrl(template, tile) {
    return template.replace('{z}', String(tile.z)).replace('{x}', String(tile.x)).replace('{y}', String(tile.y));
  }

  /**
   * The argument to `map.setTerrain`.
   *
   * @returns {{source: string, exaggeration: number}}
   */
  function terrainSpec() {
    return { source: DEM_SOURCE_ID, exaggeration: TERRAIN_EXAGGERATION };
  }

  /**
   * The `?bbox=` for the tours around a point, as `west,south,east,north`.
   *
   * @param {{lng: number, lat: number}} centre
   * @param {number} [halfSpan] Degrees either side; defaults to TOURS_HALF_SPAN_DEG.
   * @returns {string}
   */
  function toursBbox(centre, halfSpan) {
    const h = Math.min(typeof halfSpan === 'number' ? halfSpan : TOURS_HALF_SPAN_DEG, 0.25);
    const round = (/** @type {number} */ n) => Math.round(n * 1e5) / 1e5;
    return [
      round(centre.lng - h),
      round(Math.max(centre.lat - h, -90)),
      round(centre.lng + h),
      round(Math.min(centre.lat + h, 90)),
    ].join(',');
  }

  /**
   * The bounds of a line, `[[west, south], [east, north]]`, or null for a
   * geometry with no line in it.
   *
   * @param {{type: string, coordinates: any}|null|undefined} geometry
   * @returns {[[number, number], [number, number]]|null}
   */
  function lineBounds(geometry) {
    if (!geometry) return null;
    /** @type {number[][]} */
    let points = [];
    if (geometry.type === 'LineString') points = geometry.coordinates;
    else if (geometry.type === 'MultiLineString') points = [].concat(...geometry.coordinates);
    else return null;
    if (!Array.isArray(points) || points.length === 0) return null;
    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;
    for (const p of points) {
      if (p[0] < west) west = p[0];
      if (p[0] > east) east = p[0];
      if (p[1] < south) south = p[1];
      if (p[1] > north) north = p[1];
    }
    return [[west, south], [east, north]];
  }

  /**
   * The union of the line bounds of every feature in a collection whose
   * `uuid` is the one given, or null when none has a line.
   *
   * @param {{features?: Array<{geometry?: any, properties?: any}>}|null|undefined} collection
   * @param {unknown} uuid
   * @returns {[[number, number], [number, number]]|null}
   */
  function boundsForUuid(collection, uuid) {
    if (!collection || !Array.isArray(collection.features) || !uuid) return null;
    /** @type {[[number, number], [number, number]]|null} */
    let out = null;
    for (const feature of collection.features) {
      if (!feature.properties || feature.properties.uuid !== uuid) continue;
      const b = lineBounds(feature.geometry);
      if (!b) continue;
      out = out
        ? [
          [Math.min(out[0][0], b[0][0]), Math.min(out[0][1], b[0][1])],
          [Math.max(out[1][0], b[1][0]), Math.max(out[1][1], b[1][1])],
        ]
        : b;
    }
    return out;
  }

  /** The page's layer switches, in the order the link lists them. */
  const LAYER_KEYS = Object.freeze(['steep', 'tours', 'wildlife']);

  /** Each switch's state when the link does not say: wildlife off, as on /map/. */
  const DEFAULT_LAYERS = Object.freeze({ steep: true, tours: true, wildlife: false });

  /**
   * FOEN's wildlife areas, as swisstopo's WMTS draws them: the wildlife rest
   * zones and the federal game reserves. Open government data, public and
   * token-less; the CSP already allows wmts.geo.admin.ch (SNOW-1260).
   */
  const WILDLIFE_LAYERS = Object.freeze([
    'ch.bafu.wrz-wildruhezonen_portal',
    'ch.bafu.wrz-jagdbanngebiete_select',
  ]);

  /**
   * The XYZ template of one swisstopo WMTS layer in Web Mercator.
   *
   * @param {string} layer The geo.admin layer id.
   * @returns {string}
   */
  function wildlifeTileUrl(layer) {
    return `https://wmts.geo.admin.ch/1.0.0/${layer}/default/current/3857/{z}/{x}/{y}.png`;
  }

  /**
   * The query string that reproduces a view: `at=lng,lat,zoom,pitch,bearing`
   * and `layers=` listing the switches that are on (empty when none is).
   *
   * @param {{center: {lng: number, lat: number}, zoom: number, pitch: number, bearing: number}} camera
   * @param {Record<string, boolean>} layers
   * @returns {string} Without the leading `?`.
   */
  function viewQuery(camera, layers) {
    const fixed = (/** @type {number} */ n, /** @type {number} */ d) => String(Number(n.toFixed(d)));
    // MapLibre's centre can sit in another world copy past ±180; wrap it
    // back, or parseView would refuse the link.
    const lng = ((((camera.center.lng + 180) % 360) + 360) % 360) - 180;
    const at = [
      fixed(lng, 5),
      fixed(camera.center.lat, 5),
      fixed(camera.zoom, 2),
      fixed(camera.pitch, 1),
      fixed(camera.bearing, 1),
    ].join(',');
    const on = LAYER_KEYS.filter((key) => layers[key]);
    return `at=${at}&layers=${on.join(',')}`;
  }

  /**
   * Read a view back from a query string. Anything malformed or out of
   * range is dropped rather than guessed at, so a mangled link still opens
   * the map.
   *
   * @param {string} search The page's `location.search`.
   * @returns {{camera: {center: [number, number], zoom: number, pitch: number, bearing: number}|null, layers: Record<string, boolean>}}
   */
  function parseView(search) {
    const params = new URLSearchParams(search);
    /** @type {{center: [number, number], zoom: number, pitch: number, bearing: number}|null} */
    let camera = null;
    const at = params.get('at');
    if (at) {
      const n = at.split(',').map(Number);
      const ok = n.length === 5 && n.every(Number.isFinite)
        && Math.abs(n[0]) <= 180 && Math.abs(n[1]) <= 90
        && n[2] >= 0 && n[2] <= 22 && n[3] >= 0 && n[3] <= MAX_PITCH;
      if (ok) camera = { center: [n[0], n[1]], zoom: n[2], pitch: n[3], bearing: n[4] };
    }
    /** @type {Record<string, boolean>} */
    const layers = { ...DEFAULT_LAYERS };
    const listed = params.get('layers');
    if (listed !== null) {
      const on = listed.split(',');
      for (const key of LAYER_KEYS) layers[key] = on.includes(key);
    }
    return { camera, layers };
  }

  self.pwaMap3dCore = Object.freeze({
    DEM_SOURCE_ID: DEM_SOURCE_ID,
    TERRAIN_EXAGGERATION: TERRAIN_EXAGGERATION,
    OPENING_CAMERA: OPENING_CAMERA,
    MAX_PITCH: MAX_PITCH,
    MAX_PIXEL_RATIO: MAX_PIXEL_RATIO,
    prefetchDemTiles: prefetchDemTiles,
    skySpec: skySpec,
    sunlightPaint: sunlightPaint,
    tileUrl: tileUrl,
    TOURS_MIN_ZOOM: TOURS_MIN_ZOOM,
    TOURS_HALF_SPAN_DEG: TOURS_HALF_SPAN_DEG,
    COLOURS: COLOURS,
    demSource: demSource,
    terrainSpec: terrainSpec,
    toursBbox: toursBbox,
    lineBounds: lineBounds,
    boundsForUuid: boundsForUuid,
    LAYER_KEYS: LAYER_KEYS,
    DEFAULT_LAYERS: DEFAULT_LAYERS,
    WILDLIFE_LAYERS: WILDLIFE_LAYERS,
    wildlifeTileUrl: wildlifeTileUrl,
    viewQuery: viewQuery,
    parseView: parseView,
  });
})();
