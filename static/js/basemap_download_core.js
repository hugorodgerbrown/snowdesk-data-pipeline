/*
 * static/js/basemap_download_core.js — Pure tile-URL helpers for the
 * map's per-region "Download basemap" icons (SNOW-521 rework).
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
 * browser does no tile enumeration or byte-estimate arithmetic of its
 * own any more. This module has shrunk to the two pure functions that
 * turn that server-computed data into URLs:
 *
 * Public API — attached to ``self.pwaBasemapDownloadCore``:
 *
 *   rangesToTileURLs(template, blob)
 *     Expands a full basemap_download blob's ``z`` tile-index ranges
 *     (``{"<z>": [xmin, xmax, ymin, ymax]}``, as fetched from
 *     ``/api/region-basemap-tiles/?id=...``) into the full list of
 *     ``{z}/{x}/{y}`` tile URLs, substituted into ``template``. Returns
 *     ``[]`` for a falsy ``template`` or ``blob``, or a blob with no
 *     ``z`` ranges.
 *   centreTileURL(template, summary)
 *     Builds the single tile URL for ``summary.centre_tile``
 *     (``{z, x, y}``) — the "done-probe" key: the client checks whether
 *     this one tile is present in the pinned cache as a proxy for "this
 *     region's download completed" (matches the region's server-picked
 *     centre tile at its tier's detail floor). Returns ``null`` for a
 *     falsy ``template``/``summary``/``summary.centre_tile``.
 *
 * The zoom-band, tile-enumeration, and byte-estimate helpers this module
 * used to hold (``zoomBand``, ``enumerateTileURLs``, ``estimateBytes``,
 * ``formatUpToMB``, and the underlying ``_lonToTileX``/``_latToTileY``
 * slippy-map math) moved server-side to
 * ``regions/services/basemap_tiles.py`` — the region-boundary geometry
 * they depended on lives in the database, and precomputing per-tier
 * once (region geometry and the tile grid are both static) beats
 * re-deriving the same numbers in every client on every viewport move.
 */

(function () {
  'use strict';

  /**
   * Expand a full basemap_download blob's ``z`` ranges into tile URLs.
   *
   * @param {string} template A ``{z}``/``{x}``/``{y}`` tile URL template.
   * @param {{z?: Object<string, number[]>}} blob The full blob fetched from
   *   ``/api/region-basemap-tiles/?id=...`` (``{band, count, mb,
   *   over_ceiling, centre_tile, z}`` — see
   *   ``regions/services/basemap_tiles.py``'s module docstring).
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
   * Build the single "done-probe" tile URL for a region's download summary.
   *
   * @param {string} template A ``{z}``/``{x}``/``{y}`` tile URL template.
   * @param {{centre_tile?: {z: number, x: number, y: number}}} summary A
   *   region's download summary (the small ``{count, mb, over_ceiling,
   *   centre_tile}`` shape inlined on the geojson endpoints' ``properties.
   *   download``, or the equivalent fields on a full blob).
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

  self.pwaBasemapDownloadCore = Object.freeze({
    rangesToTileURLs: rangesToTileURLs,
    centreTileURL: centreTileURL,
  });
})();
