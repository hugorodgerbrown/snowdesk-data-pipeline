# ALBINA sentinel C — split-day + multi-problem

| Field | Value |
|---|---|
| **bulletinID** | `27357867-b6eb-47a3-9542-d3a91e77eb6d` |
| **Date** | 2026-02-25/26 (validTime startTime 2026-02-25T16:00:00Z, endTime 2026-02-26T16:00:00Z) |
| **Region** | AT-07 (Northern Tyrol), covering 4 sub-regions (Brandenberg Alps, Kaiser Mountains, Kitzbühel Alps Wildseeloder, Karwendel Mountains East) |
| **Source** | `bulletins/local_mirrors/albina_archive.ndjson` |

## Why this is variant C

Three `dangerRating` entries spanning two time periods:

- `elevation.upperBound: treeline`, `mainValue: low`, `validTimePeriod: earlier`
- `elevation.lowerBound: treeline`, `mainValue: considerable`, `validTimePeriod: earlier`
- `mainValue: considerable`, `validTimePeriod: later` (no elevation split)

Two distinct avalanche problem types:

- `persistent_weak_layers` — present in both `earlier` and `later` periods on N/NW/NE aspects above treeline
- `wet_snow` — present in `later` period on SE/W/SW/S aspects (all elevations)

The combination of a time split (`earlier` / `later`) plus two distinct avalanche problem types is the defining characteristic of variant C.

## PDF

See `albina/A-single-level/README.md` — ALBINA does not publish stable archive PDF
URLs.  `source.pdf` is absent from this directory.
