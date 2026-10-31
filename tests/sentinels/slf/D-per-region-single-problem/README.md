# SLF sentinel D — per-region, single problem

| Field | Value |
|---|---|
| **bulletinID** | `51e348b5-6966-4baa-8d85-07c710d99d62` |
| **Date** | 2026-09-10 (validTime 2026-09-10T09:00:00Z – 2026-09-10T15:00:00Z; publicationTime 2026-09-10T09:00:00Z) |
| **Region** | `CH-1122` Gruyère — one micro-region only |
| **Source** | `slf-v5-bulletins.json` on branch `samples/slf-2026-27-preview`, commit `44695ba0` (SLF's 2026/27 preview sample, 133 bulletins) |

## Why this is variant D

The first of the two **per-region** sentinels (SNOW-998). From the 2026/27
winter SLF publish one bulletin per micro-region instead of one per
aggregated danger area, so `regions` carries a single entry and the
`bulletinID` belongs to that region alone.

- One `dangerRating` (`mainValue: moderate`, `validTimePeriod: all_day`,
  `customData.CH.subdivision: neutral`).
- One avalanche problem (`wind_slab`, above 1400 m, six aspects) in one
  `aggregation` entry.
- `customData.CH.weather` — the new export's structured weather series,
  three periods of wind, new snow, snowfall limit and temperatures. It is
  about 80% of the bulletin's bytes, it is the field `detect_caaml_shape`
  reads as the new-export marker, and it is excluded from the fidelity
  table until SNOW-1000 ingests it.

The moderate level is the minority in the sample (11 of 133), which is
why it is the single-problem case: `/examples/moderate/` has to find it
among a far larger number of newer considerable bulletins.

## Extraction

Copied verbatim — the one array entry whose `regions[0].regionID` is
`CH-1122` — and written with `json.dump(indent=2, ensure_ascii=False)`.
The sample is never committed whole (SNOW-905 tried and was abandoned).

## PDF

See [`../A-single-level/README.md`](../A-single-level/README.md) — no stable
URL for archived SLF PDFs is discoverable from the public API. `source.pdf` is
absent from this directory.
