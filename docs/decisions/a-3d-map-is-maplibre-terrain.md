---
name: a-3d-map-is-maplibre-terrain
description: 3D map on MapLibre setTerrain: ?view=3d, ?at=, /map/3d/ redirect, route 3D links, 3D wash, fallbacks, offline elevation, Alpine gate
status: current
last-reviewed: 2026-11-08
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
  thing to measure before 3-D reaches `/map/`. `/map/3d/?probe=1` measured
  it on the device that opened it (SNOW-1448; since SNOW-1456 it redirects
  to `/map/?probe=1&view=3d`, [the 3D test](#the-3d-test-snow-1456-6-nov-2026)): it turns the camera over
  Saas-Fee at z12.6 and over the Valais at z9, with Steep terrain and SAC
  tours on, and reports the median, 95th-percentile and over-budget share
  of the frame intervals against the proposed budget, a median under 33 ms.
- Offline: see [3D offline in a downloaded area](#3d-offline-in-a-downloaded-area-snow-1453-6-nov-2026)
  below. Since SNOW-1453 an area downloaded on swisstopo winter pins the
  elevation tiles with its terrain data, and `/map/`'s 3-D works offline
  inside it.

**Sharing (5 Nov 2026; the token went on 6 Nov 2026, SNOW-1450).** The
sandbox first had two links past the flag, both revoked by deleting the
sharer's `Map3dShare` row: `?share=<token>`, which opened the page with no
routes, and a route share's `3d/` link, which opened it on that one route.

SNOW-1450 retired the sandbox and removed `Map3dShare` with it (migration
`map.0003`). Hugo decided not to keep a way past the flag: the token opened
a page that no longer exists, and `/map/`'s 3D view is the flag's to give
until SNOW-1451 opens 3D to everyone. Meanwhile a signed-out visitor, or
anyone outside the flag, who follows a 3D link sees the flat `/map/`;
SNOW-1469 later let a route share's 3D link grant 3D (below).

- **`/map/3d/` is a redirect** (`map_3d.map_3d_redirect`): a 302 with
  `Cache-Control: no-store`, the same for anyone, writing nothing to the
  session. `?route_share=<token>` goes to `/routes/s/<token>/3d/`, carrying
  `at`; anything else goes to `/map/`, carrying `probe` and `at`, then
  `view=3d`. `share` and the old `layers` switches are dropped: `/map/` has
  no URL for its switches, and its own `?layers=` is the layer explainer.
- **A route's 3D link**, `/routes/s/<token>/3d/` (`routes:share_redirect_3d`),
  sends any live share to `/map/?route_share=<token>&at=…&view=3d`, with the
  same 404/410/429, budget and seating as the flat follow. The visitor still
  sees only the routes their own session followed, through `routes.geojson`,
  never the sharer's others. Inside the flag it opens in 3D; outside, flat,
  framed on the route. A link with no readable `at` (the route Shares in 3D
  copy none) gets one framed on the route's `bounds`, pitch 0, zoom 9–15,
  so 3D's coverage is judged on the route rather than on wherever the
  reader last left the map.
- **`/map/` reads `?at=lng,lat,zoom,pitch,bearing`**
  (`map_view_core.readCamera`, the old page's validation): the linked camera
  beats the stored one, a flat boot zeroes its pitch, the coverage check
  uses its centre, and `at` leaves the address bar at boot. A pitch above 0
  is kept when the map boots into 3D (`snowdeskMapState.linkedPitch`)
  rather than eased to the opening tilt; a pitch of 0 still tilts in. A
  route share still frames its route after boot.
- **Share.** `/map/` has no share for the view itself: the address bar is
  the link. In 3D the two route Shares (the routes panel's row and the
  route rail) copy the `3d/` link through `map_3d_core.routeShare3dUrl`;
  flat, the plain one.

## The 3-D view on /map/ (SNOW-1449, 6 Nov 2026)

For a reader in both `map_3d` and `map_modes`, 3-D is a view of `/map/`
itself rather than a separate page. It sits beside the mode, not inside it:
the mode says what the map shows, the view how it is drawn
([the modes](the-map-has-a-dated-mode-and-a-terrain-mode.md)). SNOW-1449 made
it Terrain's alone; SNOW-1452 brought it to Conditions (below).
A `map_3d` reader without the modes, and a friend sent a route by a 3-D
reader, kept the "3D" link to `/map/3d/` until SNOW-1450 retired it; both
now see the flat map.

- **The rules** are `static/js/map_view_core.js` (`self.pwaMapViewCore`):
  the `?view=3d` parameter beside `?mode=terrain` (read only in Terrain
  until SNOW-1452); `availability` (online, no route being drawn, the centre inside
  `terrain_filter_core.COVERAGE_BOUNDS`, which is passed in rather than
  copied, and zoom 9 or more; since SNOW-1460 the coverage may be a ring); the basemap (swisstopo winter in 3-D, the
  reader's own flat, the stored choice never written); and the boot camera
  (a flat boot zeroes a stored pitch). `map.js` reads it at parse time for
  the boot basemap and camera and for its reconnect `setStyle`, so a
  reconnect in 3-D cannot swap swisstopo winter away.
- **Nothing about the view is stored.** A bare `/map/` always opens flat;
  `?view=3d` outside Switzerland is stripped at boot (outside the Alps with
  `map_beyond_switzerland`, below). A
  date write keeps it (`map_mode_core.withDate`).
- **The runner** is `static/js/map_view_3d.js` (`window.pwaMapView`). It
  reuses `map_3d_core` and the shared `static/js/map_3d_terrain.js`
  (`self.pwaMap3dTerrain`: the elevation protocol, registered once, and an
  idempotent terrain install), which the sandbox used too until SNOW-1450. Entering
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
  in both views, always shown. It first showed only while the map was not
  north-up; Hugo asked for it at all times (6 Nov 2026), so the reader
  always knows which way the map faces.

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
(SNOW-1452). The probe's own verdict (`frameStats().withinBudget`) was
never read by the watchdog; since SNOW-1456 the probe lives on `/map/` and
is judged by the watchdog's rule ([below](#the-3d-test-snow-1456-6-nov-2026)).

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
   for the rest of the visit. Since SNOW-1463 a trip outside playback asks
   first ([below](#a-lower-bar-and-ask-first-snow-1463-7-nov-2026)).

## A lower bar, and ask first (SNOW-1463, 7 Nov 2026)

**What went wrong.** On 6 Nov 2026 Hugo's iPad dropped out of 3D with "3D
is too slow on this device" while it was still drawing well, and the 3D
test then passed on the same iPad. Its three test runs in production
(`Map3dDeviceTest` 1–3) went Smooth, Slow, Smooth within two minutes, at
medians of 26–35 ms. The bar was 33 ms, and a steady 30 fps is 33.3 ms a
frame, so the bar sat exactly on 30 fps: any iPad, any phone in Low Power
Mode (iOS caps frames at 30) and any large screen failed on noise. On 7 Nov
Hugo's Pixel 9 (Mali-G715) dropped out at z13–14 while its relief was still
sharpening. That phone's test median is 16.7 ms: close in, every pan
fetches and drapes new tiles, and those frames run long on a phone that
draws what it already has well.

**Decision** (Hugo, "lower bar and ask first", 7 Nov 2026):

1. **The bar.** `FRAME_BUDGET_MS` is 45 ms and `FRAME_WINDOWS_TO_TRIP` is
   5, about ten seconds of moving time. Frame intervals come in
   display-refresh steps (16.7, 33.3 and 50 ms at 60 Hz, 41.7 ms at 120 Hz),
   so 45 ms passes 30 and 24 fps cleanly and fails 20 fps cleanly. A bar of
   exactly 50 ms would put a steady 20 fps on the edge, the mistake 33 ms
   made at 30 fps.
2. **Tiles loading are not timed.** Each frame reads `map.areTilesLoaded()`;
   an interval either of whose frames saw tiles loading is pushed to
   `createFrameWatch().push(ms, loading)` as loading and skipped: it neither
   counts towards the window's two seconds nor joins its median, and the
   window is kept. MapLibre 4.7.1's `idle` never fires while the camera
   moves, so a `dataloading`→`idle` span would have covered every gesture.
3. **Ask, do not drop.** A trip outside timelapse playback keeps the map in
   3D, stops timing, and shows "3D is running slowly here" on the map toast
   with "Switch to 2D" and "Keep 3D", with no timer
   (`MapSheet.toast(text, {sticky: true})`). Switch to 2D is the old trip:
   flat for the visit, `left-slow`, then `why-slow` with "Test this device".
   Keep 3D, the toast's ×, or another line taking its place all leave the
   watchdog silenced for the visit, playback included; nothing is stored.
   Leaving 3D by any route takes the prompt away. During playback a trip
   still goes flat at once, because playback cannot wait for a tap. The two
   text buttons on one toast line are a recorded exception to design rule 5
   ([`design-system.md`](../design-system.md#interaction-principles)).
4. **The test agrees.** `judgePass(intervals, loading)` skips what the
   watchdog skips, and counts a pass as measured only with at least
   `FRAME_WINDOWS_TO_TRIP` windows, so a slow device could have tripped on
   it; each `PROBE_PASSES` pass lasts 18 s (was 12 s) so that a pass with
   some tile time still holds five. The Details show each pass's skipped
   time, and their figures are over the timed intervals. Results sent
   before and after the change are told apart by `client_version`.
5. **The trace.** `createFrameWatch({onWindow})` hears every closed window;
   the runner puts each on the debug trace as `map 3d.window`
   ([`debug-log.md`](../debug-log.md)), so a phone that drops out can show
   whether its slow windows lined up with tile loads.

Left out: a lower pixel-ratio cap for large canvases (`MAX_PIXEL_RATIO` is
already 2, what the iPad ran at), to be measured first. SNOW-1464 added it
(below).

## 1.5x on a large canvas (SNOW-1464, 7 Nov 2026)

Every 3D frame shades every device pixel through the terrain mesh, so the
cost of a frame grows with the canvas, not the scene. An iPad at 2x
(744 x 1011 CSS pixels) fills three million pixels a frame, more than twice
a 2x phone. `pixelRatioFor(devicePixelRatio, cssWidth, cssHeight)` in
`map_3d_core.js` caps the ratio at `MAX_PIXEL_RATIO` (2) as before, then at
`LARGE_CANVAS_PIXEL_RATIO` (1.5) when the canvas would draw more than
`LARGE_CANVAS_PIXELS` (2.5 million) device pixels at the capped ratio. At
1.5x the iPad fills 1.7 million, about 44% fewer. It never raises a ratio,
so a 1x desktop stays at 1x and phones keep 2x.

The runner applies it on entering 3D and again on each map `resize` while
in 3D, so a rotated iPad or a resized window crosses the line both ways.
Leaving 3D gives the reader's own ratio back, held until the restore if the
WebGL context is lost, as before.

Rejected: capping the elevation tiles' zoom. MapLibre's terrain fetches
elevation two zooms below the map (`deltaZoom` 1 on 1024-pixel tiles), so a
cap at z15 bites only at map z17, and a lower cap would soften the relief
where it is looked at. Rejected also: turning off tappable layers in 3D.
They cost nothing per frame on touch devices; only a desktop's mouse moves
query them.

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

## The 3D test (SNOW-1456, 6 Nov 2026)

**Decision.** Any reader with `/map/`'s 3D view can test it on their own
device: "Test 3D on this device" in the Help menu, or "Test this device" on
the line that says the watchdog laid the map flat. The test is judged by
**the same rule as the fallback**, so a device that passes the test is one
the watchdog would not lay flat on the same ground, and a reader is never
told "smooth" by one rule and sent back to 2D by another.

- **The rule.** Each pass's frame intervals go through a fresh
  `map_3d_core.createFrameWatch()` (`judgePass`): a pass that trips falls
  behind, one that does not keeps up. `testVerdict(passes)` gives `smooth`
  when both keep up, `stutter` when only the second (z9, over the Valais)
  falls behind, and `slow` otherwise. A pass of fewer than
  `FRAME_WINDOWS_TO_TRIP` watchdog windows (one window until SNOW-1463) was
  not measured, and the run is `unmeasured`:
  the panel says so and offers Test again, and nothing can be sent.
  `frameStats` still gives the Details figures (median, fps, p95, share over
  budget, slow windows); the verdict never reads the p95.
- **The run** is `static/js/map_3d_test.js`, ported from the sandbox's probe
  (`nextIdle`, `timeTurn`, `gpuName`, `PROBE_PASSES`), on the live map as the
  reader has it: their basemap and overlays, which the result records as
  layer keys (`MAP_3D_TEST_OVERLAYS`). It drives the view through
  `pwaMapView.canTest` / `beginTest` / `endTest`. `beginTest` saves the view
  and camera, enters 3D even after a trip, and holds the runner's own
  watchdog off for the run; `endTest({verdict})` puts both back, and a
  `smooth` or `stutter` verdict lifts the visit's `slow` latch. Stop, Escape,
  a hidden tab or a lost WebGL context ends the run with no verdict. It is
  not offered offline (it loads tiles), while a route is drawn, or where the
  device gate refuses.
- **The panel** (`public/partials/_map_3d_test_panel.html`) is a form panel
  on `_overlay_sheet.html`, on the left on a desktop. Share and Test again
  sit in its "…" menu (design rule 5); Done closes it.
- **Sending is opt-in and anonymous.** "Send anonymously" posts the result
  to `POST /api/map-3d-test/` (`apps.map.api.map_3d_test_receive`, CSRF
  protected, 16 KiB at most, 10 an hour per IP with `block=False`, so a
  dropped post still answers 204), which writes one `Map3dDeviceTest` row
  holding no user, session or IP. The admin lists the rows by verdict and
  probe, so the budget can be read across real devices before it is retuned.
- **Staff re-measure.** `/map/?probe=1` starts the test on load with Details
  open, turning on Steep terrain and SAC tours for that run only (the
  layers SNOW-1448's budget was measured on); Share sends the full JSON. The
  probe left `map_3d.js`, and `/map/3d/?probe=1` redirects there; since
  SNOW-1450 it does so for anyone, as the whole of `/map/3d/` does.

## 3D needs only map_3d (SNOW-1451, 7 Nov 2026)

Until SNOW-1451, 3D was for readers in both `map_3d` and `map_modes`, since
it began as part of Terrain. SNOW-1452 put it in both modes, so nothing in
the 3D view still needs the modes. Hugo decided to unpair the flags so that
3D can open to every signed-in reader while the Conditions | Terrain toggle
stays with superusers.

- **One rule.** `map_shared.map_3d_view_enabled` is the `map_3d` flag alone,
  still short-circuited for an anonymous request. `/map/`, the home page's
  3D card and `/offline/`'s elevation template already read it. The nav's
  wordmark nested `{% flag "map_3d" %}{% flag "map_modes" %}` and now reads
  it through `{% map_3d_view_enabled %}` (`apps/public/templatetags/map_flags.py`).
  A tag, not a context value, because every view that renders the nav would
  otherwise pass it.
- **Two places assumed the modes.** `map.css` showed the switch only under
  `:root[data-map-mode]`, which only `map_mode.js` sets. It now keys on
  `:root[data-map-view]`, which `map_view_3d.js` sets at boot. `map.js` boot
  required `MAP_MODES_ENABLED` before reading `?view=3d`; it no longer does.
  Without the modes a reader's map is the one dated view, and 3D drapes it
  as in Conditions.
- **Signed-in readers only.** Hugo chose on 7 Nov 2026 that signed-out
  visitors stay flat when `map_3d` goes on for everyone. The anonymous guard
  keeps those pages free of waffle queries. The one way past it is a route
  share's 3D link (SNOW-1469, below).

## A boot tilts with the elevation frozen (SNOW-1467, 7 Nov 2026)

Every `?view=3d` page load on production drew a blank map, though tapping
3D on a loaded map worked, so MS10 passed. A boot tilts in with `easeTo`
before the swisstopo winter style's `styledata` installs the terrain.
MapLibre 4.7 follows the ground's height on each ease frame only when
terrain was on as the ease began (`_prepareElevation` sets its centre).
Terrain arriving mid-ease left the frame reading an unset centre, which
threw inside the render task queue and left it "already running" for
good: no tile was ever requested. `tiltIn` now passes
`freezeElevation: true`. A tilt keeps its centre, so there is no height to
follow. The e2e suite missed it because its fixture style loads before the
first ease frame; the Vitest suite pins the option.

## A route share's 3D link grants 3D (SNOW-1469, 7 Nov 2026)

Once SNOW-1450 removed `Map3dShare`, a signed-out visitor had no way to see
3D: a route's `/routes/s/<token>/3d/` opened the route flat. Hugo chose
(7 Nov 2026) that a shared route is the way past the signed-in rule, and
only a route: no "share this view" link, no per-user token.

- **The grant is a session key**, `map_3d_grant`
  (`apps/routes/services/map_3d_grant.py`): the token of the route share
  whose 3D link this browser followed last. `route_share_redirect_3d` seats
  it on a real top-level navigation only, beside the pending token, as the
  flat follow seats that. The flat follow seats no grant. It lasts the
  session and ends at sign-out.
- **It is checked on every read.** `map_3d_view_enabled` is true for a
  signed-in reader inside `map_3d`, or for anyone whose grant still holds:
  the share is claimable (live, and not the example route, which every
  visitor is offered and would otherwise open 3D to everyone), and its
  sharer is inside `map_3d`, asked with waffle's cached `Flag.get` and
  `is_active_for_user`, which covers superusers, users and groups. Revoking
  the share, letting it expire or taking the sharer out of the flag ends
  the grant on the visitor's next page. No grant costs no query; the answer
  is kept on the request, which `/map/` asks twice.
- **Its own key, not the pending list.** Claiming or dismissing the route
  drops its token from `route_shares`; the 3D the visitor was given should
  not go with it.
- **It shows nothing the share did not.** The visitor sees only the routes
  their own session followed (`routes.geojson`). A granted visitor gets the
  3D test too, which already accepts anonymous posts.
- **The worker passes the follow through.** `SESSION_REDIRECT_PATTERNS` in
  `static/js/sw.js` lacked `/routes/s/<token>/3d/` and `/map/3d/`, so with
  the app installed the 3D follow reached the server as
  `Sec-Fetch-Dest: empty` and seated nothing: SNOW-1235's bug for the 3D
  link. Both now go to the browser.
- A signed-out shell the worker caches during a granted visit can show the
  3D switch offline later. That is harmless: offline 3D needs a downloaded
  area, and downloading needs an account.

## "3D is experimental" (SNOW-1470, 7 Nov 2026)

Before 3D went to every reader, Hugo asked that the view say it is still
experimental. A floating banner (`#map-3d-notice`, the off-map banner's
primitive) shows whenever the map is in 3D and goes when it is
flat, whether the reader switched or the map dropped out by itself.
`static/js/map_3d_notice.js` reads `:root[data-map-view]` at load and then
`snowdesk:map-view-changed`. A banner that stays up through a 3D visit must
not cover a control, so `static/css/map.css` places it inside `#map` rather
than in the primitive's top-centre slot, which sat on the search pill from
640px (Hugo, 8 Nov 2026). On a phone it drops under the date row and stops
short of the right-hand roundels; from 640px it takes the top-right corner,
level with the search pill and never wider than the room the pill leaves. It
sits under the map's controls and panels, so an open panel covers it, not
the other way round. The two answers to a locate tap, "Off the map" and "Can't find your
location", moved into `#map` with it and share its slot, covering it for
their few seconds. Closing it hides it for the visit and stores
nothing, so the next load shows it again: the point is that nobody meets the
rough edges unwarned, and the cost of a repeat is one tap.

## The fly-past (SNOW-1466, 7 Nov 2026)

The promo video's fly-past is drawn by this view, one frame at a time
(`map_3d_flight_core.js`, `map_3d_flight.js`, `bin/record-3d-flyby`); why:
[`a-fly-past-is-rendered-frame-by-frame.md`](a-fly-past-is-rendered-frame-by-frame.md).

## Past Switzerland (SNOW-1460)

With `map_beyond_switzerland` on as well as `map_3d`, 3D opens anywhere in the
Alps, not only over Switzerland. The winter map carries on past the border as
OpenStreetMap in the winter style, over our own relief
([why](the-winter-map-beyond-switzerland-is-restyled-osm.md)). The coverage
is then the **Alpine gate**, `beyond_switzerland_core.ALPINE_GATE`: a ring
about 5.0–16.6°E by 43.7–48.3°N, which leaves out the Pyrenees and Corsica.
`map_view_core.covers` reads a ring (even-odd) as well as a box.

- **The ring is handed over.** `map_view_3d.js` starts on the Swiss box.
  `map_beyond_switzerland.js`, which loads after it, calls
  `pwaMapView.setCoverage(ALPINE_GATE)`. A flagged `?view=3d` boot waits for
  that call under the same two-second cap as the offline wait, then enters
  or lays the map flat. `map.js` skips its own boot coverage downgrade when
  `#map` carries `data-map-beyond-switzerland`, so an Arlberg link is not
  stripped before the ring arrives.
- **The copy follows.** Behind the flag, the strings template says "3D covers
  the Alps for now" and "Back in 2D: 3D covers the Alps for now.". Off the
  flag nothing changes: the Swiss box and "3D covers Switzerland for now".
- **What stays Swiss.** The terrain class tiles, and so Terrain mode's slope
  layer (SNOW-1459). Offline 3D is still only inside a downloaded area, and
  Alpine area downloads are not part of SNOW-1460.
