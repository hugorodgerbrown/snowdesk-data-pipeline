/*
 * static/js/alpine_relief_worker.js — answers `alpinerelief://` tiles off
 * the main thread (SNOW-1460).
 *
 * A dedicated worker, started by static/js/map_beyond_switzerland.js on the
 * first relief tile the map asks for. Its first message is `init`, carrying
 * the core's URL (static/js/alpine_relief_core.js), the elevation tiles'
 * XYZ template (MAP_3D_DEM_TILE_URL, Terrarium-encoded) and swisstopo's
 * detailed-relief polygons (static/js/swisstopo_extent.js, cloned across).
 * Every message after that is one tile:
 *
 *   {id, url}        url is `alpinerelief://{z}/{x}/{y}`. The worker fetches
 *                    the elevation tile, draws it on an OffscreenCanvas to
 *                    read its pixels, paints the relief with the core (the
 *                    neutral hillshade and the rock tone, erased inside the
 *                    detailed relief) and replies {id, data} with the PNG
 *                    bytes, transferred.
 *   {type: 'cancel', id}
 *                    MapLibre aborted the tile: its fetch is aborted and no
 *                    reply is sent for it.
 *
 * Any failure replies {id, error} with the message, so the page's handler
 * rejects and MapLibre retries the tile later.
 *
 * The fetch is made from the worker, which the page's service worker
 * controls, so the elevation tiles reach the same cache the 3D view's do.
 */
(function () {
  'use strict';

  /** @type {any} */
  let core = null;
  let demTemplate = '';
  /** @type {any} */
  let relief = null;
  /** Requests in flight, by id. */
  const pending = new Map();

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
   * Paint one tile from the elevation tile's bytes.
   *
   * @param {Blob} blob
   * @param {{z: number, x: number, y: number}} tile
   * @returns {Promise<ArrayBuffer>}
   */
  async function paintTile(blob, tile) {
    const bitmap = await createImageBitmap(blob);
    const size = bitmap.width;
    const canvas = new OffscreenCanvas(size, size);
    const ctx = /** @type {OffscreenCanvasRenderingContext2D} */ (canvas.getContext('2d'));
    // Terrarium heights are spread across the three channels: no blending.
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(bitmap, 0, 0, size, size);
    if (typeof bitmap.close === 'function') bitmap.close();
    const dem = ctx.getImageData(0, 0, size, size).data;
    const rgba = core.paint(dem, size, tile, relief);
    ctx.clearRect(0, 0, size, size);
    ctx.putImageData(new ImageData(rgba, size, size), 0, 0);
    const png = await canvas.convertToBlob({ type: 'image/png' });
    return png.arrayBuffer();
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
      if (!tile) throw new Error(`alpine_relief: not a tile url: ${url}`);
      const response = await fetch(core.tileUrl(demTemplate, tile), {
        signal: controller.signal,
        credentials: 'omit',
      });
      if (!response.ok) throw new Error(`alpine_relief: elevation tile ${response.status}`);
      const data = await paintTile(await response.blob(), tile);
      reply(id, { data });
    } catch (err) {
      reply(id, { error: String((err && /** @type {any} */ (err).message) || err) });
    }
  }

  self.addEventListener('message', (event) => {
    const message = /** @type {any} */ (event).data || {};
    if (message.type === 'init') {
      self.importScripts(message.coreUrl);
      core = self.pwaAlpineReliefCore;
      demTemplate = message.demTileUrl;
      relief = message.relief;
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
