---
name: weather-is-one-immutable-location-row
description: Weather: one row per (Location, observed_on), final once past; forecast_on, lead_days, hourly per row; WeatherQuerySet.window
status: current
last-reviewed: 2026-11-01
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
  forecast_on   date — the day the fetch that last wrote it ran
  fetched_at    datetime — updated on every write

  18 daily scalars describing observed_on
  hourly        JSON — observed_on itself, hour by hour

  unique_together (location, observed_on)
```

Three rules follow, and they are the whole decision:

1. **A row is the account of its own day, refined until the day passes,
   then final** (SNOW-1337, Hugo, 1 Nov 2026). Every fetch brings the next
   `FORECAST_DAYS` (seven) days and writes them as seven rows, today to six
   days ahead, each with its own `hourly`, overwriting what the previous
   fetch wrote for those days. A day first appears six days out
   (`lead_days = observed_on - forecast_on = 6`) and is refined daily until
   it is today (lead time 0), the value every past row ends with. No row
   holds a list of later days: the outlook from any day, today or one in
   the past, is the next rows for that place
   (`Weather.objects.window(first_day, days)`, `following_rows`).
2. **A past row is immutable.** `upsert_weather` creates when absent,
   refines in place when the day is today or later, and raises
   `ImmutableWeatherRowError` when the day has passed. `Weather.save()`
   re-checks against the database as the backstop for admin and shell
   writes. Creating a past-dated row stays legal — recording a day never
   recorded is not a rewrite.
3. **Today's row is updated in place, not appended.** Every read path is
   therefore a `.first()` on the unique constraint, never an
   `.order_by("-fetched_at").first()`.

**The fetch is one walk over `Location.objects.active()`** — since
2026-10-28 the curated estate only: a location reachable from a
`ResortLocation`, a resort's pin or a `MicroRegion.centroid_location`.
A `Favourite` and an upcoming trip's meeting point were in it until then
(SNOW-1217); they now borrow the nearest curated point's row instead
([snowdesk-decides-where-a-forecast-is-fetched](snowdesk-decides-where-a-forecast-is-fetched.md)),
and one reachable only from a `FieldObservation` never was. One shared queryset method, because the map feed asks
the same question and two implementations would drift until a private pin
leaked into a public feed.

**Every row carries its hourly series** (`HOURLY_DAYS = FORECAST_DAYS`).
Open-Meteo returns the full seven-day hourly block in the one request, so
this costs no API call, only storage (about 5.6 KB a row). A row without
one is a row written without it, which every reader still tolerates.

**Convergence history is deliberately not kept** (Hugo, 1 Nov 2026). What
D-6 said about a day is overwritten by D-5. Until SNOW-1337 a row carried a
`forecast[]` list of the six days after it as known on it, so the history
sat in seven copies of each day across seven rows — most of the table's
161 MB, read by nothing but the outlook. If the convergence is ever wanted
it is a one-off deep dive against Open-Meteo's historical-forecast API,
not a stored series.

## Why

**Three tables were one table's worth of data.** `WeatherSnapshot` and
`ForecastCellWeather` held the same variables against two different anchors —
a region centroid and a quantised grid cell — because those anchors were
different kinds of thing. Since SNOW-700 both *are* `Location`, so the split
had nothing left to express. `ForecastCellWeatherHistory` existed to retain
each issue's view of a forecast day before the next run overwrote it;
SNOW-757 kept that view as a `forecast` column, and SNOW-1337 dropped it
(rule 1 above).

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
reverse this: if `active()` ever widened to every favourite again, the
estate could reach thousands with genuine clustering. Re-measure then.
Since 2026-10-28 it holds only the curated estate, which makes that less
likely, not more.

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

- **The row exists twice since SNOW-1253, under one rule.** The weather
  service keeps its own account of each reference place's day
  (`apps.ingest.models.SourceWeather`, written by `store_source_weather`,
  seven rows a fetch since SNOW-1337, with the same `forecast_on`), and
  every instance applies it into `Weather` through `upsert_weather`, row
  for row. Both refuse to rewrite a past day, so a pull can never change
  history the service has fixed.
- **`weather_weather`, not `bulletins_weather`.** A genuinely new table takes
  the natural name; none of the `bulletins_*` pinning SNOW-654 left behind
  carries forward, and SNOW-655 (the rename) is retired.
- **`apps/weather/migrations/0001`–`0006` stay.** Six migrations in four
  other apps depend on them. `0007` creates `Weather` on the surface `0006`
  left empty. The old numbers go only with a migration-history rewrite.
- **A reader of the outlook reads rows, not a list.** A bulk reader (the
  map feed, a resort's points) reads every place's week in one query with
  `window(...)` and groups it; a single sheet's `build_point_forecast_panel`
  reads its six following rows in one query. A day with no row is absent.
- **A backfilled row has lead time zero.** `apps/ingest/backfill.py` writes
  a past day from the historical API with `forecast_on = observed_on`,
  the value a fetched row ends with.
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
