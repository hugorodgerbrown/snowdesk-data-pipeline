# Sample days — real observed hourly weather

Three committed days of real Open-Meteo data for **Verbier village**
(46.0956, 7.2203; the model's own cell elevation is **1436 m**), each a
complete 24-hour series carrying every field of
`apps.weather.types.HourlyRow`.

They exist so the hourly chart component can be built and iterated against
weather that actually happened. A generated series cannot do that job: an
arithmetic ramp draws every band as a straight diagonal, the component reads
as a test pattern, and it becomes impossible to tell which visual problems
are real ones (SNOW-776).

| File | Character | Temp (°C) | Freezing level (m) | Snow / precip | Gusts |
|---|---|---|---|---|---|
| `2026-02-16-verbier-storm.json` | Warm Atlantic storm | −2.5 … +1.6 | 1250 … 1760 | 15.7 cm / 27.0 mm | 53 km/h |
| `2026-01-05-verbier-cold-clear.json` | Settled midwinter high | −12.1 … −6.1 | 580 … 1000 | none | 16 km/h |
| `2026-04-11-verbier-spring-thaw.json` | Spring warmth | +8.7 … +18.7 | 2920 … 3230 | none | 29 km/h |

## Why these three

They were picked to disagree with each other on every axis the chart draws,
by ranking a whole winter (2025-12-01 … 2026-04-30) of daily summaries and
taking the extremes:

- **The storm day** is the busiest the season got — both bar series full,
  the temperature line crossing 0 °C twice, and the freezing level moving
  500 m within the day. It is the day the design handoff's own summary
  figures were drawn from.
- **The cold clear day** is the empty case: not one millimetre of
  precipitation in 24 hours, so both bar bands render with nothing in them,
  and the freezing level sits *below* the village for the whole day.
- **The spring thaw day** never goes near freezing, so the chart has no
  0 °C crossing to draw at all and the freezing level is off the top of any
  winter-shaped scale.

Between them they demonstrate why the vertical scales are derived from the
data rather than fixed: the handoff originally specified −6 … +4 °C and
1000 … 2600 m, and **two of these three real days fall entirely outside
both**.

## What they do not cover

Every field is populated in all three — there is not a single null across
the 72 rows. The "break the line, do not interpolate" rule for a missing
value is therefore **not** exercised by this data and needs its own
constructed case when tests are written.

## Provenance

Retrieved 2026-08-31 from
`historical-forecast-api.open-meteo.com/v1/forecast` — the archive of the
forecast model's own past runs, which unlike the ERA5 archive endpoint
carries `freezing_level_height`. Open-Meteo data is CC BY 4.0.

The files are stored in the provider's shape, not a Django fixture: `hourly`
is a list of `HourlyRow` dicts and the day-level keys mirror `ForecastDay`,
so a consumer loads one and passes it straight through. This follows
`apps/bulletins/local_mirrors/` — committed real payloads, not `loaddata`
input.
