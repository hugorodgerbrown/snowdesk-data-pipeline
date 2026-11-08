/*
 * static/js/osm_clip_worker.js — answers `osmclip://` tiles off the main
 * thread (SNOW-1460).
 *
 * A dedicated worker, started by static/js/map_beyond_switzerland.js on the
 * first OSM tile the map asks for. Its first message is `init`, carrying
 * the kit's and the core's URLs, the OSM TileJSON's URL and the swisstopo
 * extent's polygons (static/js/swisstopo_extent.js, cloned across). Every
 * message after that is one tile:
 *
 *   {id, url}        url is `osmclip://{z}/{x}/{y}`. The worker reads the
 *                    TileJSON once for the tile template, fetches the
 *                    OpenFreeMap tile, decodes it (@mapbox/vector-tile),
 *                    clips every feature to outside the extent
 *                    (static/js/osm_clip_core.js), encodes it again (vt-pbf)
 *                    and replies {id, data} with the bytes, transferred. A
 *                    tile wholly inside the extent is answered empty, one
 *                    wholly outside with the bytes as fetched.
 *   {type: 'cancel', id}
 *                    MapLibre aborted the tile: its fetch is aborted and no
 *                    reply is sent for it.
 *
 * Any failure (the fetch, a non-OK status, a tile that does not decode)
 * replies {id, error} with the message, so the page's handler rejects and
 * MapLibre retries the tile later.
 *
 * The fetches are made from the worker, which the page's service worker
 * controls like the page itself, so OpenFreeMap's tiles reach the same
 * runtime cache as the basemap's (static/js/sw.js).
 */
(function () {
  'use strict';

  /** @type {any} */
  let core = null;
  /** @type {any} */
  let kit = null;
  /** @type {any} */
  let extent = null;
  let tilejsonUrl = '';
  /** @type {?Promise<string>} */
  let template = null;
  /** Requests in flight, by id. */
  const pending = new Map();

  /**
   * The tile template, read from the TileJSON once. A failed read is not
   * kept, so the next tile asks again.
   *
   * @param {AbortSignal} signal
   * @returns {Promise<string>}
   */
  function tileTemplate(signal) {
    if (!template) {
      template = fetch(tilejsonUrl, { signal })
        .then((response) => {
          if (!response.ok) throw new Error(`osm_clip: TileJSON ${response.status}`);
          return response.json();
        })
        .then((json) => {
          const tiles = json && Array.isArray(json.tiles) ? json.tiles : [];
          if (!tiles.length) throw new Error('osm_clip: TileJSON has no tiles');
          return String(tiles[0]);
        });
      template.catch(() => { template = null; });
    }
    return template;
  }

  /**
   * Decode, clip and encode one tile.
   *
   * @param {ArrayBuffer} bytes
   * @param {{z: number, x: number, y: number}} tile
   * @returns {ArrayBuffer}
   */
  function clipTile(bytes, tile) {
    const decoded = new kit.VectorTile(new kit.Pbf(new Uint8Array(bytes)));
    /** @type {Record<string, {extent: number, features: Array<object>}>} */
    const layers = {};
    for (const name of Object.keys(decoded.layers)) {
      const layer = decoded.layers[name];
      const features = [];
      for (let i = 0; i < layer.length; i += 1) {
        const feature = layer.feature(i);
        features.push({
          type: feature.type,
          id: feature.id,
          tags: feature.properties,
          geometry: feature.loadGeometry().map(
            (/** @type {Array<{x: number, y: number}>} */ ring) => ring.map((p) => [p.x, p.y]),
          ),
        });
      }
      layers[name] = { extent: layer.extent, features };
    }
    const result = core.clipLayers(layers, extent, tile, kit.polygonClipping);
    if (result.status === core.OUTSIDE) return bytes;
    const names = Object.keys(result.layers);
    if (!names.length) return new ArrayBuffer(0);
    // vt-pbf takes one extent for every layer; OpenMapTiles' are all 4096.
    const encoded = kit.vtpbf.fromGeojsonVt(result.layers, {
      version: 2,
      extent: result.layers[names[0]].extent,
    });
    return encoded.buffer.slice(encoded.byteOffset, encoded.byteOffset + encoded.byteLength);
  }

  /**
   * @param {number} id
   * @param {{data?: ArrayBuffer, error?: string}} body
   */
  function reply(id, body) {
    if (!pending.has(id)) return;
    pending.delete(id);
    if (body.data) self.postMessage({ id, data: body.data }, [body.data]);
    else self.postMessage({ id, error: body.error });
  }

  /**
   * @param {number} id
   * @param {string} url
   */
  async function handleTile(id, url) {
    const controller = new AbortController();
    pending.set(id, controller);
    try {
      const tile = core.parseUrl(url);
      if (!tile) throw new Error(`osm_clip: not a tile url: ${url}`);
      const source = core.tileUrl(await tileTemplate(controller.signal), tile);
      const response = await fetch(source, { signal: controller.signal });
      if (!response.ok) throw new Error(`osm_clip: tile ${response.status}`);
      const bytes = await response.arrayBuffer();
      reply(id, { data: bytes.byteLength ? clipTile(bytes, tile) : new ArrayBuffer(0) });
    } catch (err) {
      reply(id, { error: String((err && /** @type {any} */ (err).message) || err) });
    }
  }

  self.addEventListener('message', (event) => {
    const message = /** @type {any} */ (event).data || {};
    if (message.type === 'init') {
      self.importScripts(message.kitUrl, message.coreUrl);
      kit = self.VectorTileKit;
      core = self.pwaOsmClipCore;
      extent = message.extent;
      tilejsonUrl = message.tilejsonUrl;
      return;
    }
    if (message.type === 'cancel') {
      const controller = pending.get(message.id);
      pending.delete(message.id);
      if (controller) controller.abort();
      return;
    }
    if (typeof message.id === 'number' && typeof message.url === 'string') {
      handleTile(message.id, message.url);
    }
  });
})();
