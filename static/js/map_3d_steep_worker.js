// @ts-check
/*
 * static/js/map_3d_steep_worker.js — Steep terrain's tiles, off the main
 * thread, for the 3-D map (static/js/map_3d.js).
 *
 * A tilted view asks for class tiles out to the horizon, and each one is a
 * fetch, an image decode, a getImageData and a per-pixel classify. On the
 * main thread that stalls panning on a phone; here it runs beside it.
 *
 * Messages in:
 *   {type: 'init', scripts: [url, …], tileTemplate}
 *       importScripts the cores (slope_classes_core.js, then
 *       terrain_filter_core.js: hashed static URLs, so the page passes
 *       them) and builds the Steep terrain classifier and palette.
 *   {type: 'tile', id, z, x, y}
 *       answer {id, bitmap} — the painted 256 px tile as a transferable
 *       ImageBitmap, or {id, error} on failure.
 *   {type: 'abort', id}
 *       drop a request MapLibre no longer wants.
 *
 * The fetch and cache rules are /map/'s (terrain-filter-is-class-tiles-
 * filtered-on-device.md): a real 204 inside coverage is no data and is
 * cached; a failed request draws nothing and is not cached.
 */

/** @type {any} */
let core = null;
/** @type {any} */
let classify = null;
/** @type {any} */
let palette = null;
/** @type {any} */
let cache = null;
let template = '';
const CACHE_TILES = 64;
/** @type {Map<number, AbortController>} */
const inFlight = new Map();

/**
 * Decode a class tile to its raw pixels: numbers, not colours, so neither
 * premultiplication nor colour management may touch them.
 *
 * @param {Blob} blob
 * @returns {Promise<Uint8ClampedArray>}
 */
async function decode(blob) {
  const size = core.TILE_SIZE;
  const bitmap = await createImageBitmap(blob, {
    premultiplyAlpha: 'none',
    colorSpaceConversion: 'none',
  });
  const canvas = new OffscreenCanvas(size, size);
  const ctx = /** @type {OffscreenCanvasRenderingContext2D} */ (
    canvas.getContext('2d', { willReadFrequently: true })
  );
  ctx.drawImage(bitmap, 0, 0);
  bitmap.close();
  return ctx.getImageData(0, 0, size, size).data;
}

/**
 * One tile's class pixels, cached as /map/ caches them.
 *
 * @param {{z: number, x: number, y: number}} tile
 * @param {AbortSignal} signal
 * @returns {Promise<Uint8ClampedArray|null>}
 */
async function load(tile, signal) {
  const key = `${tile.z}/${tile.x}/${tile.y}`;
  const cached = cache.get(key);
  if (cached !== undefined) return cached;
  let response;
  try {
    response = await fetch(core.tileUrl(template, tile.z, tile.x, tile.y), {
      signal,
      credentials: 'omit',
    });
  } catch (err) {
    if (signal.aborted) throw err;
    return null;
  }
  if (response.status === 204) {
    const empty = core.tileInCoverage(tile.z, tile.x, tile.y) ? core.noDataTile() : null;
    cache.set(key, empty);
    return empty;
  }
  if (!response.ok) return null;
  const pixels = await decode(await response.blob());
  cache.set(key, pixels);
  return pixels;
}

self.addEventListener('message', async (/** @type {MessageEvent} */ event) => {
  const msg = event.data || {};
  if (msg.type === 'init') {
    /** @type {any} */ (self).importScripts(...msg.scripts);
    core = /** @type {any} */ (self).pwaTerrainFilterCore;
    template = msg.tileTemplate;
    const filter = core.normaliseFilter({ ...core.DEFAULT_FILTER, mode: 'slope' });
    classify = core.createClassifier(filter);
    palette = core.paletteFor(filter);
    cache = core.createTileCache(CACHE_TILES);
    return;
  }
  if (msg.type === 'abort') {
    const controller = inFlight.get(msg.id);
    if (controller) controller.abort();
    inFlight.delete(msg.id);
    return;
  }
  if (msg.type !== 'tile' || !core) return;
  const controller = new AbortController();
  inFlight.set(msg.id, controller);
  try {
    const tile = { z: msg.z, x: msg.x, y: msg.y };
    const pixels = await load(tile, controller.signal);
    const size = core.TILE_SIZE;
    const out = new Uint8ClampedArray(size * size * 4);
    if (pixels) core.paintTile(pixels, out, classify, palette, tile);
    const bitmap = await createImageBitmap(new ImageData(out, size, size));
    /** @type {any} */ (self).postMessage({ id: msg.id, bitmap }, [bitmap]);
  } catch (err) {
    /** @type {any} */ (self).postMessage({ id: msg.id, error: String(err) });
  } finally {
    inFlight.delete(msg.id);
  }
});
