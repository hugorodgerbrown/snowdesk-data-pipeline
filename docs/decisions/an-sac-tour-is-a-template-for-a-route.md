---
name: an-sac-tour-is-a-template-for-a-route
description: An SAC ski tour (SkiTour) is found by its summit, opened unsaved in the route panel, saved as a copy (Route.source SKI_TOUR)
status: current
last-reviewed: 2026-10-29
---

# An SAC tour is a template for a route

## Decision

**A `SkiTour` is reference data that nobody owns. It becomes a `Route` only
when a reader saves it, and then as a copy.** "Save to my routes"
(`routes:ski_tour_save`) calls `save_ski_tour`, which goes through
`write_route_copy`, the same path a claimed share and a saved trip take, so
the per-account cap applies the same way. The copy is an ordinary `Route`
with `source = Route.Source.SKI_TOUR` and `ski_tour_uuid` set to the tour's
`source_uuid`. Its points are the tour's line, `[lon, lat, None]`, its
heights come from the terrain model, and its name is the summit and SAC's
name for the way: "Signalhorn (GR) · Von der Chamonna Tuoi CAS". After
saving, the reader can rename it, share it or plan a trip with it like any
other route, and later imports never change it.

**The summit is the handle.** Drawn together, the tours form one web, and
a tap near a hut can land on six of them. Each target (the summit or hut a
tour climbs to or comes down from) gets a labelled mark, and readers pick
a tour by where it goes. swisstopo names the target but gives no
coordinate, so `build_summits` places it at the median of its tours'
target ends. A tour's target end is the last point going up or the first
coming down. After that first guess, each tour is re-read by whichever end
is nearer, so a tour drawn the wrong way round is still placed correctly.

**A tap never picks a tour for the reader when more than one is in
reach.**

- A summit tap opens the list of every way up and every way down
  (`routes:ski_tour_list?summit=`).
- A line tap with one tour in reach opens that tour's panel.
- A line tap with more than one opens "N routes pass here"
  (`?tours=`), with each row named by its summit.

The listed tours are drawn as a saved route's legs are (SNOW-1286): a way
up in the climb's dashed grey, a way down in the descent's solid fuchsia.
They had been drawn the other way round — a way up solid, a way down
dashed — which on one map gave the same dash two opposite meanings.

**SAC's points are never edited (SNOW-1287).** A tour is drawn, measured
and saved on exactly the points swisstopo publishes for it: if SAC's tour
stops at the top, so does ours. SAC publishes most of its tours as a way
up with a descent figure and the guide's "as ascent", skied back down the
line it climbed (2,503 of the 2,941). The panel says so in words ("↓ via
ascent", `descends_via_ascent`) and adds no point for it. SNOW-1286 had
drawn such a tour there and back, the line followed by itself reversed,
so its profile, distance and estimate would cover the day; Hugo reversed
that the same day, because a line we made up is not SAC's. A terrain
record walked over that doubled line carries a `there_and_back` mark;
`tour_slope_samples` does not hold one, so the next reader to open the
tour walks it again one way, and `save_ski_tour` never copies one. A copy
saved while the doubled line was live (staging only) keeps it.

**The SAC guide is for members or subscribers.** SAC's route portal asks
anyone else to sign in and subscribe, so the panel says so under the
link rather than letting the link land on a login page unannounced.

**An unsaved tour opens in the same route panel as a saved route.**
`routes:ski_tour_feature` builds the tour as an unsaved `Route`
(`draft_tour_route`) and answers with `_route_feature`. The profile, legs
and estimate therefore come from the code that will show them once the
tour is saved. The Feature has no `uuid` and no `token`, so the panel
offers none of a route's own actions. Its claim slot holds SAC's line
(grade, gain and guide time, labelled as SAC's), the Save button and SAC's
guide link. On the map, the tour is drawn in a slot of its own (map.js
`window.pwaSkiTourRoute`), as a trip's route is, with its own cursor.

**The terrain is sampled once per tour, ahead of time, and kept on the
tour.** Walking the terrain grid under a 10 km line takes several seconds,
which is too long for a tap. Since SNOW-1288 every tour is walked before
anyone opens it: `backfill_ski_tour_slope_samples` runs nightly as the
third pass of `reconcile_slope_samples` and walks every tour in
`tours_to_sample()`, storing the result in `SkiTour.slope_samples`. So a
tour opens with its profile at once, as a saved route does, and every
saved copy inherits the record without sampling again. A sync that
rewrites the tour clears it, and the next night's run walks it again. A
tour the job has not reached yet falls back to the first reader's open:
the first answer is sent at once without the terrain, flagged
`terrain_pending`, and the panel asks again with `?sample=1`, which
samples the line and stores the result.

**The tours and their summits are the map's ski-touring layer, and
everything it draws can be tapped** (SNOW-1285). The layers menu's "Ski
touring" row opens a page with two switches, Tours and Summits, under a
master that is on while either is (as the boundaries' master is). Both are
on by default, and both are drawn from z9. Below z12 the tours come from
`/api/ski-tours.geojson?overview=1`: every tour at once, each line thinned
to about 20 m (`ski_tours.tour_overview`, ~0.6 MB gzipped, loaded once).
From z12 they come from the view's own rectangle at full detail. A tap
works the same way at every zoom. The ids are `OVERLAY_LAYERS.ski_tours`
and `.ski_summits`, and map.js keeps the switches (`window.pwaSkiRoutes`).

swisstopo's raster of its whole network (`ch.swisstopo-karto.skitouren`)
is no longer drawn on the map. SNOW-1283 kept it at every zoom because the
network is bigger than SAC's tours (round the Wildhorn, 19 tours against
111 segments), and SNOW-1285 first faded it under bold tours. Hugo found
lines that could be seen but not tapped confusing (28 Oct 2026), so it
went. The network is still drawn where it is used: in draw mode, as the
vector shadow a leg snaps to (`/api/ski-network.geojson`). The map no
longer calls swisstopo's tile server for it.

## Why

- **A copy, not a reference.** A saved route is the reader's to rename,
  share and plan with. Linking it to a record that the weekly import can
  rewrite would let swisstopo change someone's plan under them. Keeping
  `ski_tour_uuid` is enough to say where the copy came from.
- **Summits, not lines.** People choose a tour by its objective. The
  data shows the overlap is the normal case: 6 tours pass one point just
  outside the Silvrettahütte, and 4 tours reach the Signalhorn.
- **One panel.** A second panel for tours would be a second set of
  figures that could disagree with the saved route's.

## Consequences

- `Route.Source` gains `SKI_TOUR` ("SAC route"), and the GPX export
  credits SAC and swisstopo for it. Like a drawn route, it exports the
  terrain track, because no heights were recorded.
- The routes panel offers three ways to add a route while `route_drawing`
  is on: Find a route, Create a route and Upload a GPX
  (`public/partials/_route_add_rows.html`). The rows replace the panel's
  own add button, and the empty-state sentence names all three.
- The tour panel has the route panel's terrain rows (the "terrain data"
  fold) too, from `routes:ski_tour_terrain` by the tour's uuid
  (SNOW-1282). It reads only the record the tour already holds, so a tour
  never sampled answers no rows until the panel's `?sample=1` refill has
  built one.
- A tour stored in several parts is read as one line from start to
  finish (`ski_tours._chained`, SNOW-1282). swisstopo stores 258 of the
  SAC tours as parts out of order, a tree of the way and a short spur, and
  joining them in stored order doubled the line back on itself. The way is
  the longest path between two loose ends, run in the direction swisstopo
  drew most of it. The map still draws every part, spur included.
- Opening a tour brings it into view only when part of it is hidden,
  and never by zooming in (map.js `frameTour`, `ski_tours_core.fitsView`,
  SNOW-1284). The reader reached it from a summit at a scale they chose,
  and the original fit to each tour's own extent zoomed in and out on
  every pick.
- While a list is open, the tours it names are drawn in the route colour,
  a green dot marks each one's far end (`ski_tours_core.farEnds`: the end
  away from its summit, where a way up starts and a way down finishes),
  and every other tour is dimmed (SNOW-1282). The mockup labels the dot
  with a place name, which the data does not carry, so it is a dot alone.
