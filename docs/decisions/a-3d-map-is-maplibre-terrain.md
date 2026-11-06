---
name: a-3d-map-is-maplibre-terrain
description: 3D map on MapLibre setTerrain: /map/3d/ sandbox, /map/ ?view=3d, 3D wash, fallbacks, offline 3D via elevation tiles in downloads
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
- Offline: see [3D offline in a downloaded area](#3d-offline-in-a-downloaded-area-snow-1453-6-nov-2026)
  below. Since SNOW-1453 an area downloaded on swisstopo winter pins the
  elevation tiles with its terrain data, and `/map/`'s 3-D works offline
  inside it.

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
the mode says what the map shows, the view how it is drawn
([the modes](the-map-has-a-dated-mode-and-a-terrain-mode.md)). SNOW-1449 made
it Terrain's alone; SNOW-1452 brought it to Conditions (below).
A `map_3d` reader without the modes, and a friend sent a route by a 3-D
reader, keep the "3D" link to `/map/3d/`.

- **The rules** are `static/js/map_view_core.js` (`self.pwaMapViewCore`):
  the `?view=3d` parameter beside `?mode=terrain` (read only in Terrain
  until SNOW-1452); `availability` (online, no route being drawn, the centre inside
  `terrain_filter_core.COVERAGE_BOUNDS`, which is passed in rather than
  copied, and zoom 9 or more); the basemap (swisstopo winter in 3-D, the
  reader's own flat, the stored choice never written); and the boot camera
  (a flat boot zeroes a stored pitch). `map.js` reads it at parse time for
  the boot basemap and camera and for its reconnect `setStyle`, so a
  reconnect in 3-D cannot swap swisstopo winter away.
- **Nothing about the view is stored.** A bare `/map/` always opens flat;
  `?view=3d` outside Switzerland is stripped at boot. A
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
- **Leaving.** The map goes flat, with a line, when it goes offline (outside
  a downloaded area, since SNOW-1453) or is panned out of Switzerland. Until SNOW-1452 it also went flat on a change
  to Conditions. Drawing a route takes the relief and the tilt away on
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

**The budget.** A window of about two seconds of moving time
(`map_3d_core.FRAME_WINDOW_MS`, 2000) passes when its median frame interval
is under 33 ms (`FRAME_BUDGET_MS`), as judged by `frameWindowFails` on
`frameStats`' output, and three failing windows in a row
(`FRAME_WINDOWS_TO_TRIP`) trip the watchdog. The first cut, as agreed on
SNOW-1448, also failed a window whose 95th percentile was over 66 ms and
tripped after two. On 6 Nov 2026 it laid flat the Mali-G715 phone whose
probe run had passed (Hugo: "it runs fine. We've over tightened the 3D
limits"). That run's opening view had a median of 16.7 ms but a p95 of
50 ms and a worst frame of 83 ms: tiles arriving and draping mid-gesture
stall single frames on a phone that draws 3D well, so a p95 limit trips on
a good phone in ordinary use. Only a map that is slow throughout lifts the
median, so the p95 limit was dropped and the count raised to three
(SNOW-1452). The `/map/3d/?probe=1` probe and its own verdict
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
   intervals go to `map_3d_core.createFrameWatch`: three failing windows in
   a row trip it, a passing window resets the count, and windows count across
   gestures. Timing starts after the first `moveend` that follows each tilt
   in, so the entering ease over a fresh style is not counted (`easeTo`
   stops an ease still running and fires its `moveend` synchronously, so
   the runner disarms again after the call). The first interval of each run
   is dropped and a hidden tab is never counted. A trip blocks 3-D (`slow`)
   for the rest of the visit.

## 3D in Conditions (SNOW-1452, 6 Nov 2026)

3D is a view of both modes. `readView` and `availability` no longer read
the mode (`REASONS.MODE` is gone), a mode change keeps the view and only
re-reads the switch, and the 3D | 2D switch shows under
`:root[data-map-mode]` in either mode. `map_mode_core.withDate` carries
`?view=3d` in Conditions too, so stepping a day keeps a copied link in 3D.

- **What drapes.** Every fill, line and raster Conditions draws drapes on
  the relief with no change, problem terrain (the `bulletinterrain://`
  raster) included; symbols and circles, such as weather symbols, float.
- **The danger fill** is a light wash in 3D, and the danger-colour edge band
  the stronger outline, from z9
  ([the region colour](the-region-colour-gives-way-by-zoom.md#in-the-3d-view-snow-1452-6-nov-2026)).
  `map.js` is the one writer: every 3D entry swaps the style and `map.js`
  reinstalls the region layers on `styledata`, so a paint set by the runner
  would be wiped or race the reinstall. `map.js` reads the live view and
  repaints on `snowdesk:map-view-changed`; nothing is saved across it.
- **The timelapse plays in 3D.** It plays only on a desktop, so its cost is
  not a reason to go flat (Hugo, 6 Nov 2026). The runner hears
  `snowdesk:timelapse-state` and runs the frame loop for as long as it
  plays, whether the camera moves or not, and picks it up again when a
  hidden tab comes back. A trip during playback goes flat through `exit()`,
  whose `snowdesk:map-view-changed` carries `reason: 'slow'`, and
  `map_timelapse.js` stops on that, which is what a desktop that cannot
  keep up needs. The basemap swap (SNOW-58) is not relied on: a reader
  whose basemap is swisstopo winter gets no swap.
- **One budget for both modes.** A trip in either keeps 3D off for the visit
  in both.
- **Not done.** From z12 the danger fill drops for every painted region,
  keyed on the camera zoom, so at a steep pitch distant ground beyond the
  problem-terrain tiles may show neither. It is a manual check in MS10, and
  a ticket only if it reads as missing data.

## 3D offline in a downloaded area (SNOW-1453, 6 Nov 2026)

**Decision.** The elevation tiles join an area's terrain data. Every
download with the `terrain` flag (`DownloadArea.terrain` and the local
record) on swisstopo winter pins them too, wherever the page has the 3-D
view (`#map-view-toggle[data-dem-tile-url]`; nothing is pinned with
`map_3d_view` off). There is no switch for them (Hugo, 6 Nov 2026). Offline,
3-D is available with the centre inside a downloaded area whose elevation
tiles are pinned, and nowhere else: **offline 3-D works in areas downloaded
on swisstopo winter.**

- **Which basemap.** Entering 3-D swaps to swisstopo winter
  (`map_view_core.BASEMAP_KEY`), and a download pins only its own basemap's
  tiles and documents, so an area downloaded on OpenFreeMap could not draw
  3-D offline whatever elevation it held (Codex, PR #1498). Elevation is
  therefore pinned, stamped, priced and expected only for an area whose
  `basemapKey` is `basemap_download_core.ELEVATION_BASEMAP_KEY`
  (`elevationBasemap`), which a test holds equal to `BASEMAP_KEY`.
  `areaElevationTileURLs` and `elevationAreaBoxes` both apply it, so an
  OpenFreeMap area never reads incomplete for missing elevation, and a stamp
  on one never offers offline 3-D. The alternative, pinning swisstopo
  winter's tiles beside every download, would double most downloads for a
  view few readers use offline.

- **Which tiles.** `basemap_download_core.elevationTileURLs` walks the
  blob's z10–13 rows (`ELEVATION_BAND`), clipped to the coverage
  rectangle, and adds each tile's parents up to z7
  (`ELEVATION_PARENT_MIN_ZOOM`). The terrain mesh reads the elevation about
  two zooms below the map (`prefetchDemTiles`) and the hillshade one below,
  so z13 serves the mesh to map z15 and the shading to z14, the basemap
  download's own ceiling; MapLibre overzooms z13 above that, about 6.6 m a
  pixel at 46°N. The z7–9 parents cost 3–4 tiles and draw coarse relief
  past the area's edge rather than flat ground. The URL is the loader's
  (`map_3d_core.tileUrl`), so a pinned entry is what MapLibre asks for.
- **What it weighs.** Five Alpine points sampled at each of z10–14 on
  `tiles.snowdesk-data.info/dem/2026-11/` on 6 Nov 2026 came out at
  125–195 KB, a mean of about 155 KB, so `ELEVATION_BYTES_PER_TILE` is
  192 KiB. A median Swiss region (181 basemap tiles, SNOW-868's per-zoom
  split) needs 1 + 2 + 2 parents at z7–9 and about 2 + 5 + 12 + 37 tiles at
  z10–13, **about 60 tiles: ~11.5 MB charged, ~9 MB real**, beside the
  basemap's ~34 MB charged (~22 MB real) and the class tiles' ~11 MB. A
  typical download grows by about a quarter. z14 would add about 145 tiles,
  ~22 MB more, for detail past what the mesh draws at touring zooms.
- **The estimate.** The elevation figure is folded into the one terrain
  figure (`terrainClassMbForBlob`, `terrainClassMbForGround`) where the
  download would pin them (`elevationPinnedOn`: the 3-D view, on
  swisstopo winter), and only there does the label read "terrain and 3D
  data" (the `-3d` strings); elsewhere it reads "terrain data".
  `terrainScaledBytesPerTile` charges a third of an elevation tile against
  every frame tile: the band is about a quarter of a z10–14 frame's tiles
  (0.27 at the ceiling, parents included), so a frame drawn to the ceiling
  stays under it.
- **The stamp.** A run that pinned them, or a sync that left every one in
  the area's OWN bucket, stamps the record `elevation: <template>`. Sync and
  the region roundel judge the elevation tiles against that bucket
  (`missingFromAreaBucket`), never the union of every bucket: an area
  overlapping another would otherwise be stamped on the other's tiles, and
  removing the other would leave the stamp over tiles that have gone
  (Codex, PR #1498). What the bucket lacks is fetched into it. Local only, for
  the reason `deps` is: it says what this device's bucket holds.
  `elevationAreaBoxes(records, template)` is the `areaBBox` of every record
  stamped with today's template, and `map_view_core.availability` takes
  them as `offlineAreas`. The rule checks boxes and never probes the cache.
- **The verdict.** `areaElevationTileURLs` joins the incomplete check on the
  Manage downloads sheet, the region roundel and `/offline/` (whose panel
  carries `data-dem-tile-url` on the same gate). An area downloaded before
  this, or before a seasonal `<version>` bump of the template, reads
  `incomplete` until it is synced (~9 MB each), following
  [the rule that an area is verified by what it renders](a-downloaded-area-is-verified-by-what-it-renders.md);
  Sync fetches the tiles and writes the stamp.
- **The runner.** `map_view_3d.js` reads the boxes from
  `pwaBasemapAreas.downloadedAreas({deviceOnly: true})` at boot, on every
  connectivity change and on `snowdesk:downloads-changed`; `deviceOnly`
  skips the account's areas, which cost a request online and are never
  stamped. Going offline inside a box keeps 3-D; a `moveend` offline outside
  every box leaves it with a line, and panning back in makes it available
  without entering it. The read is IndexedDB, so asynchronous: an online
  boot is unchanged, and an offline `?view=3d` boot waits for the first read,
  entering inside a box and lying flat on the reader's basemap outside,
  with the switch disabled for `offline` until then.
- **The loader.** `createDemLoader` does not count a 504 with
  `X-SW-Cache: miss`, the service worker's offline miss
  (`_synthesizedGatewayTimeout`), as a failure. Three misses past the
  area's edge would otherwise latch it to AWS, and the pinned tiles would
  never be asked for again.
