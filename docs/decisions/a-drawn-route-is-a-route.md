---
name: a-drawn-route-is-a-route
description: A route drawn on the map is a Route (source DRAWN), previewed by routes:draw_preview with the routes layer's Feature
status: current
last-reviewed: 2026-10-28
---

# A drawn route is a route

## Decision

**A route drawn on the map is stored as an ordinary `Route` row, with
`source = Route.Source.DRAWN`.** Its `points` are the waypoints the owner
placed, `[lon, lat, None]`, joined by straight lines. Every recorded field
(`ele`, `ascent_m`, `descent_m`, `started_at`, `finished_at`,
`source_point_count`) is null, because nothing was recorded. The heights,
the climb, the legs and the slope classes come from the terrain model
through `slope_samples`. That is the same path an upload with no `<ele>`
already takes.

**The live profile is a server preview of the saved route, not a
client-side approximation.** `POST /routes/drawn/preview/`
(`routes:draw_preview`) builds an unsaved `Route` with
`drawn.draft_route`, samples the ground under it with
`build_slope_samples`, and answers with `_route_feature`, the same Feature
`routes:geojson` serves. The draw panel then draws it with
`route_drawing_core.js` and profiles it with `route_profile.js`, the same
code that draws the route once it is saved. It needs no account: drawing
is free and saving is not. It is rate-limited by IP, and a draft is capped
at `MAX_DRAWN_DISTANCE_M` (100 km).

**A drawn route carries an estimate, never a duration.**
`estimate.estimate_duration_s` applies DIN 33466 (4 km/h on the flat,
300 m/h climbing, the larger plus half the smaller) with the descent skied
at 1,500 m/h, and rounds to ten minutes. It travels as `estimate_s` and is
quoted with a "≈". `duration_s` stays null, because that slot means "this
is how long it took".

**The flag (`route_drawing`) is on the minting surfaces only:** the
preview, the save, the "Draw on the map" CTA and the GPX download. A saved
drawn route reads on every surface whatever the flag says, which is the
lesson of `route_sharing` (docs/feature-flags.md). On the map page the flag is read only for
a signed-in request, so a signed-out visitor pays no waffle query for it.
Opening drawing to signed-out visitors means removing that guard in
`_routes_context` on purpose. The endpoints already read the flag for
anyone.

## Why

- **Everything downstream already works on a `Route`.** That covers the
  legs, the slope classes, the point card, the day's bulletin, sharing,
  trips, GPX export and the per-user cap. A separate model would have
  meant a second copy of each of them, or an adapter in front of each.
- **The server is the only place that has the terrain.** The class tiles
  the browser paints from carry a slope band, not a height. Interpolating
  a profile on the device would have drawn a draft that disagreed with
  the same route after Save. A sampled 5 km draft costs about 7 s cold
  and 0.02 s warm (the tile cache), and a growing draft re-reads mostly
  warm tiles.
- **Straight lines, and no editing in v1** (decided 27 Oct 2026). Undo and
  Start over are the only edits. Snapping to swisstopo's ski-touring
  routes is deliberately not done. The routes are shown as a guide under
  the line (`ch.swisstopo-karto.skitouren`, open government data,
  attribution required), and the drawer decides where to go.
- **The guide was the map's own layer** (28 Oct 2026), swisstopo's raster
  under the layers menu's "Ski-touring routes" row, held on by draw mode
  (`window.pwaSkiRoutes.hold`). SNOW-1285 took the raster off the map: the
  map's ski-touring layer is SAC's tours and summits, every one of them
  tappable, and draw mode draws swisstopo's network itself as the vector
  shadow below, whether or not a leg follows it.

## Consequences

- No terrain model outside Switzerland means no profile, climb or estimate
  there. The line is drawn, and the panel says why the figures are
  missing.
- The preview needs a connection. Drawing offline on device-side class
  tiles is the follow-up, and it would have to match this server's
  figures.
- A drawn route's GPX export writes the terrain track (the 25 m sampler
  boundaries with the model's heights), not the sparse waypoints. A watch
  would otherwise draw a flat line between two dots kilometres apart.
- The draft waits in `localStorage` (`snowdesk.route-draft`) across a
  close and across a sign-in. Only "Start over" or a successful save
  discards it.
- A leg can follow swisstopo's ski-touring network (SNOW-1277). From z12
  draw mode loads `/api/ski-network.geojson` for the view and draws it as
  a faint shadow, followed or not (SNOW-1285). While following, a tap
  within 24 px of a line snaps to it. When the last point is on the
  network too, the leg is the shortest path along it
  (`static/js/ski_snap_core.js`, Dijkstra on the device, with no server
  routing), or straight when that path is over three times the straight
  distance. The path's vertices are ordinary waypoints, so
  `routes:draw_preview`, `routes:create_drawn` and the GPX keep their
  contracts. The draft also stores `taps`, the index of each tap's last
  point, so Undo takes back a whole leg and the map marks taps rather
  than every vertex. "Follow ski routes" turns this off on the device
  (`snowdesk.route-draw-follow`). `direction` and `access` are ignored,
  and snowshoe-only segments are neither drawn nor snapped to.
