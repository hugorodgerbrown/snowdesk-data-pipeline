/*
 * static/js/basemap_download_core.js — Pure tile-math + size-estimate
 * helpers for the map's "Download basemap" control (SNOW-521).
 *
 * Dependency-free IIFE attaching ``self.pwaBasemapDownloadCore`` — same
 * idiom as ``basemap_cache_core.js`` (frozen export, no side effects).
 * Loaded on the page (window context) BEFORE ``static/js/map.js`` in
 * ``home.html``, unlike ``basemap_cache_core.js`` which is only ever
 * ``importScripts``'d into the service worker — this module has no SW
 * dependency, so it's a plain deferred ``<script>`` tag.
 *
 * Houses the slippy-map (Web Mercator) tile math previously inlined in
 * ``map.js``'s ``_lonToTileX`` / ``_latToTileY`` (SNOW-492), plus the
 * zoom-band and byte-estimate rules new to SNOW-521, so all of it can be
 * unit-tested directly (see tests/js/test_basemap_download_core.js)
 * without a MapLibre instance.
 *
 * Public API — attached to ``self.pwaBasemapDownloadCore``:
 *
 *   zoomBand(centreZoom, detailFloor)
 *     Returns ``{minZ, maxZ}`` for the band of zoom levels to download
 *     around the map's current view. ``minZ`` is one level shallower than
 *     the (rounded) current zoom, so a small zoom-out after reconnecting
 *     still hits a warm cache. ``maxZ`` reaches down to ``detailFloor``
 *     (or the current zoom, if the user is already deeper than that) —
 *     this is the "detail floor" the control is named for: a download
 *     always covers at least street/piste-label detail, not just whatever
 *     zoom level happened to be on screen when the button was pressed.
 *   enumerateTileURLs(template, bounds, minZ, maxZ, maxTiles)
 *     Enumerates the ``{z}/{x}/{y}`` tile URLs covering ``bounds``
 *     (``{west, east, north, south}``, degrees) across ``[minZ, maxZ]``,
 *     substituted into ``template``. Capped at ``maxTiles`` total (across
 *     the whole zoom range) so a zoomed-out viewport can't build an
 *     unbounded array — the caller decides the cap (see
 *     ``BASEMAP_DOWNLOAD_CEILING_TILES`` below).
 *   estimateBytes(tileCount, bytesPerTile)
 *     ``tileCount * bytesPerTile`` — a worst-case upper bound, not an
 *     average.
 *   formatUpToMB(bytes)
 *     ``bytes`` rounded UP to the nearest whole megabyte and rendered as
 *     e.g. ``"12 MB"`` — the docked bar's live readout is always phrased
 *     "up to N MB", so this must never under-report.
 *
 * Constants:
 *
 *   BASEMAP_DETAIL_FLOOR_ZOOM = 14
 *     The "detail floor" a download always reaches down to, regardless of
 *     how zoomed-out the triggering view was — piste/street-label detail
 *     on most basemap styles.
 *   BASEMAP_WORST_CASE_BYTES_PER_TILE = 100 * 1024
 *     A conservative upper bound for a dense Liberty-style vector tile, so
 *     "up to N MB" is a true ceiling rather than a typical-case average.
 *   BASEMAP_DOWNLOAD_CEILING_MB = 200
 *     The per-run hard ceiling. Over this, the control refuses (disables
 *     Download) and nudges the user to zoom in rather than starting a
 *     download the estimate can't bound sensibly.
 *   BASEMAP_DOWNLOAD_CEILING_TILES
 *     The tile-count ceiling derived from the two constants above
 *     (``ceil(BASEMAP_DOWNLOAD_CEILING_MB * 1MB / BASEMAP_WORST_CASE_BYTES_PER_TILE)``)
 *     — the actual cap ``map.js`` passes as ``enumerateTileURLs``'s
 *     ``maxTiles``.
 */

(function () {
  'use strict';

  const BASEMAP_DETAIL_FLOOR_ZOOM = 14;
  const BASEMAP_WORST_CASE_BYTES_PER_TILE = 100 * 1024;
  const BASEMAP_DOWNLOAD_CEILING_MB = 200;
  const BASEMAP_DOWNLOAD_CEILING_TILES = Math.ceil(
    (BASEMAP_DOWNLOAD_CEILING_MB * 1024 * 1024) / BASEMAP_WORST_CASE_BYTES_PER_TILE,
  );

  /**
   * Web Mercator longitude → tile X at zoom ``z``.
   * @param {number} lon
   * @param {number} z
   * @returns {number}
   */
  function _lonToTileX(lon, z) {
    return Math.floor(((lon + 180) / 360) * 2 ** z);
  }

  /**
   * Web Mercator latitude → tile Y at zoom ``z``.
   * @param {number} lat
   * @param {number} z
   * @returns {number}
   */
  function _latToTileY(lat, z) {
    const rad = (lat * Math.PI) / 180;
    return Math.floor(
      ((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * 2 ** z,
    );
  }

  /**
   * The band of zoom levels a download covers around ``centreZoom``.
   *
   * @param {number} centreZoom Current (fractional) map zoom.
   * @param {number} detailFloor The deepest zoom to always reach —
   *   ``BASEMAP_DETAIL_FLOOR_ZOOM`` in practice.
   * @returns {{minZ: number, maxZ: number}}
   */
  function zoomBand(centreZoom, detailFloor) {
    const centre = Math.round(centreZoom);
    const minZ = Math.max(0, centre - 1);
    const maxZ = Math.min(20, Math.max(centre, detailFloor));
    return { minZ: minZ, maxZ: maxZ };
  }

  /**
   * Enumerate the tile URLs covering ``bounds`` across ``[minZ, maxZ]``.
   *
   * @param {string} template A ``{z}``/``{x}``/``{y}`` tile URL template.
   * @param {{west: number, east: number, north: number, south: number}} bounds
   * @param {number} minZ
   * @param {number} maxZ
   * @param {number} maxTiles Hard cap on the returned array's length.
   * @returns {string[]}
   */
  function enumerateTileURLs(template, bounds, minZ, maxZ, maxTiles) {
    const urls = [];
    if (!template || !bounds || maxTiles <= 0) return urls;
    for (let z = minZ; z <= maxZ && urls.length < maxTiles; z++) {
      const xMin = _lonToTileX(bounds.west, z);
      const xMax = _lonToTileX(bounds.east, z);
      const yMin = _latToTileY(bounds.north, z);
      const yMax = _latToTileY(bounds.south, z);
      for (let x = xMin; x <= xMax && urls.length < maxTiles; x++) {
        for (let y = yMin; y <= yMax && urls.length < maxTiles; y++) {
          urls.push(
            template
              .replace('{z}', String(z))
              .replace('{x}', String(x))
              .replace('{y}', String(y)),
          );
        }
      }
    }
    return urls;
  }

  /**
   * Worst-case byte estimate for ``tileCount`` tiles.
   * @param {number} tileCount
   * @param {number} bytesPerTile
   * @returns {number}
   */
  function estimateBytes(tileCount, bytesPerTile) {
    return tileCount * bytesPerTile;
  }

  /**
   * ``bytes`` rounded up to the nearest whole MB, formatted as ``"N MB"``.
   * @param {number} bytes
   * @returns {string}
   */
  function formatUpToMB(bytes) {
    const mb = Math.max(0, Math.ceil(bytes / (1024 * 1024)));
    return `${mb} MB`;
  }

  self.pwaBasemapDownloadCore = Object.freeze({
    zoomBand: zoomBand,
    enumerateTileURLs: enumerateTileURLs,
    estimateBytes: estimateBytes,
    formatUpToMB: formatUpToMB,
    BASEMAP_DETAIL_FLOOR_ZOOM: BASEMAP_DETAIL_FLOOR_ZOOM,
    BASEMAP_WORST_CASE_BYTES_PER_TILE: BASEMAP_WORST_CASE_BYTES_PER_TILE,
    BASEMAP_DOWNLOAD_CEILING_MB: BASEMAP_DOWNLOAD_CEILING_MB,
    BASEMAP_DOWNLOAD_CEILING_TILES: BASEMAP_DOWNLOAD_CEILING_TILES,
  });
})();
