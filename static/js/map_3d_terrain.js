// @ts-check
/*
 * static/js/map_3d_terrain.js — MapLibre terrain, installed on a map
 * (SNOW-1449).
 *
 * Shared by the 3-D sandbox at /map/3d/ (map_3d.js) and /map/'s 3-D view
 * (map_view_3d.js), so the two draw the same relief from the same tiles
 * (docs/decisions/a-3d-map-is-maplibre-terrain.md). What it owns:
 *
 *   - THE ELEVATION PROTOCOL. `map3ddem://` answered by map_3d_core's
 *     loader: Mapterhorn (or our copy of it) first, AWS's Terrarium tiles
 *     when it fails, an AWS tile enlarged nearest-neighbour to 512 px.
 *     Registered once per page whoever asks: MapLibre keeps one handler
 *     per protocol name.
 *   - THE TERRAIN. The elevation source, setTerrain, the sky and the
 *     north-west sunlight hillshade under the style's first label layer.
 *     installTerrain is idempotent, so a caller can run it after every
 *     setStyle without counting; removeTerrain takes the relief and the
 *     sunlight away and keeps the source, so a return costs nothing.
 *   - THE PREFETCH. The elevation tiles a 3-D view around a camera asks
 *     for first, fetched into the browser's cache once per view, so the
 *     relief is there when 3-D opens (map_3d_link.js and the 3D roundel).
 *
 * Exports (frozen `self.pwaMap3dTerrain`):
 *
 *   SUNLIGHT_LAYER
 *   registerDemProtocol(maplibre, demTileUrl)
 *   installTerrain(map)
 *   removeTerrain(map)
 *   hasTerrain(map)
 *   createPrefetcher(template)
 */
(function () {
  'use strict';

  const core = self.pwaMap3dCore;
  if (!core) return;

  /** The sunlight hillshade's layer id. */
  const SUNLIGHT_LAYER = 'map-3d-sunlight';

  /** @type {WeakSet<object>} The MapLibre namespaces the protocol is on. */
  const registered = new WeakSet();

  /**
   * Enlarge a 256 px AWS tile to the 512 px the source declares, without
   * blending: Terrarium heights are encoded across the three channels.
   *
   * @param {Blob} blob
   * @returns {Promise<ImageBitmap>}
   */
  async function enlarge(blob) {
    const size = core.DEM_TILE_SIZE;
    const image = await createImageBitmap(blob);
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = /** @type {CanvasRenderingContext2D} */ (canvas.getContext('2d'));
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(image, 0, 0, size, size);
    image.close();
    return createImageBitmap(canvas);
  }

  /**
   * Answer `map3ddem://` with Mapterhorn's tiles, AWS's when they fail.
   * Both are fetched without credentials, as the prefetch fetches them, so
   * a prefetched tile is answered from the browser's cache. A second call
   * for the same MapLibre does nothing.
   *
   * @param {any} maplibre The `maplibregl` namespace.
   * @param {string} demTileUrl The primary tiles' XYZ template.
   * @returns {boolean} Whether this call registered it.
   */
  function registerDemProtocol(maplibre, demTileUrl) {
    if (!maplibre || registered.has(maplibre)) return false;
    registered.add(maplibre);
    const loader = core.createDemLoader({
      primary: demTileUrl || '',
      fallback: core.DEM_FALLBACK_TILE_URL,
      fetchTile: (/** @type {string} */ url, /** @type {AbortSignal} */ signal) =>
        fetch(url, { signal, credentials: 'omit' }),
      enlarge,
    });
    maplibre.addProtocol(
      core.DEM_PROTOCOL,
      /**
       * @param {{url: string}} params
       * @param {AbortController} abortController
       */
      (params, abortController) => {
        const tile = core.parseDemUrl(params.url);
        if (!tile) return Promise.reject(new Error(`map_3d: not an elevation url: ${params.url}`));
        return loader.load(tile, abortController.signal);
      },
    );
    return true;
  }

  /**
   * Whether the map is drawing the relief now.
   *
   * @param {any} map
   * @returns {boolean}
   */
  function hasTerrain(map) {
    const terrain = typeof map.getTerrain === 'function' ? map.getTerrain() : null;
    return !!terrain && terrain.source === core.DEM_SOURCE_ID;
  }

  /**
   * Lift the map into relief: the elevation source, the terrain, the sky
   * and the sunlight. Each part is added only if missing, so a second call,
   * or one after a setStyle wiped some of it, puts back exactly what is
   * gone.
   *
   * @param {any} map
   * @returns {void}
   */
  function installTerrain(map) {
    if (!map.getSource(core.DEM_SOURCE_ID)) map.addSource(core.DEM_SOURCE_ID, core.demSource());
    if (!hasTerrain(map)) map.setTerrain(core.terrainSpec());
    map.setSky(core.skySpec());
    if (!map.getLayer(SUNLIGHT_LAYER)) {
      // Under the basemap's labels and over its hachures, so the relief
      // reads as form. It shades from the terrain's own elevation source,
      // so it downloads nothing more.
      const style = map.getStyle();
      const layers = (style && style.layers) || [];
      const firstLabel = layers.find((/** @type {any} */ l) => l.type === 'symbol');
      map.addLayer(
        { id: SUNLIGHT_LAYER, type: 'hillshade', source: core.DEM_SOURCE_ID, paint: core.sunlightPaint() },
        firstLabel ? firstLabel.id : undefined,
      );
    }
  }

  /**
   * Lay the map flat again: no relief and no sunlight. The elevation
   * source stays, so coming back to 3-D re-reads nothing.
   *
   * @param {any} map
   * @returns {void}
   */
  function removeTerrain(map) {
    if (hasTerrain(map)) map.setTerrain(null);
    if (map.getLayer(SUNLIGHT_LAYER)) map.removeLayer(SUNLIGHT_LAYER);
  }

  /**
   * A prefetch of the elevation tiles a 3-D view around a camera asks for
   * first (map_3d_core.prefetchDemTiles). Once per view: a call for the
   * same tiles as the last one fetches nothing.
   *
   * @param {string} template The primary tiles' XYZ template.
   * @returns {(centre: {lng: number, lat: number}, zoom: number) => number} The number of tiles fetched.
   */
  function createPrefetcher(template) {
    let last = '';
    return (centre, zoom) => {
      if (!template) return 0;
      const tiles = core.prefetchDemTiles(centre, zoom);
      const key = tiles.map((/** @type {{z: number, x: number, y: number}} */ t) => `${t.z}/${t.x}/${t.y}`).join(' ');
      if (key === last) return 0;
      last = key;
      for (const tile of tiles) {
        fetch(core.tileUrl(template, tile), { credentials: 'omit' }).catch(() => {});
      }
      return tiles.length;
    };
  }

  self.pwaMap3dTerrain = Object.freeze({
    SUNLIGHT_LAYER,
    registerDemProtocol,
    installTerrain,
    removeTerrain,
    hasTerrain,
    createPrefetcher,
  });
})();
