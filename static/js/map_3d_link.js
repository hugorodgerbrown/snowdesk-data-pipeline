// @ts-check
/*
 * static/js/map_3d_link.js — /map/'s "3-D" roundel: open the 3-D map on the
 * same view.
 *
 * Rendered only inside the map_3d flag (public/partials/_map_embed.html).
 * The link's href is plain /map/3d/, so it works without this file; on a
 * click this rewrites it to carry the 2-D map's camera and its switches
 * (map_3d_core.viewQuery):
 *
 *   - Steep terrain ← the terrain filter's switch;
 *   - SAC tours     ← the SAC ski-tours switch (on unless switched off);
 *   - Wildlife      ← the wildlife rest zones' switch.
 *
 * A flat 2-D camera is tilted to the 3-D map's opening pitch: opening a
 * 3-D map looking straight down shows nothing the 2-D one did not.
 *
 * A friend sent a route by a 3-D reader gets the roundel outside the flag,
 * with that share's token on `data-route-share` (apps/map/map_3d.py
 * pending_route_share_for_3d). The link keeps it as `?route_share=`, which
 * is what opens the 3-D map for them with the route on it.
 *
 * PREFETCH. The moment a pointer, a finger or focus reaches the roundel,
 * the elevation tiles the 3-D view asks for first are fetched into the
 * browser's cache (map_3d_terrain.createPrefetcher, over
 * map_3d_core.prefetchDemTiles; Mapterhorn caches them for 7 days), so the
 * terrain is there when the page opens. Once per view: moving the map arms
 * it again.
 *
 * A reader in both map_3d and map_modes gets /map/'s own 3-D view instead
 * (map_view_3d.js, SNOW-1449), and this link is not rendered for them.
 */
(function () {
  'use strict';

  const link = /** @type {HTMLAnchorElement|null} */ (document.getElementById('map-3d-link'));
  const core = self.pwaMap3dCore;
  if (!link || !core) return;

  /** Below this the 2-D view counts as flat, and takes the 3-D opening pitch. */
  const FLAT_PITCH = 20;

  /**
   * One stored switch, as map_shared.js readBoolStorage reads it.
   *
   * @param {string} key
   * @param {boolean} fallback
   * @returns {boolean}
   */
  function readSwitch(key, fallback) {
    try {
      const value = window.localStorage.getItem(key);
      return value === null ? fallback : value === 'true';
    } catch (err) {
      return fallback;
    }
  }

  // map_3d_terrain.js's prefetch (SNOW-1449), shared with the 3D roundel
  // that /map/'s own 3-D view puts in this link's place.
  const prefetchAround = self.pwaMap3dTerrain
    ? self.pwaMap3dTerrain.createPrefetcher(link.dataset.demTileUrl || '')
    : null;

  /** Warm the cache with the elevation tiles around the 2-D view. */
  function prefetch() {
    const state = /** @type {any} */ (window).snowdeskMapState;
    const map = state && state.map;
    if (!map || !prefetchAround) return;
    prefetchAround(map.getCenter(), map.getZoom());
  }

  for (const type of ['pointerenter', 'focus', 'touchstart']) {
    link.addEventListener(type, prefetch, { passive: true });
  }

  link.addEventListener('click', () => {
    const state = /** @type {any} */ (window).snowdeskMapState;
    const map = state && state.map;
    if (!map) return;
    const pitch = map.getPitch();
    const query = core.viewQuery(
      {
        center: map.getCenter(),
        zoom: map.getZoom(),
        pitch: pitch < FLAT_PITCH ? core.OPENING_CAMERA.pitch : pitch,
        bearing: map.getBearing(),
      },
      {
        steep: readSwitch('snowdesk.map.overlay.terrain_filter', false),
        tours: readSwitch('snowdesk.map.overlay.ski_routes', true),
        wildlife: readSwitch('snowdesk.map.overlay.rest_zones', false),
      },
    );
    const routeShare = link.dataset.routeShare;
    const prefix = routeShare ? `route_share=${encodeURIComponent(routeShare)}&` : '';
    link.href = `${link.pathname}?${prefix}${query}`;
  });
})();
