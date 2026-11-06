---
name: a-3d-map-is-maplibre-terrain
description: 3-D terrain map — /map/3d/ sandbox on MapLibre setTerrain (raster-dem, Terrarium DEM), not deck.gl, CesiumJS or three.js; map_3d flag
status: current
last-reviewed: 2026-11-06
---

# A 3-D map is MapLibre's own terrain

**Decision.** SNOW-1438 (5 Nov 2026). The 3-D view of the map is MapLibre's
built-in terrain: `map.setTerrain` on a `raster-dem` source, in the MapLibre
4.7.1 we already vendor. It ships first as a sandbox page, `/map/3d/`
(`apps/map/map_3d.py`, `static/js/map_3d.js`, `static/js/map_3d_core.js`),
behind the `map_3d` flag, separate from `/map/`. It shows Steep terrain, SAC's
ski tours, the reader's own routes and the resorts, because Hugo found 3-D
most useful "in terrain mode, when looking at routes".

- **Elevation** is Mapterhorn's open Terrarium tiles (512 px lossless WebP,
  swissALTI3D over Switzerland), since SNOW-1442. Since SNOW-1444 production
  reads our own copy, clipped to the supported area and served from
  `tiles.snowdesk-data.info/dem/<version>/`, through the
  `MAP_3D_DEM_TILE_URL` setting
  ([runbook](../runbooks/self-hosted-tiles.md#3-d-elevation-demversion)).
  Its default, used in development, is `tiles.mapterhorn.com`. When Mapterhorn fails, each tile falls back to
  AWS's Terrarium tiles (`s3.amazonaws.com/elevation-tiles-prod/terrarium`,
  256 px, global, z0–15, no token), enlarged nearest-neighbour so no height is
  invented. After three network failures or 5xx answers in a row, the rest of
  the visit goes straight to AWS (SNOW-1445,
  `map_3d_core.createDemLoader`). A 204 from our copy, for a tile outside the
  supported area, falls back the same way as a 404, and neither is counted.
  Mapterhorn asks users to copy its archives rather than hotlink them
  ([research](../research/mapterhorn/README.md)), which is why we serve our
  own copy.
- **Steep terrain** is our class tiles, painted on the device exactly as on
  `/map/` (`terrain_filter_core`'s slope classifier and palette, through a
  `terrainfilter://` protocol the page registers for itself).
- Tilt and rotate are MapLibre's own gestures. The page adds no control and
  no icon for them.

**Why.** A prototype on real data (the project's `3d-map/` board) showed
every existing fill, raster and line layer draping onto the relief with no
change. The alternatives each replace or duplicate the map:

| Option | Cost | Why not |
|--------|------|---------|
| deck.gl over MapLibre | L | A second render loop, and our layers rewritten as deck layers. |
| CesiumJS | XL | Replaces MapLibre: every layer, sheet hook and offline cache. About 3 MB of JS. |
| three.js + DEM tiles | XL | We would own tiling, level of detail, draping and picking. |

The 2026-11-03 profile already showed the map's time is GPU and browser
work, not app JS, so a different engine buys no speed either.

**Consequences.**

- Our terrain-class tiles carry height in R and G, so they could serve as
  the DEM inside Switzerland (`encoding: 'custom'`, redFactor 256,
  greenFactor 1, blueFactor 0, which 4.7.1 supports). They only exist at
  z12–14, so a lower-zoom DEM would still be needed. The sandbox uses
  Terrarium alone, which keeps it simple.
- Terrain costs GPU time, which matters most on a phone. That is the first
  thing to measure before 3-D reaches `/map/`.
- Offline: Terrarium tiles are not in an area download. Bringing 3-D to
  `/map/` offline means adding the DEM to downloads.

**Sharing (5 Nov 2026).** Two links open the sandbox past the flag, for
anyone, signed in or not, and both stop when the sharer's `Map3dShare` row
is deleted in the admin:

- `?share=<token>` (`Map3dShare`, one per reader) opens the page with no
  routes at all: a reader's routes are private.
- A route share, `/routes/s/<token>/3d/` (`routes:share_redirect_3d`): the
  ordinary route share link with `3d/` on the end. It opens
  `/map/3d/?route_share=<token>` on that one route, drawn through the
  session's pending shares in `routes.geojson`, so the visitor sees the
  shared route and none of the sharer's others. Only a live, non-example
  share by a reader holding a `Map3dShare` qualifies
  (`map_3d.route_share_for_3d`). Inside the flag, Share on the 3-D map sends
  this link for the route that is open; a friend who followed the 2-D link
  gets /map/'s "3D" roundel carrying it (`pending_route_share_for_3d`).
