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
 * browser's cache (map_3d_core.prefetchDemTiles; Mapterhorn caches them
 * for 7 days), so the terrain is there when the page opens. Once per
 * view: moving the map arms it again.
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

  const demTemplate = link.dataset.demTileUrl || '';
  let prefetched = '';

  /** Warm the cache with the elevation tiles around the 2-D view. */
  function prefetch() {
    const state = /** @type {any} */ (window).snowdeskMapState;
    const map = state && state.map;
    if (!map || !demTemplate) return;
    const centre = map.getCenter();
    const zoom = map.getZoom();
    const tiles = core.prefetchDemTiles(centre, zoom);
    const key = tiles.map((/** @type {{z: number, x: number, y: number}} */ t) => `${t.z}/${t.x}/${t.y}`).join(' ');
    if (key === prefetched) return;
    prefetched = key;
    for (const tile of tiles) {
      fetch(core.tileUrl(demTemplate, tile), { credentials: 'omit' }).catch(() => {});
    }
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
