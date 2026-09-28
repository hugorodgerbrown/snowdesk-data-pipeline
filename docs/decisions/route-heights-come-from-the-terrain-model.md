---
name: route-heights-come-from-the-terrain-model
description: slope_samples heights, terrain_points, climb_totals — profile, legs, ascent/descent, track gradient on model heights
status: current
last-reviewed: 2026-09-29
---

# A route's heights come from the terrain model, not the altimeter

## Decision

The slope sampler records the terrain model's height at every 25 m
boundary as `heights` on `Route.slope_samples` / `Trip.slope_samples`
(SNOW-1043) — one entry per boundary, metres to one decimal, `null` where
the model has no ground. Every figure read off a track's elevation is then
read off those heights:

- the routes feed and both trip payloads send `terrain_points(points,
  slope_samples)` as the geometry, so the elevation profile draws the
  model's series;
- the legs on the wire (`wire_legs`) are cut from the same points;
- the ascent and descent every surface shows — the feed, both trip
  payloads, the route row, the trip page's figures line and stats row, and
  the past-trip row — come from one rule, `climb_figures`, read through
  `Route.climb` / `Trip.climb`: `climb_totals` of those points when the
  record has heights, the stored columns when it has not;
- the along-track gradient in `terrain_detail` reads `heights` directly;
- the bulletin panels match elevation bands on the same points.

The device's own elevation stays the fallback wherever a height is `null`
or the record has no `heights` key. Nothing stored is rewritten:
`Route.points`, `ascent_m` and `descent_m` stay what `parse_gpx` produced.

## Why

A GPX's `<ele>` is what the recording device's barometric altimeter said,
and an altimeter drifts with the weather over the hours of a tour. On the
Mont Fort – Backside canonical track the recorded height starts 139 m
above the terrain model and is 744 m above it 1.5 km later, while the
skier is descending. `detect_legs` read that as a 215 m climb followed by
an 874 m drop; on the model's heights it is one descent of 548 m (570 m of
gross descent). The stored ascent was 478.8 m; on the model it is 320.9 m.

The heights cost nothing extra to obtain. The sampler already visits the
terrain grid at each boundary for the angle, so the tiles are in the
process cache and the height is one cell read. The per-point elevation is
interpolated between boundaries along the track, so a 25 m resolution is
what the profile gets — finer than the eye separates on a line a few
pixels wide, and coarser than the altimeter's noise.

The heights are applied at read time rather than written into
`Route.points` for three reasons: the device series is the user's upload
and the comparison between the two is the recording-fault signal
(`terrain_detail`'s rejection check); a record resampled against a newer
grid changes every figure without a migration over user rows; and a
record that cannot be matched to the points (`terrain_points` re-walks
the stride and declines on a boundary-count mismatch) falls back to the
stored track rather than to a height placed against the wrong ground.

**Elevation is still read at the recorder's coordinate.** This does not
reverse `bulletin_join`'s rule that where the skier stood comes from the
track. The coordinate is the recorder's; only the height at it is the
model's.

## Consequences

- A route or trip sampled before SNOW-1043 serves device heights until
  `backfill_route_slope_samples` / `backfill_trip_slope_samples` re-walks
  it; both select a record lacking `heights`.
- An outage during the height pass omits the key entirely, as the crux
  pass does, so the row stays a backfill candidate. A `null` is only ever
  a permanent "no ground here".
- Ground outside the model's coverage keeps the device's heights, and a
  drifting altimeter there is still read as recorded (100 of the Chamonix
  track's 1,134 points).
- Stored `ascent_m` / `descent_m` and the shown figures can disagree. The
  Django admin's list and detail views still show the stored columns — they
  are the parser's record of the upload, which is what staff are auditing.
- `bin/record-slope-fixtures` captures the canonical tracks' records from
  the live tile origin into `tests/routes/fixtures/slope_records/`, so
  tests pin real-data behaviour without reaching the network.
- Related: [a-slope-segment-is-the-shared-record.md](a-slope-segment-is-the-shared-record.md)
  (the angle is still never read from the track).
