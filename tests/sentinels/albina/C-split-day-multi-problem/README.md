# ALBINA sentinel C — split-day + multi-problem

| Field | Value |
|---|---|
| **bulletinID** | `641ddea6-e0de-4199-90ce-4dfc25fb6da2` |
| **Date** | 2025-12-06/07 (validTime startTime 2025-12-06T16:00:00Z, endTime 2025-12-07T16:00:00Z) |
| **Region** | AT-07 (Northern Tyrol), covering 5 sub-regions |
| **Source** | `bulletins/local_mirrors/albina_archive.ndjson` |

## Why this is variant C

Three `dangerRating` entries spanning two time periods:

- `mainValue: low`, `validTimePeriod: earlier` (no elevation split)
- `elevation.upperBound: 2400`, `mainValue: low`, `validTimePeriod: later`
- `elevation.lowerBound: 2400`, `mainValue: moderate`, `validTimePeriod: later`

Two distinct avalanche problem types.  The combination of a time split
(`earlier` / `later`) with an elevation split within the `later` period, plus
two problem types, is the defining characteristic of variant C.

## PDF

See `albina/A-single-level/README.md` — ALBINA does not publish stable archive PDF
URLs.  `source.pdf` is absent from this directory.
