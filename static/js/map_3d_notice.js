// @ts-check
/*
 * static/js/map_3d_notice.js — "3D is experimental" (SNOW-1470).
 *
 * The 3D view still has rough edges, so a reader in it is told so
 * (Hugo, 7 Nov 2026). #map-3d-notice is a floating top banner
 * (includes/_overlay_banner.html), rendered hidden in
 * public/partials/_map_embed.html only for a reader with the 3D view.
 *
 * Behaviour:
 *   - Shown whenever the map is in 3D: at load when map_view_3d.js has
 *     already set :root[data-map-view="3d"], and on every
 *     `snowdesk:map-view-changed` whose view is '3d'.
 *   - Hidden when the map goes flat, by the switch or on its own (a slow
 *     device, a lost WebGL context), so it never sits over a flat map.
 *   - The x (overlays.js's shared dismiss) hides it for the rest of the
 *     visit, whichever view the reader moves to. Nothing is stored, so a
 *     reload brings it back.
 *
 * A classic script loaded after map_view_3d.js. It owns no strings: the
 * copy is in the template.
 */

(function map3dNoticeInit() {
  'use strict';

  const banner = document.getElementById('map-3d-notice');
  if (!banner) return;

  // map_view_core.js's VIEWS.THREE_D.
  const THREE_D = '3d';
  let dismissed = false;

  /**
   * Show the banner in 3D, unless the reader closed it this visit.
   *
   * @param {string | undefined} view The map's view.
   */
  const sync = (view) => {
    banner.classList.toggle('hidden', dismissed || view !== THREE_D);
  };

  document.addEventListener('overlay:dismissed', (event) => {
    const detail = /** @type {CustomEvent} */ (event).detail;
    if (detail && detail.overlay === banner) dismissed = true;
  });

  document.addEventListener('snowdesk:map-view-changed', (event) => {
    const detail = /** @type {CustomEvent} */ (event).detail;
    sync(detail ? detail.view : undefined);
  });

  sync(document.documentElement.dataset.mapView);
})();
