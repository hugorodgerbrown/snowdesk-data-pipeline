---
name: a-3d-map-is-maplibre-terrain
description: 3-D map on MapLibre setTerrain: /map/3d/ sandbox, /map/ ?view=3d (map_view_3d), 3-D fallbacks (frame watchdog, WebGL loss, device gate)
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
  thing to measure before 3-D reaches `/map/`. `/map/3d/?probe=1` measures
  it on the device that opens it (SNOW-1448): it turns the camera over
  Saas-Fee at z12.6 and over the Valais at z9, with Steep terrain and SAC
  tours on, and reports the median, 95th-percentile and over-budget share
  of the frame intervals against the proposed budget, a median under 33 ms.
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

## The 3-D view on /map/ (SNOW-1449, 6 Nov 2026)

For a reader in both `map_3d` and `map_modes`, 3-D is a view of `/map/`
itself rather than a separate page. It sits beside the mode, not inside it:
the mode says what the map shows, the view how it is drawn, and 3-D is
Terrain's alone ([the modes](the-map-has-a-dated-mode-and-a-terrain-mode.md)).
A `map_3d` reader without the modes, and a friend sent a route by a 3-D
reader, keep the "3D" link to `/map/3d/`.

- **The rules** are `static/js/map_view_core.js` (`self.pwaMapViewCore`):
  the `?view=3d` parameter beside `?mode=terrain`, read only in Terrain;
  `availability` (Terrain, online, no route being drawn, the centre inside
  `terrain_filter_core.COVERAGE_BOUNDS`, which is passed in rather than
  copied, and zoom 9 or more); the basemap (swisstopo winter in 3-D, the
  reader's own flat, the stored choice never written); and the boot camera
  (a flat boot zeroes a stored pitch). `map.js` reads it at parse time for
  the boot basemap and camera and for its reconnect `setStyle`, so a
  reconnect in 3-D cannot swap swisstopo winter away.
- **Nothing about the view is stored.** A bare `/map/` always opens flat;
  `?view=3d` outside Terrain or outside Switzerland is stripped at boot. A
  date write keeps it (`map_mode_core.withDate`).
- **The runner** is `static/js/map_view_3d.js` (`window.pwaMapView`). It
  reuses `map_3d_core` and the shared `static/js/map_3d_terrain.js`
  (`self.pwaMap3dTerrain`: the elevation protocol, registered once, and an
  idempotent terrain install), which the sandbox now uses too. Entering
  swaps to swisstopo winter, adds the relief, sky and sunlight, caps the
  pixel ratio, opens the pitch ceiling, floors the zoom at 9 and eases to
  the opening tilt. Leaving reverses it and keeps the elevation source. The
  relief is put back on `styledata` after every `setStyle`.
- **The flat map does not tilt.** `maxPitch` is 0 except in 3-D, and while
  flat in Terrain with 3-D available, where a reader's own tilt (it carries
  an `originalEvent`) enters 3-D. Measured in Chromium on the vendored
  MapLibre 4.7.1: a ctrl-drag fires `pitchstart` and `pitch` with an
  `originalEvent` even at `maxPitch` 0, but the map does not move; with the
  ceiling open the same drag tilts it, so the gesture that enters 3-D also
  carries the reader's tilt into it.
- **Leaving.** The map goes flat, with a line, when it changes to
  Conditions (before a sheet paints, since `map_mode.js` dispatches
  `snowdesk:map-mode-changed` synchronously), goes offline or is panned
  out of Switzerland. Drawing a route takes the relief and the tilt away on
  swisstopo winter and gives them back when it ends.
- **Two MapLibre 4.7.1 behaviours** shaped it. A `setStyle` stops a camera
  ease where it stands, so the tilt runs again on the new style's
  `style.load` and leaving jumps to pitch 0. And a style diff fails on a
  style carrying terrain, so the runner's swaps pass `{diff: false}`.
- **The switch** (`#map-view-toggle`) is a 3D / 2D segmented switch, the
  current view raised, like the Conditions / Terrain switch. A single
  button that named the other view ("2D" while in 3-D) read as the wrong
  state and looked inactive (Hugo, 6 Nov 2026). Its name stays "3-D view";
  `aria-pressed` says whether it is on.
- **The compass** (`public/partials/_map_compass.html`,
  `static/js/map_compass.js`) sits under the zoom roundel for every reader,
  in both views, shown whenever the map is not north-up.

## When the device cannot keep up (SNOW-1455, 6 Nov 2026)

**The agreed budget.** A window of about two seconds of moving time
(`map_3d_core.FRAME_WINDOW_MS`, 2000) passes when its median frame interval
is under 33 ms (`FRAME_BUDGET_MS`) and its 95th percentile is 66 ms or less
(`FRAME_P95_BUDGET_MS`), as judged by `frameWindowFails` on `frameStats`'
output. The `/map/3d/?probe=1` probe and its own verdict
(`frameStats().withinBudget`, `PROBE_PASSES`) are unchanged; the watchdog
does not read that verdict.

Three things lay `/map/`'s 3-D flat, each through the runner's
`exit({reason})`, with the switch then disabled through `availability`. A
fallback lasts for this visit only, and nothing about the device is stored
(Hugo, 6 Nov 2026). `map_view_core.REASONS` gained `device`, `slow` and
`webgl`, asked straight after `mode`, so a device that can never draw 3-D
is not told "Zoom in".

1. **The gate at boot** (`map_view_core.deviceGate`): no WebGL on the map's
   canvas, or `navigator.deviceMemory` of 2 GB or less on Android Chrome
   (`LOW_MEMORY_GB`). On `/map/` no WebGL already means no map at all
   (SNOW-893's `#map-unavailable`), so the memory test is the one that does
   the work. A `?view=3d` boot on such a device opens flat.
2. **WebGL lost.** MapLibre 4.7.1 re-fires the canvas's `webglcontextlost`
   and `webglcontextrestored` as map events. A loss goes flat with one line
   and blocks 3-D (`webgl`) until the restore, which says nothing and turns
   the switch back on.
3. **Frames over budget.** A `requestAnimationFrame` loop runs only between
   `movestart` and `moveend`, in 3-D and not while a route is drawn. Its
   intervals go to `map_3d_core.createFrameWatch`: two failing windows in a
   row trip it, a passing window resets the count, and windows count across
   gestures. Timing starts after the first `moveend` that follows each tilt
   in, so the entering ease over a fresh style is not counted (`easeTo`
   stops an ease still running and fires its `moveend` synchronously, so
   the runner disarms again after the call). The first interval of each run
   is dropped and a hidden tab is never counted. A trip blocks 3-D (`slow`)
   for the rest of the visit.
