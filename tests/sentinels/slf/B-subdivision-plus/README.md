# SLF sentinel B — subdivision-plus

| Field | Value |
|---|---|
| **bulletinID** | `aca35256-e290-47a6-9a6c-5ffa2079bd44` |
| **Date** | 2025-11-02 (validTime 2025-11-02T16:00:00Z – 2025-11-03T16:00:00Z) |
| **Region** | All CH micro-regions (full-country bulletin) |
| **Source** | `bulletins/local_mirrors/slf_archive.ndjson` |

## Why this is variant B

One `dangerRating` entry (`mainValue: moderate`, `validTimePeriod: all_day`) with
`customData.CH.subdivision: plus`.  The SLF "subdivision-plus" signal indicates the
danger is at the upper boundary of the `moderate` band (i.e. approaching
`considerable`) — an SLF-specific rendering hint rather than a strict elevation split.
Two avalanche problem types in the `aggregation` (`wind_slab` and `wet_snow`).

## PDF

See `slf/A-single-level/README.md` — no stable URL for archived SLF PDFs is
discoverable from the public API.  `source.pdf` is absent from this directory.
