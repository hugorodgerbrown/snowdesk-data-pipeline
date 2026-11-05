---
name: a-3d-map-is-maplibre-terrain
description: 3-D terrain map — /map/3d/ sandbox on MapLibre setTerrain (raster-dem, Terrarium DEM), not deck.gl, CesiumJS or three.js; map_3d flag
status: current
last-reviewed: 2026-11-05
---

# A 3-D map is MapLibre's own terrain

**Decision.** SNOW-1438 (5 Nov 2026). The 3-D view of the map is MapLibre's
built-in terrain: `map.setTerrain` on a `raster-dem` source, in the MapLibre
4.7.1 we already vendor. It ships first as a sandbox page, `/map/3d/`
(`apps/map/map_3d.py`, `static/js/map_3d.js`, `static/js/map_3d_core.js`),
behind the `map_3d` flag, separate from `/map/`. It shows Steep terrain, SAC's
ski tours, the reader's own routes and the resorts, because Hugo found 3-D
most useful "in terrain mode, when looking at routes".

- **Elevation** comes from AWS's open Terrarium tiles
  (`s3.amazonaws.com/elevation-tiles-prod/terrarium`, global, z0–15, no
  token), so the relief reaches every zoom.
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
