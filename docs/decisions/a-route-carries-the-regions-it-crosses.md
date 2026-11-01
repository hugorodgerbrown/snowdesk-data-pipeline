---
name: a-route-carries-the-regions-it-crosses
description: Route.crossed_regions / Trip.crossed_regions, track_regions, resolve_track_regions; Plan tiles, trip danger lines, one alert place per trip
status: current
last-reviewed: 2026-11-01
---

# A route carries the regions it crosses

## Decision

A route, and a trip planned from it, stores the avalanche regions its track
crosses: `Route.crossed_regions` and `Trip.crossed_regions`, a list of
EAWS region ids in the order the track first enters them (SNOW-945).

- **Resolved once, at creation.** `resolve_track_regions` samples the track
  every 50 m (`REGION_STRIDE_M`) and asks `regions_for_points` which region
  each sample lies in. Every creation path writes the list: an upload, a
  drawn route, a claimed share, a saved SAC tour and a new trip. A trip
  copies its route's list, because the geometry is copied too.
- **Read through one function.** `track_regions(stored, points)` turns the
  stored ids into `MicroRegion` rows in one query and keeps the order. A
  row with no stored list (`None`) is resolved live, so a row the backfill
  has not reached still reads correctly. An empty list means the track is
  outside every region, and is not resolved again.
- **Every surface uses every region.** A Plan tile shows each crossed
  region's level for its day, up to three digits and then "+N". The
  caption and read-back name each region. The trip page has one danger
  line per rated region, in route order. An unrated region shows nothing,
  as an unrated day always has.
- **A trip is one alert place.** A trip that crosses several regions is
  one place covering them all, named and linked by the trip. A change in
  any of its regions makes it fire, and the message counts the bulletins
  ("For your trip. 2 bulletins."). It never folds into a pin, because its
  link is the trip page. A trip that stays in one region behaves as
  before, folding into a pin in that region.

## Why

The start region was a guess about the whole day. A tour from Verbier to
Arolla is skied under two bulletins, and showing only the first one hid
the level the party would meet in the afternoon. Hugo's answers on the
scope (2026-11-01): a Plan tile shows every crossed region's level, and a
trip is one alert place.

Storing the list keeps every read path cheap. The trip page, the Plan
page and the evening alert run would otherwise walk the track through a
polygon test on every render. The regions a fixed track crosses cannot
change, except when the region boundaries change, which they do once a
season at most.

## Consequences

- `plan_days.start_region` and `trip_danger` are gone. `trip_dangers` and
  `plan_week` take the region list.
- The send log stays keyed on (account, region, day). A multi-region trip
  writes one row per region, and fires when any of its regions does. The
  scope's per-place fingerprint would have needed a new key on
  `BulletinAlert`; per-region rows give the same answer without one. Where
  a pin shares a region with such a trip, both address one log row, and
  `deliver` writes it once, the sending row winning
  (`_one_row_per_log_key`), so a quiet pin never records the trip's
  delivered day as untold.
- A multi-region alert states no level and no new snow. One region's
  figures would misstate the others, and the trip page lists each.
- Existing rows are filled by `backfill_crossed_regions --commit`, run as
  a Deploy-Step. Until it runs, those rows resolve live.
- A boundary change in the EAWS fixtures leaves stored lists stale. Clear
  the column (`crossed_regions = NULL`) and rerun the backfill.
