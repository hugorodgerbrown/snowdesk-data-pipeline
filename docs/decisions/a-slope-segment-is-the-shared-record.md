---
name: a-slope-segment-is-the-shared-record
description: Route.slope_samples, slope_segments.py — a 25 m stride sampled from the terrain not the track; null means never sampled, not unknown
status: current
last-reviewed: 2026-09-14
---

# A slope segment is the shared record, and it samples the ground

## Decision

`Route.slope_samples` (SNOW-910) holds one record per route: a 25 m stride
walk along the track, with the steepness of the **ground** at the middle of
each stride.

```json
{ "window_m": 10.0, "stride_m": 25.0, "grid": "snowdesk-terrain-5m-3035",
  "points":   [[lon, lat], ...],                        // N + 1
  "segments": [ {"angle_deg": 34.2, "aspect_deg": 105.3},
                {"unknown": "outside_coverage"}, ... ] } // N
```

Five rules go with it:

- **The terrain, never the track.** Nothing reads the third ordinate of
  `Route.points`.
- **`null` on the field means NEVER SAMPLED**, which is not the same fact as
  a segment carrying an `unknown` reason.
- **All three `TerrainUnknown` reasons are kept**, though the map collapses
  them to one dashed treatment.
- **The wire form is compact and different**: `properties.slope` is
  `{points, angles}` with a null angle for an unknown, and the key is
  **omitted entirely** for a route that has never been sampled.
- **A run in which every sample was `UNAVAILABLE` stores nothing.**

## Why

### The track's own gradient is free, and it is a lie

`Route.points` already carries an elevation per coordinate, so a gradient
along the track costs one subtraction. It is also the wrong number, in the
dangerous direction: a skin track zigzagging up a 38° face rises about 15°
along its own length, and a rising traverse across the same face rises near
zero. Both would paint green. "The map said it was fine" is the failure
this whole feature exists to prevent, so the arithmetic that produces it is
not in the codebase at all — not behind a flag, not as a fallback.

Sampling the terrain instead costs an HTTP request per terrain tile the
track crosses. That is why it runs in a background task rather than in the
upload response, and why backfilling the historical rows is a
`--commit`-gated command rather than a migration.

### Null and unknown are different facts and must render differently

`apps/locations/services/terrain.py` refuses to return a bare `None` for a
sample it cannot answer, on the grounds that a null is one `or 0` away from
being painted as gentle ground
([terrain-unknown-is-a-reason-not-a-null](terrain-unknown-is-a-reason-not-a-null.md)).
The same rule has to survive one layer up, where there is now a **second**
kind of absence: a route nothing has looked at yet.

They call for different treatments on screen. A never-sampled route keeps
the flat fuchsia line it has always had — an honest "no claim made". A
sampled route with unanswerable stretches draws those dashed and grey — "we
looked, and this ground is not surveyed". Collapsing the two would mean
either dashing every route nobody has sampled (a claim we have not earned)
or drawing unsurveyed ground flat and uncoloured (indistinguishable from
gentle). So the field's null is the first, an `unknown` segment is the
second, and the GeoJSON keeps them apart by the PRESENCE of the `slope`
property rather than by its value.

That presence is also load-bearing on the map: `routes-line` filters on
`['!', ['has', 'slope']]` so a sampled route is not painted flat underneath
its own colours. A present-but-null value would answer that filter wrongly.

### Aspect is stored and not sent

`sample_slope` computes the angle and the aspect from one kernel, so the
bearing is free at sampling time and would cost a second full pass over the
tile origin to recover later. SNOW-839 needs it to score a route against a
bulletin's aspect bands. Nothing draws it.

Sending it anyway would roughly double the payload for a 15 km tour — 600
segments — on a feed the offline cache holds. So the stored record is the
server-side truth that SNOW-911 (cruxes) and SNOW-839 (bulletin scoring)
read, and the wire form is the subset the map paints. They are deliberately
not the same shape, and `_compact_slope` in `apps/routes/views.py` is the
one place the reduction happens.

### The sample points have to travel

They are not `Route.points`. The stride interpolates between stored
coordinates, so a segment boundary usually falls between two of them and
the geometry cannot be recovered from the route. N + 1 coordinates bounding
N segments — sharing each boundary between the two segments it joins — is
what keeps that from being 2N.

### An outage is not a fact about the ground

`UNAVAILABLE` means our tile origin could not be read, and an unreachable
origin answers it for every point on a track. Writing that record out would
store a statement about the ground that is really a statement about us, and
— worse — it would take the row out of the backfill command's candidate
set, which selects on the field being null. So an all-`UNAVAILABLE` run
leaves the field as it found it and logs a warning. A PARTIALLY unavailable
record is stored: a mixed result is genuine information about where the
sampling got to.

Nor is an outage waited out. A failure is deliberately not memoised — it
has to be retried, not cached — so every remaining midpoint would re-attempt
the same dead tiles at the transport's full timeout, which for a long track
is minutes of a shared task worker spent reaching an answer already known.
Three CONSECUTIVE `UNAVAILABLE` results end the walk with the same "left
unsampled" outcome (`_UNAVAILABLE_RUN_LIMIT`). Consecutive, because an
isolated dead tile on an otherwise good track must still store its other
hundred-odd samples, and any other result — including `OUTSIDE_COVERAGE`
and `NO_DATA`, which are answers rather than failures — resets the count.

## Alternatives rejected

**Sample at the segment boundaries rather than the midpoints.** An
end-sampled segment takes its colour from a point it only touches, and has
to choose between its two ends to do it. The midpoint is the one sample
most representative of the ground the segment crosses, and it costs N
samples rather than N + 1.

**Colour pending shares too.** A followed-but-unclaimed share is drawn as a
teal dashed line meaning "this one is not yours yet", which is the only
action it offers. One line cannot carry two messages; saving it makes it an
owned route, and owned routes are coloured.

**Store the compact form and derive the rest on demand.** Aspect cannot be
derived from an angle, and re-sampling to recover it is the second pass
over the origin this record exists to avoid.

## Consequences

- A newly uploaded route draws flat until its background task lands. That
  is visible and is the intended reading: it has not been sampled yet. The
  map re-reads the routes feed ONCE, twenty seconds later, so the colouring
  arrives without a page reload; one shot, never a poll, and inert wherever
  the task ran inline.

  **Armed by the two writes that PUT A ROUTE ON THE SERVER — an upload and
  a claim — and by neither of the two that do not.** A rename and a delete
  raise the same `snowdesk:routes-changed` announcement and can never
  produce a route about to gain a record, so arming off them would cost a
  refetch on every visit for the life of any legacy route the backfill
  never reached. The claim was excluded at first, on the reasoning that it
  copies the sharer's record and so arrives already coloured; it does,
  except when it beats the sharer's own sampling task, which is the same
  race the enqueue below exists for. Both claim paths flag it — the
  panel's row form in `static/js/routes.js` and the map popup's own Save
  in `static/js/map.js`.
- Routes uploaded before SNOW-910 stay flat until an operator runs
  `backfill_route_slope_samples --commit`.
- Coverage is the terrain tileset's — Switzerland and its immediate
  surrounds — so a route in the Dolomites is dashed for its whole length.
  `/help/#help-topic-slope` says so, beside the raster's own coverage
  caveat.
- A claimed share copies the record rather than re-sampling identical
  geometry (`_COPIED_FIELDS` in `apps/routes/services/shares.py`) — and
  samples the COPY when the source had nothing to give, which a claim that
  beats the sharer's own task sees. Nothing else would ever sample it: the
  sharer's task carries the source's pk, and the backfill runs once.
- **A trip-saved route is re-sampled rather than handed a record**
  (`save_trip_route` in `apps/trips/services/routes.py`, SNOW-910), which
  is the opposite of the line above and not an inconsistency. A claim has
  the record in hand — `share.route.slope_samples` is one field access.
  A trip has no route to read: its geometry is a SNAPSHOT and `Trip.route`
  is provenance only and may be null
  ([a-trip-is-one-object-with-a-roster](a-trip-is-one-object-with-a-roster.md)).
  So the question there is not "copy or re-sample" but "put a slope column
  on `Trip` or ask the origin again", and the column was declined: the
  snapshot exists to hold a trip still against changes the ORGANISER could
  make, the terrain is nobody's to edit, no trip surface draws a coloured
  line, and a trip snapshotted from a freshly-uploaded route would carry a
  null record anyway — so the column would buy tile reads and never
  correctness. The re-sample is the same question over the same geometry,
  and the only thing that moves under it is the tileset, whose next
  version is a correction rather than drift.
