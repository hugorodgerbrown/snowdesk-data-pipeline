---
name: weather-is-one-immutable-location-row
description: Weather model — one row per (Location, observed_on), immutable once past; upsert_weather, fetch_weather, Location.objects.active()
status: current
last-reviewed: 2026-08-30
---

# Weather is one immutable row per location per day (SNOW-757)

**Supersedes**
[`weather-snapshot-vs-forecast-point-weather`](weather-snapshot-vs-forecast-point-weather.md),
[`forecast-point-quantisation`](forecast-point-quantisation.md),
[`weather-is-its-own-app`](weather-is-its-own-app.md),
[`icon-ch-domain-bounding-box`](icon-ch-domain-bounding-box.md),
[`resort-page-weather-shows-region-snapshot`](resort-page-weather-shows-region-snapshot.md),
[`resort-page-shows-point-forecast`](resort-page-shows-point-forecast.md) and
[`resort-page-shows-location-forecasts`](resort-page-shows-location-forecasts.md).
Those describe an estate SNOW-762 stripped whole. One argument survives from
them and is restated below: weather is still its own app.

## Decision

**One table. One row per `(location, observed_on)`. Never rewritten once
that day is past.**

```
Weather                                apps/weather/
  location      FK → locations.Location, CASCADE
  observed_on   date — the day this row is OF
  fetched_at    datetime — updated on every write

  17 daily scalars describing observed_on
  hourly        JSON — observed_on itself, hour by hour
  forecast      JSON — the days AFTER it, as known ON it

  unique_together (location, observed_on)
```

Three rules follow, and they are the whole decision:

1. **A row is an account of a day, not a cache of it.** `forecast` records
   what the forward days looked like *on* `observed_on`. Read a week later,
   the row still says what was being forecast at the time.
2. **A past row is immutable.** `upsert_weather` creates when absent,
   refines in place when the day is today or later, and raises
   `ImmutableWeatherRowError` when the day has passed. `Weather.save()`
   re-checks against the database as the backstop for admin and shell
   writes. Creating a past-dated row stays legal — recording a day never
   recorded is not a rewrite.
3. **Today's row is updated in place, not appended.** Every read path is
   therefore a `.first()` on the unique constraint, never an
   `.order_by("-fetched_at").first()`.

**The fetch is one walk over `Location.objects.active()`** — a location
reachable from a `ResortLocation`, a `MicroRegion.centroid_location` or a
`Favourite`, and explicitly *not* one reachable only from a
`FieldObservation`. One shared queryset method, because the map feed asks
the same question and two implementations would drift until a private pin
leaked into a public feed.

**`hourly` nests in the first `HOURLY_DAYS` `forecast[]` entries.**
`observed_on` carries its own series in a column; the next day carries one
inside its forecast entry. So `hourly` is an **optional per-entry key** —
`apps/weather/types.py` declares it `NotRequired` and every consumer must
read it with `.get()`.

## Why

**Three tables were one table's worth of data.** `WeatherSnapshot` and
`ForecastCellWeather` held the same variables against two different anchors —
a region centroid and a quantised grid cell — because those anchors were
different kinds of thing. Since SNOW-700 both *are* `Location`, so the split
had nothing left to express. `ForecastCellWeatherHistory` existed to retain
each issue's view of a forecast day before the next run overwrote it; the
`forecast` column now holds exactly that, without a second table to keep in
step.

**Immutability is the fix for a real bug, not a purity argument.** The old
write path rewrote a day's row in place after that day had passed, so what
we said and what turned out to be true became indistinguishable. Worse, the
path degraded quietly — SNOW-628's zero-row bug survived months because a
failed write logged nothing anyone read. Raising is deliberate: a caller
trying to rewrite history has a bug, and one traceback is cheaper than
silently serving a rewritten past.

**The quantisation grid was measured and did nothing.** On 2026-08-29, 240
real places produced 240 distinct quantised cells — zero sharing, in a test
rigged in dedup's favour. The grid existed to keep the `ForecastCell` *table*
small, and there is no longer a table for it to keep small. What would
reverse this: if `active()` ever widened to every favourite, the estate could
reach thousands with genuine clustering. Re-measure then.

**Weather is still its own app.** The reason survives the collapse from four
models to one: a different upstream (Open-Meteo, not the CAAML providers), a
different cadence (a 4×/day scheduled batch), a different failure mode (a
rate limit and a billable call count), and no foreign key to bulletins in
either direction. Putting `Weather` in `apps/locations/` would make the
primitive every other app reaches through depend on Open-Meteo fetch
semantics.

**The cadence stays 4×/day even though rows are immutable.** Bulletin
regions have a live on-demand fetch behind the page render; locations have
no equivalent, so the scheduled batch is the only thing keeping today's row
current — and today's row is the one every surface reads.

## Consequences

- **`weather_weather`, not `bulletins_weather`.** A genuinely new table takes
  the natural name; none of the `bulletins_*` pinning SNOW-654 left behind
  carries forward, and SNOW-655 (the rename) is retired.
- **`apps/weather/migrations/0001`–`0006` stay.** Six migrations in four
  other apps depend on them. `0007` creates `Weather` on the surface `0006`
  left empty. The old numbers go only with a migration-history rewrite.
- **A consumer must presence-check `forecast[i]["hourly"]`.** It is absent
  beyond the first `HOURLY_DAYS` entries by design.
- **A field observation never mints a forecast.** Asserted in
  `tests/locations/test_models.py`, not left to a comment.
- **A historical backfill needs no exception.** It creates rows for days
  never recorded, which the rule already permits, so it uses `upsert_weather`
  like everything else.
- **The daily estate grows with the centroid backfill.** Giving all 461
  micro-regions a centroid `Location` adds ~1,800 Open-Meteo calls a day.
  Confirm plan headroom before running
  `link_region_centroid_locations --commit` in production — see
  [`../runbooks/region-centroid-backfill.md`](../runbooks/region-centroid-backfill.md).
