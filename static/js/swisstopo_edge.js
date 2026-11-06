/*
 * static/js/swisstopo_edge.js — the `swisstopoedge://` tile protocol
 * (SNOW-1457).
 *
 * Draws swisstopo's raster winter map outside Switzerland, on top of the
 * swisstopo winter vector style, which draws Switzerland. The rules are
 * in static/js/swisstopo_edge_core.js; this file does the canvas work.
 *
 * For each tile:
 *
 *   1. Switzerland. The mask is fetched once from the map's
 *      `data-major-regions-url` (?country=ch). A tile that meets no
 *      region's box needs no mask. Otherwise the mask is drawn into the
 *      tile; if it covers every pixel the tile is wholly Swiss, and an
 *      empty tile is returned without fetching anything.
 *   2. The raster. The WMTS tile is fetched. If it is blank (pure white,
 *      swisstopo's "no detail here") and above z13, the nearest ancestor
 *      with detail is cut and enlarged instead. Ancestors are held in a
 *      small LRU, so the sixteen z16 tiles under one z14 share one fetch.
 *   3. The cut. Switzerland is erased from the result.
 *
 * Nothing here is cached beyond the ancestors: MapLibre keeps the tiles it
 * draws. A mask that fails to load rejects the tile, so nothing is drawn
 * (the vector map alone, as before this ticket) and the mask is fetched
 * again on the next tile. A failed
 * WMTS request with no ancestor to stand in rejects, so MapLibre treats
 * the tile as an error and asks again later; it is never kept as empty.
 * An abort is re-thrown: MapLibre cancelled the tile.
 *
 * In 4.7.1 a protocol that answers with no `data` leaves the tile pending
 * forever, so "nothing here" is a transparent bitmap.
 *
 * Registered once at parse time, when `maplibregl.addProtocol` exists (the
 * Vitest harness stubs maplibregl without it). Exposes `self.pwaSwisstopoEdge`
 * for tests: `{ handle(params, abortController), reset() }`.
 */
(function () {
  'use strict';

  const CORE = self.pwaSwisstopoEdgeCore;
  if (!CORE) return;

  const SIZE = CORE.TILE_SIZE;

  /** How many decoded ancestors to keep. A z14 serves 16 tiles at z16. */
  const ANCESTOR_CACHE_SIZE = 48;

  /** @type {Promise<Array<any>>|null} */
  let maskPromise = null;

  /** @type {Map<string, Promise<ImageBitmap|null>>} */
  const ancestorCache = new Map();

  /**
   * The Swiss mask polygons, fetched once. A failure is not kept, so the
   * next tile asks again.
   *
   * @returns {Promise<Array<any>>}
   */
  function loadMask() {
    if (maskPromise) return maskPromise;
    const mapEl = document.getElementById('map');
    const base = mapEl && mapEl.dataset ? mapEl.dataset.majorRegionsUrl : '';
    if (!base) return Promise.reject(new Error('swisstopo edge: no major-regions URL'));
    maskPromise = fetch(base + '?country=ch', { credentials: 'same-origin' })
      .then((response) => {
        if (!response.ok) throw new Error('swisstopo edge: mask HTTP ' + response.status);
        return response.json();
      })
      .then((collection) => CORE.maskPolygons(collection))
      .catch((err) => {
        maskPromise = null;
        throw err;
      });
    return maskPromise;
  }

  /**
   * A 2D canvas `size` px square.
   *
   * @param {number} size
   * @returns {OffscreenCanvas|HTMLCanvasElement}
   */
  function canvas(size) {
    if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(size, size);
    const el = document.createElement('canvas');
    el.width = size;
    el.height = size;
    return el;
  }

  /**
   * Trace the mask polygons into a context as ONE path, for a non-zero
   * fill: the core oriented outer rings and holes oppositely.
   *
   * @param {any} ctx
   * @param {Array<any>} polygons
   * @param {{z: number, x: number, y: number}} tile
   * @returns {void}
   */
  function traceMask(ctx, polygons, tile) {
    const project = CORE.projector(tile.z, tile.x, tile.y, SIZE);
    ctx.beginPath();
    for (const polygon of polygons) {
      for (const ring of polygon.rings) {
        ring.forEach((/** @type {Array<number>} */ point, /** @type {number} */ i) => {
          const [px, py] = project(point[0], point[1]);
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.closePath();
      }
    }
  }

  /**
   * Whether the mask covers the whole tile.
   *
   * @param {Array<any>} polygons Those touching the tile.
   * @param {{z: number, x: number, y: number}} tile
   * @returns {boolean}
   */
  function whollySwiss(polygons, tile) {
    const probe = canvas(SIZE);
    const ctx = /** @type {any} */ (probe.getContext('2d', { willReadFrequently: true }));
    ctx.fillStyle = '#000';
    traceMask(ctx, polygons, tile);
    ctx.fill('nonzero');
    return CORE.isFullyCovered(ctx.getImageData(0, 0, SIZE, SIZE).data);
  }

  /**
   * One WMTS tile as a bitmap: null when it is blank, undefined when the
   * request failed (so a failure is never remembered as a blank).
   *
   * @param {number} z
   * @param {number} x
   * @param {number} y
   * @param {AbortSignal} [signal]
   * @returns {Promise<ImageBitmap|null|undefined>}
   */
  async function fetchDetail(z, x, y, signal) {
    let response;
    try {
      response = await fetch(CORE.wmtsUrl(z, x, y), { signal, credentials: 'omit', mode: 'cors' });
    } catch (err) {
      if (signal && signal.aborted) throw err;
      return undefined;
    }
    if (!response.ok) return undefined;
    const bitmap = await createImageBitmap(await response.blob());
    const probe = canvas(SIZE);
    const ctx = /** @type {any} */ (probe.getContext('2d', { willReadFrequently: true }));
    ctx.drawImage(bitmap, 0, 0, SIZE, SIZE);
    if (CORE.isBlank(ctx.getImageData(0, 0, SIZE, SIZE).data)) {
      if (typeof bitmap.close === 'function') bitmap.close();
      return null;
    }
    return bitmap;
  }

  /**
   * An ancestor tile, through the LRU. Not tied to any one tile's abort:
   * siblings share it.
   *
   * @param {number} z
   * @param {number} x
   * @param {number} y
   * @returns {Promise<ImageBitmap|null>}
   */
  function ancestor(z, x, y) {
    const key = z + '/' + x + '/' + y;
    const hit = ancestorCache.get(key);
    if (hit) {
      ancestorCache.delete(key);
      ancestorCache.set(key, hit);
      return hit;
    }
    // A blank ancestor is remembered; a failed one is dropped, so the next
    // tile under it asks again.
    const pending = fetchDetail(z, x, y)
      .catch(() => undefined)
      .then((bitmap) => {
        if (bitmap === undefined) {
          if (ancestorCache.get(key) === pending) ancestorCache.delete(key);
          return null;
        }
        return bitmap;
      });
    ancestorCache.set(key, pending);
    while (ancestorCache.size > ANCESTOR_CACHE_SIZE) {
      const oldest = ancestorCache.keys().next().value;
      if (oldest === undefined) break;
      ancestorCache.delete(oldest);
    }
    return pending;
  }

  /**
   * A transparent tile.
   *
   * @returns {Promise<ImageBitmap>}
   */
  function empty() {
    return createImageBitmap(new ImageData(SIZE, SIZE));
  }

  /**
   * MapLibre's handler for `swisstopoedge://z/x/y`.
   *
   * @param {{url: string}} params
   * @param {AbortController} abortController
   * @returns {Promise<{data: ImageBitmap}>}
   */
  async function handle(params, abortController) {
    const tile = CORE.parseTileUrl(params.url);
    if (!tile) throw new Error('swisstopo edge: not a tile url: ' + params.url);
    const signal = abortController && abortController.signal;

    // Without the mask the raster would cover Switzerland, so a mask that
    // fails rejects the tile (MapLibre asks again later) and draws nothing.
    const polygons = CORE.polygonsTouching(await loadMask(), CORE.tileBounds(tile.z, tile.x, tile.y));
    if (polygons.length && whollySwiss(polygons, tile)) return { data: await empty() };

    /** @type {ImageBitmap|null|undefined} */
    let source = await fetchDetail(tile.z, tile.x, tile.y, signal);
    const failed = source === undefined;
    let crop = { sx: 0, sy: 0, size: SIZE };
    if (!source) {
      for (const up of CORE.ancestors(tile.z, tile.x, tile.y)) {
        source = await ancestor(up.z, up.x, up.y);
        if (signal && signal.aborted) throw new DOMException('Aborted', 'AbortError');
        if (source) {
          crop = up;
          break;
        }
      }
    }
    if (!source) {
      // A tile that failed is an error to MapLibre, so it is asked for
      // again rather than kept as an empty one.
      if (failed) throw new Error('swisstopo edge: tile request failed: ' + params.url);
      return { data: await empty() };
    }

    const out = canvas(SIZE);
    const ctx = /** @type {any} */ (out.getContext('2d'));
    ctx.drawImage(source, crop.sx, crop.sy, crop.size, crop.size, 0, 0, SIZE, SIZE);
    if (crop.size === SIZE && typeof source.close === 'function') source.close();
    if (polygons.length) {
      ctx.globalCompositeOperation = 'destination-out';
      ctx.fillStyle = '#000';
      traceMask(ctx, polygons, tile);
      ctx.fill('nonzero');
    }
    return { data: await createImageBitmap(out) };
  }

  /**
   * Forget the mask and the ancestors (tests).
   *
   * @returns {void}
   */
  function reset() {
    maskPromise = null;
    ancestorCache.clear();
  }

  if (typeof maplibregl !== 'undefined' && typeof maplibregl.addProtocol === 'function') {
    maplibregl.addProtocol(CORE.PROTOCOL, handle);
  }

  self.pwaSwisstopoEdge = Object.freeze({ handle, reset });
})();
