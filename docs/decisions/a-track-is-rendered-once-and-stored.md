---
name: a-track-is-rendered-once-and-stored
description: track_render stored on Route, Trip and SkiTour; build_track_render, store_terrain the one writer, readers only read, rebuild_track_renders
status: current
last-reviewed: 2026-10-30
---

# A track is rendered once and stored

## Decision

**Every track Snowdesk draws holds its drawing.** A `Route`, a `Trip` and
a `SkiTour` each carry one JSON field, `track_render`
(`apps/routes/services/track_render.py`):

- the terrain track (`terrain_points`), the line the map draws;
- `ascent_m` / `descent_m` (`climb_figures`);
- `slope` (`compact_slope`), `legs` (`wire_legs`) and `terrain`
  (`summarise_record`), each omitted when there is none;
- `rows` (`terrain_detail`), the point card's terrain data;
- a version `v` (`TRACK_RENDER_VERSION`).

**One builder.** `build_track_render(points, slope_samples, *, ascent_m,
descent_m)` is exactly what `_route_feature` and the trip page computed
per request before. The stored ascent and descent go in as the fallback
for a track with no model heights, because the simplified points cannot
recover the parser's full-resolution figures.

**One writer.** `store_terrain(obj, record)` is the only path that writes
`slope_samples`. It writes the render with it, so the two cannot describe
different things. Creation paths stamp the no-terrain render
(`stamp_render`, `stamped_fields`). A copy builds its own render from what
it copies rather than taking the source's. A SkiTour's no-terrain state
is a null render (`cleared_terrain`, written by the weekly sync), read by
the tours layer as SAC's own stored line, because which way its line
runs is a guess until the terrain is walked.

**Readers only read.** The routes feed, the trip page and its meeting
picker, the three terrain-rows endpoints, the tour panel, the tours layer
(bbox and overview) and `Route.climb` / `Trip.climb` read the record.
`current_render` builds a missing or stale one in memory, logs it for a
saved row and never writes it on a GET.

## Why

Hugo, 30 Oct 2026: "Tours and routes don't change. All of the render
data should be precalculated. Both should store the same shape of data.
Only the source changes, along with the additional metadata that comes
with SAC." Cutting legs for all 2,941 tours per request took 35 s. More
to the point, a tour and a route that held different shapes drew
differently: the tours layer labelled a whole tour "up" or "down" where a
route showed its legs.

## Consequences

- A change to any builder (`terrain_points`, `compact_slope`,
  `wire_legs`, `summarise_record`, `terrain_detail`, `climb_figures`)
  must raise `TRACK_RENDER_VERSION` and ship a
  `Deploy-Step: rebuild_track_renders --commit` trailer. Until it runs,
  readers build in memory and log a warning per read.
- A new write of `slope_samples` goes through `store_terrain`, or the
  render goes stale silently.
- The draw preview (`routes:draw_preview`) is never stored. It builds in
  memory through the same builder.
- The overview stays thinned per cache build, keyed by the tours' stamp,
  and reads only the render's `geometry` and `legs` (`drawn_tours`).
  Storing a second, thinned copy was declined: about 1 s once per tour
  change.
- The row's own columns (`distance_m`, `bounds`, `duration`) stay read
  from the row. They are already stored, and a copy in the record would
  be a second place for them to disagree.
- Since SNOW-1316 the builder is `track.render.build_track_render`, a
  pure package, and a render carries `fp`; `store_terrain` writes nothing
  when the new `fp` equals the stored one. A SAC tour's render is made by
  the reference-data service (`render_ski_tours`, on `SourceSkiTour`) and
  copied into `SkiTour` from the feed; see
  [pure-track-maths-is-a-package](pure-track-maths-is-a-package.md).
