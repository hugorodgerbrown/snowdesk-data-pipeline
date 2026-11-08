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
 *                    tile wholly inside the extent is answered empty without
 *                    a fetch; one wholly outside with the bytes as fetched,
 *                    never decoded.
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
  let styleUrl = '';
  /** @type {?Promise<string>} */
  let template = null;
  /** Requests in flight, by id. */
  const pending = new Map();

  /**
   * Fetch JSON, failing on a non-2xx answer.
   *
   * @param {string} url
   * @returns {Promise<any>}
   */
  async function fetchJson(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`osm_clip: ${url} ${response.status}`);
    return response.json();
  }

  /**
   * The tile template, resolved once from the configured OpenFreeMap style:
   * its OpenMapTiles vector source names its tiles directly or through a
   * TileJSON `url`. Read from the style, not assumed, because a
   * self-hosted deployment lays its tiles out differently from the public
   * /planet TileJSON. A failed read is not kept, so the next tile asks
   * again. It is fetched with no abort signal: the read is shared by every
   * tile waiting on it, so one tile's cancel must not fail the others.
   *
   * @returns {Promise<string>}
   */
  function tileTemplate() {
    if (!template) {
      template = (async () => {
        const style = await fetchJson(styleUrl);
        const sources = (style && style.sources) || {};
        const source =
          (sources.openmaptiles && sources.openmaptiles.type === 'vector' && sources.openmaptiles) ||
          Object.values(sources).find((s) => s && s.type === 'vector');
        if (!source) throw new Error('osm_clip: the style has no vector source');
        let tiles = Array.isArray(source.tiles) ? source.tiles : [];
        let base = styleUrl;
        if (!tiles.length && source.url) {
          base = new URL(source.url, styleUrl).href;
          const tilejson = await fetchJson(base);
          tiles = tilejson && Array.isArray(tilejson.tiles) ? tilejson.tiles : [];
        }
        if (!tiles.length) throw new Error('osm_clip: the vector source has no tiles');
        // Kept as written: the {z}/{x}/{y} braces must survive, so the
        // template is resolved only when it is not already absolute.
        const first = String(tiles[0]);
        return /^https?:\/\//.test(first) ? first : decodeURI(new URL(first, base).href);
      })();
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
      // Classified before the fetch: over Switzerland every tile is wholly
      // inside, and is answered empty without asking OpenFreeMap for it.
      // OpenMapTiles' layers are all 4096 units across.
      const status = core.classifyTile(extent, tile, 4096, kit.polygonClipping);
      if (status === core.INSIDE) {
        reply(id, { data: new ArrayBuffer(0) });
        return;
      }
      const source = core.tileUrl(await tileTemplate(), tile);
      const response = await fetch(source, { signal: controller.signal });
      if (!response.ok) throw new Error(`osm_clip: tile ${response.status}`);
      const bytes = await response.arrayBuffer();
      // Wholly outside: passed on as fetched, never decoded.
      if (status === core.OUTSIDE || !bytes.byteLength) reply(id, { data: bytes });
      else reply(id, { data: clipTile(bytes, tile) });
    } catch (err) {
      reply(id, { error: String((err && /** @type {any} */ (err).message) || err) });
    }
  }

  /**
   * The URL, if it is on this worker's own origin. The page hands the
   * worker its script URLs; only same-origin scripts are imported, so a
   * message can never load code from elsewhere.
   *
   * @param {unknown} url
   * @returns {string}
   */
  function ownScript(url) {
    const resolved = new URL(String(url), self.location.href);
    if (resolved.origin !== self.location.origin) {
      throw new Error(`refusing a script from another origin: ${resolved.origin}`);
    }
    return resolved.href;
  }

  self.addEventListener('message', (event) => {
    const message = /** @type {any} */ (event).data || {};
    if (message.type === 'init') {
      try {
        self.importScripts(ownScript(message.kitUrl), ownScript(message.coreUrl));
      } catch (err) {
        // A refused or failed import leaves the worker without its core,
        // so every tile it is asked for is answered with an error.
        console.error('[map] SNOW-1460: worker init failed', err);
        return;
      }
      kit = self.VectorTileKit;
      core = self.pwaOsmClipCore;
      extent = message.extent;
      styleUrl = message.styleUrl;
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
