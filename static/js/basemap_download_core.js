/*
 * static/js/basemap_download_core.js — Pure tile-URL helpers for the
 * map's basemap download controls (SNOW-521 per-region rework;
 * SNOW-522 re-adds a client-side tile-math port for the custom-area
 * control).
 *
 * Dependency-free IIFE attaching ``self.pwaBasemapDownloadCore`` — same
 * idiom as ``basemap_cache_core.js`` (frozen export, no side effects).
 * Loaded on the page (window context) BEFORE ``static/js/map.js`` in
 * ``home.html``, unlike ``basemap_cache_core.js`` which is only ever
 * ``importScripts``'d into the service worker — this module has no SW
 * dependency, so it's a plain deferred ``<script>`` tag.
 *
 * SNOW-521 replaced the viewport-anchored "Download basemap" control
 * with per-region download: each region's tile coverage is precomputed
 * server-side (``regions.services.basemap_tiles`` — see that module's
 * docstring for the stored blob shape) and served over the API, so the
 * per-*region* download does no tile enumeration or byte-estimate
 * arithmetic of its own — ``rangesToTileURLs``/``centreTileURL`` below
 * still just turn that server-computed data into URLs.
 *
 * SNOW-522 re-introduces the tile-index/byte-estimate arithmetic
 * client-side, deliberately, for the *custom-area* download: a
 * user-drawn bbox (the map panned/zoomed under a fixed framing
 * rectangle) is not precomputable server-side the way a region's fixed
 * boundary is — there is no stable ID to precompute against, and the
 * live "up to N MB" readout has to track every frame of that pan/zoom
 * with no network round-trip. ``lonLatToTile``/``tileRangesForBBox``/
 * ``tileCount``/``centreTile``/``buildBlob`` below are therefore a
 * DELIBERATE re-port of ``apps/regions/services/basemap_tiles.py``'s
 * pure functions of the same names (module docstring there has the full
 * algorithm rationale) — this is not drift back to a client that forgot
 * the math moved server-side; the two now legitimately coexist for two
 * different download shapes. Kept honest against the Python by a shared
 * golden vector asserted in both ``tests/js/test_basemap_download_core.js``
 * and ``tests/regions/services/test_basemap_tiles.py`` — see either
 * test's comment for the paired assertion. That shared vector is a
 * convention, not a mechanism: nothing fails the build if the two drift,
 * so treat any change to the Python tile math as also owed here.
 *
 * Coordinate convention: **(lon, lat) order**, matching
 * ``basemap_tiles.py``'s deliberate carve-out from the project's usual
 * (lat, lon) argument order — see that module's docstring. Kept
 * identical here so a reader porting between the two never has to
 * mentally swap axes.
 *
 * Public API — attached to ``self.pwaBasemapDownloadCore``:
 *
 *   rangesToTileURLs(template, blob)
 *     Expands a full basemap_download blob's ``z`` tile-index ranges
 *     (``{"<z>": [xmin, xmax, ymin, ymax]}``, as fetched from
 *     ``/api/region-basemap-tiles/?id=...`` OR produced locally by
 *     ``buildBlob`` below) into the full list of ``{z}/{x}/{y}`` tile
 *     URLs, substituted into ``template``. Returns ``[]`` for a falsy
 *     ``template`` or ``blob``, or a blob with no ``z`` ranges.
 *   centreTileURL(template, summary)
 *     Builds the single tile URL for ``summary.centre_tile``
 *     (``{z, x, y}``) — the "done-probe" key: the client checks whether
 *     this one tile is present in the pinned cache as a proxy for "this
 *     download completed" (matches the download's centre tile at its
 *     band's detail floor, whether server-picked (region) or
 *     locally-computed (custom area)). Returns ``null`` for a falsy
 *     ``template``/``summary``/``summary.centre_tile``.
 *   lonLatToTile(lon, lat, z)
 *     Web Mercator ``[x, y]`` tile indices for ``(lon, lat)`` at zoom
 *     ``z`` — mirror of ``basemap_tiles.lon_lat_to_tile``. Not clamped
 *     to the valid ``[0, 2**z - 1]`` range; ``tileRangesForBBox`` clamps
 *     explicitly, matching the Python.
 *   tileRangesForBBox(bbox, minZ, maxZ)
 *     ``{"<z>": [xmin, xmax, ymin, ymax]}`` for every zoom in
 *     ``[minZ, maxZ]`` — mirror of ``basemap_tiles.tile_ranges``, same
 *     clamping.
 *   tileCount(ranges)
 *     Total tile count across every zoom level in ``ranges`` — mirror of
 *     ``basemap_tiles.tile_count``.
 *   centreTile(bbox, z)
 *     The tile at ``bbox``'s centre point, at zoom ``z`` — mirror of
 *     ``basemap_tiles.centre_tile``.
 *   buildBlob(bbox, minZ, maxZ)
 *     The full blob (``{band, count, mb, over_ceiling, centre_tile, z}``)
 *     — mirror of ``basemap_tiles.build_blob`` — produced in the SAME
 *     shape ``rangesToTileURLs``/``centreTileURL`` already consume, so a
 *     locally-built blob and a server-fetched one are interchangeable.
 *   budgetScaleForBBox(bbox, minZ, maxZ)
 *     The largest factor in ``[0, 1]`` by which ``bbox`` may be scaled
 *     about its centre while its download still fits under
 *     ``DOWNLOAD_CEILING_MB``. Client-only — it has no
 *     ``basemap_tiles.py`` counterpart and needs none: the server never
 *     sizes a framing rectangle. Do NOT go looking for a Python twin to
 *     keep it honest against.
 *   hasStorageHeadroom(estimate, mb)
 *     SNOW-568: whether a download of ``mb`` megabytes fits in the
 *     origin's remaining storage quota, with a safety margin. Client-only,
 *     like ``budgetScaleForBBox`` — no ``basemap_tiles.py`` counterpart.
 *   MICRO_BAND, WORST_CASE_BYTES_PER_TILE, DOWNLOAD_CEILING_MB,
 *   STORAGE_HEADROOM_FACTOR
 *     Constants mirroring ``basemap_tiles.py``'s module-level constants
 *     of the same name (see there for the sizing rationale) — except
 *     ``STORAGE_HEADROOM_FACTOR``, which is client-only.
 */

(function () {
  'use strict';

  // Mirrors apps/regions/services/basemap_tiles.py::MICRO_BAND — the
  // custom-area download uses the same zoom band as the region download
  // (z10-14), so the two produce directly-comparable size estimates.
  var MICRO_BAND = [10, 14];

  // Mirrors apps/regions/services/basemap_tiles.py::WORST_CASE_BYTES_PER_TILE.
  var WORST_CASE_BYTES_PER_TILE = 100 * 1024;

  // Mirrors apps/regions/services/basemap_tiles.py::DOWNLOAD_CEILING_MB.
  var DOWNLOAD_CEILING_MB = 200;

  // SNOW-568: the fraction of the origin's REMAINING storage quota a
  // single download may claim. Client-only — no basemap_tiles.py twin.
  // Half leaves room for the shell cache, the passive basemap cache the
  // user's ordinary browsing keeps filling, IndexedDB, and the mutation
  // queue, none of which stop growing because a download is in flight.
  var STORAGE_HEADROOM_FACTOR = 0.5;

  /**
   * Expand a full basemap_download blob's ``z`` ranges into tile URLs.
   *
   * @param {string} template A ``{z}``/``{x}``/``{y}`` tile URL template.
   * @param {{z?: Object<string, number[]>}} blob The full blob — either
   *   fetched from ``/api/region-basemap-tiles/?id=...`` or built locally
   *   by ``buildBlob`` (``{band, count, mb, over_ceiling, centre_tile,
   *   z}`` — see ``regions/services/basemap_tiles.py``'s module
   *   docstring).
   * @returns {string[]}
   */
  function rangesToTileURLs(template, blob) {
    const urls = [];
    if (!template || !blob || !blob.z) return urls;
    for (const z of Object.keys(blob.z)) {
      const [xmin, xmax, ymin, ymax] = blob.z[z];
      for (let x = xmin; x <= xmax; x++) {
        for (let y = ymin; y <= ymax; y++) {
          urls.push(
            template.replace('{z}', z).replace('{x}', String(x)).replace('{y}', String(y)),
          );
        }
      }
    }
    return urls;
  }

  /**
   * Build the single "done-probe" tile URL for a download summary.
   *
   * @param {string} template A ``{z}``/``{x}``/``{y}`` tile URL template.
   * @param {{centre_tile?: {z: number, x: number, y: number}}} summary A
   *   download's summary (the small ``{count, mb, over_ceiling,
   *   centre_tile}`` shape inlined on the geojson endpoints' ``properties.
   *   download``, a full server-fetched blob, or a locally-built
   *   ``buildBlob`` result — all carry ``centre_tile`` in the same shape).
   * @returns {string | null}
   */
  function centreTileURL(template, summary) {
    if (!template || !summary || !summary.centre_tile) return null;
    const { z, x, y } = summary.centre_tile;
    return template
      .replace('{z}', String(z))
      .replace('{x}', String(x))
      .replace('{y}', String(y));
  }

  /**
   * Web Mercator ``[x, y]`` tile indices for ``(lon, lat)`` at zoom ``z``.
   *
   * Deliberate re-port of ``basemap_tiles.lon_lat_to_tile`` — see the
   * module header above for why. Not clamped to the valid
   * ``[0, 2**z - 1]`` range; ``tileRangesForBBox`` clamps explicitly,
   * matching the Python.
   *
   * @param {number} lon Longitude in degrees.
   * @param {number} lat Latitude in degrees.
   * @param {number} z Zoom level.
   * @returns {[number, number]}
   */
  function lonLatToTile(lon, lat, z) {
    const n = Math.pow(2, z);
    const [wx, wy] = lonLatToWorld(lon, lat);
    return [Math.floor(wx * n), Math.floor(wy * n)];
  }

  /**
   * ``(lon, lat)`` as UNFLOORED Web Mercator world coordinates — the same
   * projection ``lonLatToTile`` floors, expressed as fractions of the
   * whole world in ``[0, 1]`` (x eastward from the antimeridian, y
   * southward from the north pole). Multiply by ``2**z`` for fractional
   * tile indices at zoom ``z``.
   *
   * Factored out of ``lonLatToTile`` (whose arithmetic it reproduces
   * operation-for-operation, so the golden vector is unaffected) because
   * ``budgetScaleForBBox`` below needs the projection WITHOUT the floor:
   * a rectangle's tile cost has to vary continuously with its size for
   * the frame to resize smoothly.
   *
   * @param {number} lon Longitude in degrees.
   * @param {number} lat Latitude in degrees.
   * @returns {[number, number]}
   */
  function lonLatToWorld(lon, lat) {
    const x = (lon + 180.0) / 360.0;
    const latRad = (lat * Math.PI) / 180.0;
    const y = (1.0 - Math.log(Math.tan(latRad) + 1.0 / Math.cos(latRad)) / Math.PI) / 2.0;
    return [x, y];
  }

  /**
   * Tile-index ranges covering ``bbox`` for every zoom in ``[minZ, maxZ]``.
   *
   * Deliberate re-port of ``basemap_tiles.tile_ranges``.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees.
   * @param {number} minZ Shallowest zoom level (inclusive).
   * @param {number} maxZ Deepest zoom level (inclusive).
   * @returns {Object<string, [number, number, number, number]>}
   *   ``{"<z>": [xmin, xmax, ymin, ymax]}`` — one entry per zoom level,
   *   with indices clamped to the valid ``[0, 2**z - 1]`` range.
   */
  function tileRangesForBBox(bbox, minZ, maxZ) {
    const [west, south, east, north] = bbox;
    const ranges = {};
    for (let z = minZ; z <= maxZ; z++) {
      const [x0, y0] = lonLatToTile(west, north, z);
      const [x1, y1] = lonLatToTile(east, south, z);
      let xmin = Math.min(x0, x1);
      let xmax = Math.max(x0, x1);
      let ymin = Math.min(y0, y1);
      let ymax = Math.max(y0, y1);
      const maxIndex = Math.pow(2, z) - 1;
      xmin = Math.max(0, Math.min(xmin, maxIndex));
      xmax = Math.max(0, Math.min(xmax, maxIndex));
      ymin = Math.max(0, Math.min(ymin, maxIndex));
      ymax = Math.max(0, Math.min(ymax, maxIndex));
      ranges[String(z)] = [xmin, xmax, ymin, ymax];
    }
    return ranges;
  }

  /**
   * Total tile count across every zoom level in ``ranges``.
   *
   * Deliberate re-port of ``basemap_tiles.tile_count``.
   *
   * @param {Object<string, number[]>} ranges The ``{"<z>": [xmin, xmax,
   *   ymin, ymax]}`` shape returned by ``tileRangesForBBox``.
   * @returns {number}
   */
  function tileCount(ranges) {
    let total = 0;
    for (const key of Object.keys(ranges)) {
      const [xmin, xmax, ymin, ymax] = ranges[key];
      total += (xmax - xmin + 1) * (ymax - ymin + 1);
    }
    return total;
  }

  /**
   * The tile at ``bbox``'s centre point, at zoom ``z`` — the "done-probe"
   * key (see ``centreTileURL`` above).
   *
   * Deliberate re-port of ``basemap_tiles.centre_tile``.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees.
   * @param {number} z Zoom level — the download's detail floor
   *   (``MICRO_BAND[1]``).
   * @returns {{z: number, x: number, y: number}}
   */
  function centreTile(bbox, z) {
    const [west, south, east, north] = bbox;
    const centreLon = (west + east) / 2.0;
    const centreLat = (south + north) / 2.0;
    const [x, y] = lonLatToTile(centreLon, centreLat, z);
    return { z: z, x: x, y: y };
  }

  /**
   * Build the full download blob for ``bbox`` — the SAME shape
   * ``rangesToTileURLs``/``centreTileURL`` consume, and the same shape
   * ``/api/region-basemap-tiles/`` serves for a region download.
   *
   * Deliberate re-port of ``basemap_tiles.build_blob``.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees.
   * @param {number} minZ The shallowest zoom level (``MICRO_BAND[0]``).
   * @param {number} maxZ The detail floor (``MICRO_BAND[1]``).
   * @returns {{band: [number, number], count: number, mb: number,
   *   over_ceiling: boolean, centre_tile: {z: number, x: number, y:
   *   number}, z: Object<string, number[]>}}
   */
  function buildBlob(bbox, minZ, maxZ) {
    const ranges = tileRangesForBBox(bbox, minZ, maxZ);
    const count = tileCount(ranges);
    const totalBytes = count * WORST_CASE_BYTES_PER_TILE;
    const mb = Math.ceil(totalBytes / (1024 * 1024));
    return {
      band: [minZ, maxZ],
      count: count,
      mb: mb,
      over_ceiling: mb > DOWNLOAD_CEILING_MB,
      centre_tile: centreTile(bbox, maxZ),
      z: ranges,
    };
  }

  /**
   * The largest factor in ``[0, 1]`` by which ``bbox`` may be scaled
   * about its centre while a ``[minZ, maxZ]`` download of the result
   * still fits under ``DOWNLOAD_CEILING_MB``. ``1`` when ``bbox`` already
   * fits, so a caller can treat "1" as "no cap needed".
   *
   * Why closed-form rather than shrinking until ``buildBlob`` comes in
   * under the ceiling: ``buildBlob``'s count is a STEP function of the
   * box, because the tile indices are floored. Two boxes of identical
   * size sitting a few metres apart on the tile grid can differ by a
   * whole row and column of tiles, so inverting that count by search
   * returns a size that depends on where the box is, not just how big it
   * is — the framing rectangle then shimmers as the map pans beneath it
   * and stutters as it zooms (SNOW-566). This models the cost with the
   * floors removed, which makes the answer a smooth, monotone function of
   * the box alone.
   *
   * The model is a strict UPPER bound on the true count, so the box it
   * sizes never exceeds the ceiling. Per axis, a span of ``t`` fractional
   * tiles covers ``floor(a + t) - floor(a) + 1 < t + 2`` whole ones — so
   * summing ``(sx*2**z + 2) * (sy*2**z + 2)`` over the band bounds the
   * blob's count for scale ``s``, and setting that equal to the budget
   * leaves a quadratic in ``s`` to solve directly. Being an upper bound it
   * leaves roughly a tile's worth of headroom per axis (a few percent of
   * the ceiling) unspent, which is the price of a frame that resizes
   * smoothly — and cheap against a per-tile byte estimate that is itself
   * a worst case.
   *
   * Scaling is assumed to scale the ground footprint linearly, which is
   * exact for the un-pitched view this map is used in. Under pitch the
   * footprint grows faster than the frame does, so the linear model
   * OVER-states the cost of a shrunken frame — the answer stays under the
   * ceiling, it just leaves more headroom.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees, at scale 1.
   * @param {number} minZ Shallowest zoom level (inclusive).
   * @param {number} maxZ Deepest zoom level (inclusive).
   * @returns {number} A factor in ``(0, 1]``.
   */
  function budgetScaleForBBox(bbox, minZ, maxZ) {
    const [west, south, east, north] = bbox;
    // Whole tiles, not MB: buildBlob rounds bytes UP to the next MB, so a
    // count at exactly this budget is the largest that still reports
    // ``mb <= DOWNLOAD_CEILING_MB``.
    const budget = (DOWNLOAD_CEILING_MB * 1024 * 1024) / WORST_CASE_BYTES_PER_TILE;
    // World-fraction spans. Longitude is linear in the projection and
    // latitude is not, hence the Mercator y difference rather than a
    // degree one.
    const spanX = Math.abs(east - west) / 360.0;
    const spanY = Math.abs(lonLatToWorld(west, south)[1] - lonLatToWorld(west, north)[1]);
    // Coefficients of the cost quadratic a*s^2 + b*s + c, summed over the
    // band: a from the two spans together, b from each span against the
    // per-axis slack, c from the slack alone (the cost of a box small
    // enough to be a point — one to four tiles at every level).
    let a = 0;
    let b = 0;
    let c = 0;
    for (let z = minZ; z <= maxZ; z++) {
      const n = Math.pow(2, z);
      a += spanX * n * spanY * n;
      b += 2 * (spanX * n + spanY * n);
      c += 4;
    }
    if (a + b + c <= budget) return 1;
    if (a <= 0) {
      // A degenerate (zero-area) box can only exceed the budget through
      // its slack term, which no amount of scaling removes.
      return b > 0 ? Math.max(0, Math.min(1, (budget - c) / b)) : 0;
    }
    const scale = (-b + Math.sqrt(b * b + 4 * a * (budget - c))) / (2 * a);
    return Math.max(0, Math.min(1, scale));
  }

  /**
   * SNOW-568: whether a download of ``mb`` megabytes fits in the origin's
   * remaining storage quota.
   *
   * Takes the already-resolved ``navigator.storage.estimate()`` result
   * rather than calling it, so this stays a pure function of its
   * arguments (and unit-testable without a Storage API).
   *
   * The estimate is deliberately conservative on both sides. ``mb`` is the
   * download's own worst case (``WORST_CASE_BYTES_PER_TILE`` per tile —
   * real vector tiles are far smaller), and it is required to fit inside
   * ``STORAGE_HEADROOM_FACTOR`` of what's left rather than all of it: a
   * browser starts evicting an origin's storage as it approaches the
   * quota, and an area download that lands exactly at the limit would be
   * the first thing evicted. Refusing early costs the user a smaller
   * frame; not refusing costs them a download that appears to succeed and
   * is gone by the time they are offline and need it.
   *
   * Returns true when the estimate is unusable (Storage API absent, or a
   * browser reporting no quota) — an unknown quota must not block a
   * download that would have worked. The run's own ``QuotaExceededError``
   * handling is the backstop for that case.
   *
   * @param {{quota?: number, usage?: number}|null|undefined} estimate
   * @param {number} mb Estimated download size in megabytes.
   * @returns {boolean}
   */
  function hasStorageHeadroom(estimate, mb) {
    if (!estimate) return true;
    const quota = Number(estimate.quota);
    const usage = Number(estimate.usage);
    if (!Number.isFinite(quota) || quota <= 0) return true;
    if (!Number.isFinite(usage) || usage < 0) return true;
    const needed = Number(mb) * 1024 * 1024;
    if (!Number.isFinite(needed) || needed <= 0) return true;
    return needed <= (quota - usage) * STORAGE_HEADROOM_FACTOR;
  }

  self.pwaBasemapDownloadCore = Object.freeze({
    rangesToTileURLs: rangesToTileURLs,
    centreTileURL: centreTileURL,
    lonLatToTile: lonLatToTile,
    tileRangesForBBox: tileRangesForBBox,
    tileCount: tileCount,
    centreTile: centreTile,
    buildBlob: buildBlob,
    budgetScaleForBBox: budgetScaleForBBox,
    hasStorageHeadroom: hasStorageHeadroom,
    MICRO_BAND: MICRO_BAND,
    WORST_CASE_BYTES_PER_TILE: WORST_CASE_BYTES_PER_TILE,
    DOWNLOAD_CEILING_MB: DOWNLOAD_CEILING_MB,
    STORAGE_HEADROOM_FACTOR: STORAGE_HEADROOM_FACTOR,
  });
})();
