# ALBINA sentinel A — single-level, single-problem

| Field | Value |
|---|---|
| **bulletinID** | `4441f095-5890-4047-8dd7-5e9c223964aa` |
| **Date** | 2025-11-28/29 (validTime startTime 2025-11-28T16:00:00Z, endTime 2025-11-29T16:00:00Z) |
| **Region** | AT-07 (Northern Tyrol), covering 12 sub-regions |
| **Source** | `apps/bulletins/local_mirrors/albina_archive.ndjson` |

## Why this is variant A

One `dangerRating` entry (`mainValue: low`, `validTimePeriod: all_day`) with no
elevation split and no time split.  One avalanche problem type.

## PDF

The ALBINA static CDN (`static.avalanche.report/bulletins/YYYY-MM-DD/`) contains
JSON and XML files but does not host PDF renderings.  The avalanche.report website
renders bulletins as PDFs client-side only; no stable archive PDF URL exists.

`source.pdf` is absent from this sentinel directory.  Spot-check via
`https://avalanche.report/bulletin?date=2025-11-29&region=AT-07&lang=en`.
The URL uses `2025-11-29` (the `customData.ALBINA.mainDate`), which is the display
date; the bulletin's `validTime.startTime` is the previous afternoon (2025-11-28T16:00Z).
