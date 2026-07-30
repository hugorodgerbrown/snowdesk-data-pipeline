# ALBINA sentinel B — elevation-band split

| Field | Value |
|---|---|
| **bulletinID** | `6dc598e1-2a96-466b-9a60-9ab72f94ace4` |
| **Date** | 2025-11-28/29 (validTime startTime 2025-11-28T16:00:00Z, endTime 2025-11-29T16:00:00Z) |
| **Region** | AT-07 (Northern Tyrol), covering 12 sub-regions |
| **Source** | `apps/bulletins/local_mirrors/albina_archive.ndjson` |

## Why this is variant B

Two `dangerRating` entries, both `validTimePeriod: all_day`, split at 2200 m:

- `elevation.upperBound: 2200`, `mainValue: low`
- `elevation.lowerBound: 2200`, `mainValue: considerable`

One avalanche problem type.  No time split.

## PDF

See `albina/A-single-level/README.md` — ALBINA does not publish stable archive PDF
URLs.  `source.pdf` is absent from this directory.
