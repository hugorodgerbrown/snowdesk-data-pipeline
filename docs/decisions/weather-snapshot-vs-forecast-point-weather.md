---
name: weather-snapshot-vs-forecast-point-weather
description: WeatherSnapshot and ForecastPointWeather stay separate models — the split is archive vs forecast, not region vs point
status: current
last-reviewed: 2026-08-09
---

# WeatherSnapshot and ForecastPointWeather stay separate models

**Decision.** `WeatherSnapshot` (one row per region-day, forecast or
backfilled archive) and `ForecastPointWeather` (one row per favourited
point-day, forecast-only) remain two models rather than merging into one.
SNOW-571 added `temperature_2m_max`/`temperature_2m_min`/`snowfall_sum` to
`WeatherSnapshot` — the same names and units `ForecastPointWeather` already
uses — narrowing the field overlap, but the two tables still do not merge.

**Why.** The real dividing line is **archive vs forecast**, not **region vs
point**:

- `WeatherSnapshot` is thin, dated, and permanent. It backfills whole
  seasons (`backfill_all_regions`) and every row it ever writes stays
  meaningful — a historical bulletin page reads a five-year-old snapshot
  exactly as it reads yesterday's. It carries only the fields that make
  sense on an archived day: the WMO code, sunrise/sunset, and (as of
  SNOW-571) the daily temperature/snowfall trio.
- `ForecastPointWeather` is rich, rolling, and evictable. It is upserted on
  `(forecast_point, valid_for_date)` for a `POINT_FORECAST_DAYS`-day
  window and carries sixteen nullable columns plus an hourly-detail JSON
  blob — apparent temperature, precipitation, wind, UV, freezing level —
  because a favourited pin is a personal detail card, not an archive
  record. There is no archive/backfill equivalent (SNOW-416, SNOW-417):
  Open-Meteo has no meaningful "what would ICON-CH have forecast for this
  point three years ago" answer, and `ForecastPoint` rows themselves are
  pruned once their last favourite/resort goes away
  (`docs/decisions/forecast-point-quantisation.md`), so old point-weather
  rows are routinely discarded rather than retained.

Merging would push all sixteen extended `ForecastPointWeather` columns
(nullable, since Open-Meteo omits some depending on the backing model) plus
the hourly JSON blob onto a table growing roughly one row per region per
day across a whole season (~30k rows/season) that can never populate them
— every archived day would carry sixteen permanent NULLs for fields an
archive fetch never requests. It would also trade
`unique_together(region, valid_for_date)` — a direct, indexable key — for a
nullable FK hop through a `ForecastPoint`, whose identity is shared by
reuse-first quantisation rather than being unique per region.

**Consequences.**

- `_build_snapshot_defaults` (region) and `_build_point_defaults` (point)
  in `apps/weather/services/weather_fetcher.py` stay separate functions,
  even though both now read `temperature_2m_max`/`temperature_2m_min`/
  `snowfall_sum` via the same degrade-to-`None` accessor shape. A future
  field added to one model's daily block does not automatically apply to
  the other — each addition is a deliberate per-model decision, not a
  shared-schema side effect.
- `build_weather_display()` (`apps/weather/services/weather_display.py`)
  is the integration point that already treats the two models
  interchangeably for the fields they share (`weather_code`/`sunrise`/
  `sunset`, and now `temp_max`/`temp_min`/`snowfall_sum` via `getattr(...,
  None)`) — that duck-typing is the seam, not a shared base class or table.
- If a future ticket wants a genuinely richer region-day record (e.g. wind,
  UV), it should ask the same archive-vs-forecast question again rather
  than assuming parity with `ForecastPointWeather`'s field list.
