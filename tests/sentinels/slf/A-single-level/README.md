# SLF sentinel A — single-level, single-problem

| Field | Value |
|---|---|
| **bulletinID** | `e292ca8f-17b5-4a11-92bc-dd406d2eed2a` |
| **Date** | 2025-10-31 (validTime 2025-10-31T16:00:00Z – 2025-11-01T16:00:00Z) |
| **Region** | All CH micro-regions (full-country bulletin) |
| **Source** | `bulletins/local_mirrors/slf_archive.ndjson` |

## Why this is variant A

One `dangerRating` entry (`mainValue: moderate`, `validTimePeriod: all_day`) with
`customData.CH.subdivision: minus` — no elevation-band split and no time split.
One avalanche problem type (`persistent_weak_layers`) in the `aggregation`.

## PDF

The SLF bulletin PDF is served by `https://aws.slf.ch/api/bulletin/document/full/en`
but the `?date=` query parameter is not honoured for archive dates — the endpoint
always returns the current day's bulletin.  No stable URL for archived SLF PDFs was
discoverable from the public-facing API or the SLF website at time of writing.

`source.pdf` is absent from this sentinel directory.  Spot-check the bulletin content
by loading `source.json` in a browser or inspecting `dangerRatings` directly.
