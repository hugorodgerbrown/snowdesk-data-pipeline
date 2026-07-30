# SLF sentinel C — split-day + multi-problem

| Field | Value |
|---|---|
| **bulletinID** | `0333d1d0-17eb-4e22-9fc9-98a914a3984e` |
| **Date** | 2026-03-05 (validTime 2026-03-05T16:00:00Z – 2026-03-06T16:00:00Z) |
| **Region** | All CH micro-regions (full-country bulletin) |
| **Source** | `apps/bulletins/local_mirrors/slf_archive.ndjson` |

## Why this is variant C

Two `dangerRating` entries across different time periods:

- `mainValue: considerable`, `validTimePeriod: all_day`, `customData.CH.subdivision: minus`
- `mainValue: moderate`, `validTimePeriod: later`

Two avalanche problem types in the `aggregation`:
- `persistent_weak_layers` (whole day)
- `wet_snow` (as the day progresses)

This combination of a time split (`all_day` + `later`) with two distinct problem
categories (dry + wet) is the defining characteristic of variant C.

## PDF

See `slf/A-single-level/README.md` — no stable URL for archived SLF PDFs is
discoverable from the public API.  `source.pdf` is absent from this directory.
