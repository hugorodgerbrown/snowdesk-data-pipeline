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
 * SNOW-583 clips a REGION download to its real boundary plus a margin
 * tile server-side (``apps.regions.services.basemap_tiles.
 * build_region_blob``), so a region's ``z`` entry is no longer always the
 * 4-int rectangle ``[xmin, xmax, ymin, ymax]`` — it is now
 * ``{"<y>": [xmin, xmax]}``, one row span per present row. The
 * CUSTOM-AREA blob ``buildBlob`` (below) produces is UNCHANGED and stays
 * rectangular — a user-drawn rectangle genuinely is one — and a stale
 * `Cache-Control: max-age=86400` response can still hand back an
 * old-shape rectangle for up to 24h after a deploy. ``zoomRows`` is the
 * one accessor every consumer of a blob's ``z`` routes through so both
 * shapes are handled in exactly one place.
 *
 * Public API — attached to ``self.pwaBasemapDownloadCore``:
 *
 *   zoomRows(zEntry)
 *     Normalises one zoom level's ``z`` entry — either shape — to
 *     ``{"<y>": [xmin, xmax]}``. See the SNOW-583 note above; every
 *     function below that walks a blob's tiles goes through this rather
 *     than assuming a rectangle.
 *   rangesToTileURLs(template, blob)
 *     Expands a full basemap_download blob's ``z`` tile-index ranges
 *     (as fetched from ``/api/region-basemap-tiles/?id=...`` OR produced
 *     locally by ``buildBlob`` below) into the full list of
 *     ``{z}/{x}/{y}`` tile URLs, substituted into ``template``. Returns
 *     ``[]`` for a falsy ``template`` or ``blob``, or a blob with no
 *     ``z`` ranges.
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
 *
 * SNOW-569 and the tile-grid rework that followed it add a second
 * client-only group — the geometry a download's on-map progress grid
 * needs (``geometryBounds``, ``bboxPolygon``,
 * ``tileBounds``, ``gridZoomFor``, ``tileGridPlan``). Like
 * ``budgetScaleForBBox`` these have no ``basemap_tiles.py`` counterpart
 * and need none: the server never draws anything. They live here rather
 * than in ``map.js`` because they are pure functions of geometry, which
 * makes them unit-testable without a MapLibre instance — everything in
 * ``map.js`` that touches them is a side effect on a live map.
 *
 *   geometryBounds(geometry)
 *     ``[west, south, east, north]`` of a Polygon/MultiPolygon, or
 *     ``null`` for anything else. Antimeridian-naive, which is safe for
 *     every geometry this project downloads (Alpine regions and a
 *     user-framed rectangle).
 *   bboxPolygon(bbox)
 *     A ``bbox`` as a GeoJSON Polygon — the custom-area download's
 *     equivalent of a region's own boundary.
 *   tileBounds(z, x, y)
 *     The ground one tile covers, as ``[west, south, east, north]`` —
 *     the inverse of ``lonLatToTile``, and the only place the grid's
 *     squares get their geometry.
 *   cachedTilesFromURLs(template, cachedURLs, zoom)
 *     The tiles a cache actually holds, read back out of its URLs — the
 *     pure half of the "cached tiles" overlay.
 *   gridZoomFor(blob)
 *     Which single zoom level of a download's band to draw the grid at
 *     — the deepest, so a square is a real tile.
 *   tileGridPlan(template, blob)
 *     The grid's cells AND the run's tile URLs ordered to fill them one
 *     at a time — see its docstring for why the ordering is the feature.
 *   blobFullyCached(template, blob, cached)
 *     SNOW-570, widened by SNOW-583: whether EVERY tile in ``blob``'s own
 *     ``z`` (either shape, via ``zoomRows``) is present in ``cached`` —
 *     "is this download actually available offline?". Replaces the old
 *     ``downloadedIds(template, entries, cachedURLs)``/internal
 *     ``_bboxFullyCached`` pair now that the only two callers (the
 *     per-region and custom-area done-probes) each ask about ONE blob at
 *     a time rather than a whole list of regions — SNOW-583 dropped the
 *     "Downloaded areas" overlay's per-region ring (see
 *     ``docs/decisions/region-downloads-clip-custom-areas-dont.md``), the
 *     last caller that needed the list form.
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
   * Normalise one zoom level's ``z`` entry to ``{"<y>": [xmin, xmax]}``,
   * whichever of the two shapes it arrived in (SNOW-583).
   *
   * A full blob's ``z[zoom]`` is one of:
   *   - a 4-int rectangle ``[xmin, xmax, ymin, ymax]`` — ``buildBlob``'s
   *     own shape (the custom-area download, always a rectangle) and
   *     what a stale `max-age=86400` region response can still be for up
   *     to 24h after a deploy — expanded here into one identical span
   *     per row.
   *   - an object ``{"<y>": [xmin, xmax]}`` — a clipped region blob's
   *     shape (``apps.regions.services.basemap_tiles.build_region_blob``),
   *     taken as given.
   *
   * Every consumer of a blob's ``z`` below (``rangesToTileURLs``,
   * ``tileCount``, ``tileGridPlan``, ``blobFullyCached``) routes through
   * this rather than assuming either shape directly.
   *
   * @param {number[] | Object<string, number[]> | undefined} zEntry
   * @returns {Object<string, [number, number]>}
   */
  function zoomRows(zEntry) {
    if (!zEntry) return {};
    if (Array.isArray(zEntry)) {
      const [xmin, xmax, ymin, ymax] = zEntry;
      const rows = {};
      for (let y = ymin; y <= ymax; y++) rows[String(y)] = [xmin, xmax];
      return rows;
    }
    return zEntry;
  }

  /**
   * Expand a full basemap_download blob's ``z`` ranges into tile URLs.
   *
   * @param {string} template A ``{z}``/``{x}``/``{y}`` tile URL template.
   * @param {{z?: Object<string, number[] | Object<string, number[]>>}} blob
   *   The full blob — either fetched from
   *   ``/api/region-basemap-tiles/?id=...`` or built locally by
   *   ``buildBlob`` (``{band, count, mb, over_ceiling, centre_tile, z}``
   *   — see ``regions/services/basemap_tiles.py``'s module docstring).
   *   Each zoom's ``z[zoom]`` is either shape ``zoomRows`` accepts.
   * @returns {string[]}
   */
  function rangesToTileURLs(template, blob) {
    const urls = [];
    if (!template || !blob || !blob.z) return urls;
    for (const z of Object.keys(blob.z)) {
      const rows = zoomRows(blob.z[z]);
      for (const y of Object.keys(rows)) {
        const [xmin, xmax] = rows[y];
        for (let x = xmin; x <= xmax; x++) {
          urls.push(template.replace('{z}', z).replace('{x}', String(x)).replace('{y}', y));
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
   * Deliberate re-port of ``basemap_tiles.tile_count`` (rectangle
   * ranges) — but, via ``zoomRows``, also correct for a clipped region
   * blob's row-span ``z`` (SNOW-583's ``row_tile_count`` counterpart),
   * so one function serves both shapes.
   *
   * @param {Object<string, number[] | Object<string, number[]>>} ranges
   *   The ``{"<z>": ...}`` shape returned by ``tileRangesForBBox``, or a
   *   full blob's ``z``.
   * @returns {number}
   */
  function tileCount(ranges) {
    let total = 0;
    for (const key of Object.keys(ranges)) {
      const rows = zoomRows(ranges[key]);
      for (const y of Object.keys(rows)) {
        const [xmin, xmax] = rows[y];
        total += xmax - xmin + 1;
      }
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

  /**
   * The polygon rings of ``geometry``, as a flat list — one entry per
   * ring, outer and inner alike. ``[]`` for anything that isn't a
   * Polygon or MultiPolygon.
   *
   * @param {{type?: string, coordinates?: any}} geometry
   * @returns {number[][][]}
   */
  function _rings(geometry) {
    if (!geometry || !geometry.coordinates) return [];
    if (geometry.type === 'Polygon') return geometry.coordinates;
    if (geometry.type === 'MultiPolygon') return geometry.coordinates.flat();
    return [];
  }

  /**
   * ``[west, south, east, north]`` of a Polygon/MultiPolygon.
   *
   * Antimeridian-naive: a geometry straddling ±180° would get a bounds
   * spanning the whole globe. Nothing this project downloads does — a
   * region boundary and a user-framed rectangle are both small and
   * Alpine — and the fill only needs the north/south edges anyway.
   *
   * @param {{type?: string, coordinates?: any}} geometry
   * @returns {[number, number, number, number] | null} ``null`` for a
   *   non-polygon geometry or one with no positions.
   */
  function geometryBounds(geometry) {
    let west = Infinity;
    let south = Infinity;
    let east = -Infinity;
    let north = -Infinity;
    for (const ring of _rings(geometry)) {
      for (const [lon, lat] of ring) {
        if (lon < west) west = lon;
        if (lon > east) east = lon;
        if (lat < south) south = lat;
        if (lat > north) north = lat;
      }
    }
    if (!Number.isFinite(west) || !Number.isFinite(south)) return null;
    return [west, south, east, north];
  }

  /**
   * ``bbox`` as a GeoJSON Polygon, wound anticlockwise from its
   * south-west corner and explicitly closed.
   *
   * @param {[number, number, number, number]} bbox ``[west, south, east,
   *   north]`` in degrees.
   * @returns {{type: string, coordinates: number[][][]}}
   */
  function bboxPolygon(bbox) {
    const [west, south, east, north] = bbox;
    return {
      type: 'Polygon',
      coordinates: [
        [
          [west, south],
          [east, south],
          [east, north],
          [west, north],
          [west, south],
        ],
      ],
    };
  }

  /**
   * Which tiles of ``cachedURLs`` belong to ``template``, as ``{z, x, y}``.
   *
   * The inverse of the substitution ``rangesToTileURLs`` performs: the
   * cache stores URLs, and the "cached tiles" overlay needs the tile
   * indices back so it can draw each one's footprint. Reading them out of
   * the URL is what makes the overlay honest — it renders what Cache
   * Storage actually holds, not what a download record claims.
   *
   * Matching is per-template, so switching basemap changes the answer:
   * tiles cached for one basemap's origin are genuinely not cached for
   * another's.
   *
   * The placeholder ORDER is read from the template rather than assumed.
   * Most templates are ``{z}/{x}/{y}``, but an ESRI VectorTileServer
   * source is ``{z}/{y}/{x}`` (see ``map.js``'s style normalisation), and
   * reading those transposed would draw every square in the wrong place.
   *
   * @param {string} template A ``{z}``/``{x}``/``{y}`` tile URL template.
   * @param {Iterable<string>} cachedURLs URLs present in the cache.
   * @param {number} [zoom] Keep only tiles at this zoom. Omit for all of
   *   them — but note a download spans a whole band, so an unfiltered
   *   result overlaps itself five deep.
   * @returns {Array<{z: number, x: number, y: number}>}
   */
  function cachedTilesFromURLs(template, cachedURLs, zoom) {
    const out = [];
    if (!template || !cachedURLs) return out;
    const order = [];
    template.replace(/\{(z|x|y)\}/g, (match, key) => {
      order.push(key);
      return match;
    });
    if (order.length !== 3) return out;
    const pattern = template
      .replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      .replace(/\\\{(z|x|y)\\\}/g, '(\\d+)');
    let re;
    try {
      re = new RegExp('^' + pattern + '$');
    } catch (_e) {
      // A template that can't be made into a pattern matches nothing,
      // which is the same answer as "no tiles cached for this basemap".
      return out;
    }
    for (const url of cachedURLs) {
      const match = re.exec(url);
      if (!match) continue;
      const tile = {};
      for (let i = 0; i < order.length; i++) tile[order[i]] = Number(match[i + 1]);
      if (typeof zoom === 'number' && tile.z !== zoom) continue;
      out.push(tile);
    }
    return out;
  }

  /**
   * The ``[west, south, east, north]`` bounds of one Web Mercator tile.
   *
   * The inverse of ``lonLatToTile``: that floors a projected position to
   * a tile index, this returns the ground the whole index covers. Round
   * tripping is therefore one-way-exact — ``lonLatToTile`` of any point
   * inside these bounds gives back ``(x, y)``, but the bounds are the
   * tile's full extent, not the point that produced it.
   *
   * Client-only, like the rest of the tile-grid group: the server
   * enumerates tile INDICES and never needs to know where they sit.
   *
   * @param {number} z Zoom level.
   * @param {number} x Tile x index.
   * @param {number} y Tile y index.
   * @returns {[number, number, number, number]} ``[west, south, east,
   *   north]`` in degrees.
   */
  function tileBounds(z, x, y) {
    const n = Math.pow(2, z);
    const west = (x / n) * 360.0 - 180.0;
    const east = ((x + 1) / n) * 360.0 - 180.0;
    // Mercator y runs southward, so y+1 is the SOUTHERN edge.
    const north = _mercatorYToLat(y / n);
    const south = _mercatorYToLat((y + 1) / n);
    return [west, south, east, north];
  }

  /**
   * A Web Mercator world y (``[0, 1]``, southward) back to a latitude.
   *
   * The inverse of ``lonLatToWorld``'s y term, factored out because
   * ``tileBounds`` needs it for both of a tile's horizontal edges.
   *
   * @param {number} y World y in ``[0, 1]``.
   * @returns {number} A latitude in degrees.
   */
  function _mercatorYToLat(y) {
    return (Math.atan(Math.sinh(Math.PI * (1 - 2 * y))) * 180) / Math.PI;
  }

  /**
   * The zoom level to draw a download's progress grid at: the deepest in
   * the blob.
   *
   * The download spans a whole band (z10-14 — see ``MICRO_BAND``) and
   * every level covers the SAME ground, so the grid has to pick one zoom
   * or it would paint the same area five times over. It picks the band's
   * detail floor, which makes each square a REAL tile — the finest unit
   * the run actually fetches (~1.7 km across in the Alps at z14), and
   * mostly one tile per square.
   *
   * Deepest rather than a "give me at least N squares" rule: cell count
   * quadruples per level, so any such threshold lands on a different
   * level for different-sized areas — a big region drew 27 km blocks
   * while a small one drew 3 km ones, which read as an inconsistent
   * animation rather than a scale. Deepest is both the finest grid
   * available and the only choice that needs no tuning constant.
   *
   * @param {{z?: Object<string, number[]>}} blob A full download blob.
   * @returns {number | null} A zoom level, or ``null`` for a blob with no
   *   ranges to draw.
   */
  function gridZoomFor(blob) {
    if (!blob || !blob.z) return null;
    const zooms = Object.keys(blob.z)
      .map(Number)
      .filter((z) => Number.isFinite(z));
    if (!zooms.length) return null;
    return Math.max.apply(null, zooms);
  }

  /**
   * The grid cell a tile belongs to, in cell-index space at ``gridZ``.
   *
   * A tile deeper than the grid sits inside exactly one cell, found by
   * shifting its indices right. A tile SHALLOWER than the grid (every
   * level above the band's floor, which is where the grid is drawn) spans
   * many cells; it is assigned to the north-westmost one it covers, so
   * every tile lands in exactly one cell and the per-cell totals sum to
   * the run's tile count.
   *
   * That assignment is deliberately not "the cells this tile covers".
   * The grid is a progress indicator, not a coverage map — one tile
   * completing several cells would let squares light up for ground whose
   * own detail tiles have not been fetched yet.
   *
   * ``gridRows`` clamps the result into the grid's own footprint. A
   * coarse tile starts WEST and NORTH of the area it was fetched for (its
   * indices floor to a wider grid), so its north-westmost fine cell can
   * fall outside the rows the grid draws — which showed up as a lattice
   * of stray squares scattered off the edge of the download area, over
   * ground the run does not cover. Clamping folds those onto the edge
   * cell the tile actually overlaps.
   *
   * SNOW-583: a clipped region blob's grid rows are no longer necessarily
   * CONTIGUOUS in y (a concave boundary can leave a gap between two
   * present rows), so the old single clamp — bound ``cx``/``cy`` each
   * independently into the grid zoom's own rectangle — could land a cell
   * on a row that has no tiles at all. The clamp is therefore two steps,
   * row first: ``cy`` snaps to the nearest row ``gridRows`` actually has,
   * THEN ``cx`` clamps into that row's own ``[xmin, xmax]`` span. For a
   * rectangular grid (every row present, one shared span) this reduces to
   * exactly the old single clamp — the ``buildBlob``/custom-area path is
   * untouched in substance, only in shape of the arithmetic.
   *
   * @param {number} z The tile's zoom level.
   * @param {number} x The tile's x index.
   * @param {number} y The tile's y index.
   * @param {number} gridZ The grid's zoom level.
   * @param {Object<string, [number, number]>} gridRows The grid zoom's own
   *   ``{"<y>": [xmin, xmax]}`` rows (``zoomRows(blob.z[gridZ])``).
   * @returns {[number, number]} ``[cellX, cellY]``.
   */
  function _cellForTile(z, x, y, gridZ, gridRows) {
    const shift = z - gridZ;
    let cx;
    let cy;
    if (shift >= 0) {
      const step = Math.pow(2, shift);
      cx = Math.floor(x / step);
      cy = Math.floor(y / step);
    } else {
      const scale = Math.pow(2, -shift);
      cx = x * scale;
      cy = y * scale;
    }
    cy = _nearestRow(cy, gridRows);
    const [xmin, xmax] = gridRows[String(cy)];
    cx = Math.max(xmin, Math.min(cx, xmax));
    return [cx, cy];
  }

  /**
   * The row (``y``) in ``gridRows`` closest to ``cy`` — exact when ``cy``
   * is itself present, otherwise the nearest neighbour. Ties (equidistant
   * above and below) resolve to the smaller ``y``, i.e. the row further
   * NORTH — an arbitrary but deterministic choice; a boundary narrow
   * enough to produce one is already a rare, single-row sliver.
   *
   * @param {number} cy A candidate row index, possibly absent from
   *   ``gridRows``.
   * @param {Object<string, [number, number]>} gridRows The grid zoom's
   *   own rows.
   * @returns {number} A row index guaranteed present in ``gridRows``.
   */
  function _nearestRow(cy, gridRows) {
    let best = null;
    let bestDistance = Infinity;
    for (const key of Object.keys(gridRows)) {
      const y = Number(key);
      const distance = Math.abs(y - cy);
      if (distance < bestDistance || (distance === bestDistance && y < best)) {
        best = y;
        bestDistance = distance;
      }
    }
    return best;
  }

  /**
   * Plan a download as a grid of cells plus the tile URLs that fill them.
   *
   * Returns the URL list to hand to the service worker AND the squares
   * the map draws, with the two tied together: ``urls[i]`` belongs to
   * ``cells[cellOfURL[i]]``, and a cell is complete once all ``total`` of
   * its tiles have settled.
   *
   * **The URLs come back grouped by cell**, which is the whole point.
   * ``rangesToTileURLs`` emits them zoom-major, so a run works through
   * every z10 tile, then every z11, and so on — five passes over the same
   * ground, with no cell finishing until the last pass reaches it.
   * Grouping instead means a cell's tiles are fetched consecutively and
   * the square lights up when they land, so the grid fills in cell by
   * cell. The SET of URLs is identical either way, so ordering costs the
   * download nothing; it is purely what makes the progress legible.
   *
   * Cells are ordered **bottom-up in a boustrophedon**: rows from the
   * south northwards, and each row runs the opposite way to the one below
   * it — west to east, then east to west, and so on. That matches the
   * download roundel, which fills from its bottom edge upwards; the map
   * and the icon are two readouts of one run, so they fill in the same
   * direction.
   *
   * Alternating rather than restarting every row at the west edge because
   * a plain raster scan jumps the length of the area at each row end,
   * which reads as a repeating wipe. Serpentine keeps consecutive cells
   * adjacent, so the filled area grows like something pouring in.
   *
   * Note rows are ordered by DESCENDING cell y: Web Mercator tile y
   * increases southward, so the southernmost row is the highest y and has
   * to come first. Deterministic, so a re-run of the same area fills in
   * the same order.
   *
   * @param {string} template A ``{z}``/``{x}``/``{y}`` tile URL template.
   * @param {{z?: Object<string, number[] | Object<string, number[]>>}} blob
   *   A full download blob — fetched from
   *   ``/api/region-basemap-tiles/?id=...`` (rectangle or clipped row
   *   spans — either shape, via ``zoomRows``) or built by ``buildBlob``
   *   (always a rectangle).
   * @returns {{gridZ: number, cells: Array<{x: number, y: number, bbox:
   *   [number, number, number, number], total: number}>, urls: string[],
   *   cellOfURL: number[]} | null} ``null`` when there is nothing to draw.
   */
  function tileGridPlan(template, blob) {
    const gridZ = gridZoomFor(blob);
    if (!template || gridZ === null) return null;

    // Bucket every tile in the blob by the cell it lands in, keyed on the
    // cell's own indices so the two loops below agree on identity.
    const gridRows = zoomRows(blob.z[String(gridZ)]);
    const buckets = new Map();
    for (const key of Object.keys(blob.z)) {
      const z = Number(key);
      if (!Number.isFinite(z)) continue;
      const rows = zoomRows(blob.z[key]);
      for (const rowKey of Object.keys(rows)) {
        const y = Number(rowKey);
        const [xmin, xmax] = rows[rowKey];
        for (let x = xmin; x <= xmax; x++) {
          const [cx, cy] = _cellForTile(z, x, y, gridZ, gridRows);
          const cellKey = cx + ':' + cy;
          let bucket = buckets.get(cellKey);
          if (!bucket) {
            bucket = { x: cx, y: cy, tiles: [] };
            buckets.set(cellKey, bucket);
          }
          bucket.tiles.push([z, x, y]);
        }
      }
    }
    if (!buckets.size) return null;

    // Group into rows, then walk them south to north, reversing every
    // other row so the sweep never jumps back across the area.
    const byRow = new Map();
    for (const bucket of buckets.values()) {
      if (!byRow.has(bucket.y)) byRow.set(bucket.y, []);
      byRow.get(bucket.y).push(bucket);
    }
    // Descending y: the southernmost row is the highest tile index.
    const rowKeys = Array.from(byRow.keys()).sort((a, b) => b - a);
    const ordered = [];
    rowKeys.forEach((y, rowIndex) => {
      const row = byRow.get(y).sort((a, b) => a.x - b.x);
      if (rowIndex % 2 === 1) row.reverse();
      for (const bucket of row) ordered.push(bucket);
    });
    const cells = [];
    const urls = [];
    const cellOfURL = [];
    ordered.forEach((bucket, index) => {
      for (const [z, x, y] of bucket.tiles) {
        urls.push(
          template
            .replace('{z}', String(z))
            .replace('{x}', String(x))
            .replace('{y}', String(y)),
        );
        cellOfURL.push(index);
      }
      cells.push({
        x: bucket.x,
        y: bucket.y,
        bbox: tileBounds(gridZ, bucket.x, bucket.y),
        total: bucket.tiles.length,
      });
    });
    return { gridZ: gridZ, cells: cells, urls: urls, cellOfURL: cellOfURL };
  }

  /**
   * Whether EVERY tile in ``blob``'s own ``z`` is present in ``cached`` —
   * "is this download actually available offline?" (SNOW-570; widened by
   * SNOW-583 from a bbox rectangle to whatever tile set the blob itself
   * carries, via ``zoomRows`` — a clipped region blob is checked against
   * exactly the tiles its download fetched, not a bbox super-set of them).
   *
   * Deliberately not the centre-tile proxy an earlier version of the
   * roundel's own done-probe used. That proxy is fair only for a question
   * that only ever asks about a download the user made AS THAT DOWNLOAD:
   * the centre tile is then a witness that that particular run completed.
   * It is NOT fair when two download shapes write to one pinned cache over
   * the same zoom band with the same URL template, so their tiles are
   * indistinguishable strings — a custom-area download whose frame merely
   * crosses a region's centre caches that region's centre tile, and a
   * centre-tile probe would then read the whole region as downloaded on
   * the strength of one tile it never covered (and would equally MISS a
   * region almost entirely covered whose centre falls outside the frame).
   *
   * Full coverage is the honest question and it is nearly free: the caller
   * has already paid for the answer with one ``cache.keys()`` pass, and
   * every tile after the first is a Set lookup. A download is at most a
   * few hundred tiles across the micro band, so the whole check costs
   * single-digit milliseconds — which is why this checks all of them
   * rather than sampling. It is also the RIGHT answer whoever cached the
   * tiles: an area wholly inside a larger download genuinely is available
   * offline, and should say so.
   *
   * @param {string} template A ``{z}``/``{x}``/``{y}`` tile URL template —
   *   the ACTIVE basemap's, which is what makes the answer per-basemap.
   * @param {{z?: Object<string, number[] | Object<string, number[]>>}} blob
   *   A full download blob — fetched from
   *   ``/api/region-basemap-tiles/?id=...`` or built locally by
   *   ``buildBlob`` (custom area).
   * @param {Set<string> | string[]} cached The pinned cache's URLs.
   * @returns {boolean} ``false`` for a falsy template or blob (or one with
   *   no ``z``), and for an empty tile set — "nothing is cached" must
   *   never read as "all of nothing is cached, so yes".
   */
  function blobFullyCached(template, blob, cached) {
    if (!template || !blob || !blob.z) return false;
    const cachedSet = cached instanceof Set ? cached : new Set(cached || []);
    let seen = 0;
    for (const z of Object.keys(blob.z)) {
      const rows = zoomRows(blob.z[z]);
      for (const y of Object.keys(rows)) {
        const [xmin, xmax] = rows[y];
        for (let x = xmin; x <= xmax; x++) {
          const url = template.replace('{z}', z).replace('{x}', String(x)).replace('{y}', y);
          if (!cachedSet.has(url)) return false;
          seen++;
        }
      }
    }
    return seen > 0;
  }

  self.pwaBasemapDownloadCore = Object.freeze({
    zoomRows: zoomRows,
    rangesToTileURLs: rangesToTileURLs,
    centreTileURL: centreTileURL,
    lonLatToTile: lonLatToTile,
    tileRangesForBBox: tileRangesForBBox,
    tileCount: tileCount,
    centreTile: centreTile,
    buildBlob: buildBlob,
    budgetScaleForBBox: budgetScaleForBBox,
    hasStorageHeadroom: hasStorageHeadroom,
    geometryBounds: geometryBounds,
    bboxPolygon: bboxPolygon,
    tileBounds: tileBounds,
    cachedTilesFromURLs: cachedTilesFromURLs,
    gridZoomFor: gridZoomFor,
    tileGridPlan: tileGridPlan,
    blobFullyCached: blobFullyCached,
    MICRO_BAND: MICRO_BAND,
    WORST_CASE_BYTES_PER_TILE: WORST_CASE_BYTES_PER_TILE,
    DOWNLOAD_CEILING_MB: DOWNLOAD_CEILING_MB,
    STORAGE_HEADROOM_FACTOR: STORAGE_HEADROOM_FACTOR,
  });
})();
