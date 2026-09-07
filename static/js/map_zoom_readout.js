/*
 * static/js/map_zoom_readout.js — the DEBUG-only zoom readout beside the
 * help roundel.
 *
 * HACK MODE, and a developer's instrument rather than a map feature: it
 * renders only where `apps.public.views.home` sets `zoom_readout_visible`,
 * which is `settings.DEBUG` alone.
 *
 * It exists because the offline download bands are defined in whole zoom
 * numbers — z0-7 shared per basemap, z8-9 around downloaded areas, z10-14
 * the area itself — and the camera is not a whole number. Working on any of
 * that means constantly asking "which side of a boundary is this view on",
 * and nothing on the map said.
 *
 * The two figures are different facts:
 *
 *   TILE ZOOM — what the map is actually fetching. Whole numbers only.
 *     MapLibre's `coveringZoomLevel` for a 512px vector source is
 *     `floor(camera)`, clamped to the source's own minzoom/maxzoom
 *     (measured: camera 9.99 fetches z9, camera 10.00 fetches z10). There
 *     is nothing in between — from 9.00 to 9.999 you are looking at z9
 *     tiles stretched, and the switch is a step. A 256px RASTER source
 *     (the slope overlay) rounds instead of flooring, so it changes at the
 *     .5 boundary — which is why this reads the vector sources rather than
 *     assuming one rule for the style.
 *   CAMERA ZOOM — the continuous value the map is really at.
 *
 * LOAD ORDER: runs at parse time and binds `MAP`, so it loads after map.js.
 * Outside the map bundle (nothing in the bundle reads it), so its tag sits
 * below the bundle's contiguous run — see tests/public/test_map_script_order.py.
 */

(function mapZoomReadoutInit() {
  const el = document.getElementById('map-zoom-readout');
  if (!el) return;
  if (typeof MAP === 'undefined' || !MAP) return;

  /**
   * The zoom the style's vector sources are fetching tiles at.
   *
   * Asks the transform rather than computing `floor(zoom)` here: the answer
   * depends on the source's tile size, its `roundZoom` flag and its own
   * minzoom/maxzoom clamp, and MapLibre owns all three. Read from the
   * FIRST vector source cache — every vector source in these styles shares
   * a tile size, so one answer serves; a style with none (the offline
   * fallback) yields null and the readout shows the camera alone.
   *
   * @returns {number|null}
   */
  function tileZoom() {
    try {
      const caches = MAP.style && MAP.style.sourceCaches;
      if (!caches) return null;
      for (const key of Object.keys(caches)) {
        const cache = caches[key];
        const source = cache && cache._source;
        if (!source || source.type !== 'vector') continue;
        const z = MAP.transform.coveringZoomLevel(source);
        return Math.max(source.minzoom || 0, Math.min(z, source.maxzoom ?? z));
      }
    } catch (_e) {
      // The style is mid-swap, or MapLibre's internals moved. The camera
      // reading below is always available and is the more useful half.
    }
    return null;
  }

  /**
   * Repaint the readout.
   *
   * @returns {void}
   */
  function render() {
    const camera = MAP.getZoom();
    const tile = tileZoom();
    // Numerals, not translatable text — the same treatment the downloads
    // panel gives its own sizes.
    el.textContent = tile === null ? camera.toFixed(1) : 'z' + tile;
    el.dataset.camera = camera.toFixed(2);
    el.setAttribute('title', 'tile ' + (tile === null ? '?' : 'z' + tile) + ' · camera ' + camera.toFixed(2));
  }

  // 'zoom' alone would miss the case that matters most on a slow style: the
  // tile zoom is clamped by the source, so it can change when the SOURCES
  // change rather than when the camera does.
  MAP.on('zoom', render);
  MAP.on('idle', render);
  document.addEventListener('snowdesk:basemap-changed', render);
  render();
})();
