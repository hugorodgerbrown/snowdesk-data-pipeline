# SLF sentinel E — per-region, two problems

| Field | Value |
|---|---|
| **bulletinID** | `21d2b7ce-568d-4975-bafa-ee1a0fbf69bd` |
| **Date** | 2026-09-10 (validTime 2026-09-10T09:00:00Z – 2026-09-10T15:00:00Z; publicationTime 2026-09-10T09:00:00Z) |
| **Region** | `CH-4222` Zermatt — one micro-region only |
| **Source** | `slf-v5-bulletins.json` on branch `samples/slf-2026-27-preview`, commit `44695ba0` (SLF's 2026/27 preview sample, 133 bulletins) |

## Why this is variant E

The second **per-region** sentinel (SNOW-998), the busier shape of the new
export:

- One `dangerRating` (`mainValue: considerable`, `validTimePeriod: all_day`,
  `customData.CH.subdivision: minus`).
- Two avalanche problems (`wind_slab` and `persistent_weak_layers`, both
  above 2200 m, seven aspects) in one `aggregation` entry.
- `customData.CH.weather`, as in [D](../D-per-region-single-problem/README.md),
  and here with `wind.highlight` set, which D's series does not carry.

Considerable is what 122 of the sample's 133 bulletins say, and two
problems is the commonest problem count, so E is what most of a 2026/27
issue looks like.

## Extraction

Copied verbatim — the one array entry whose `regions[0].regionID` is
`CH-4222` — and written with `json.dump(indent=2, ensure_ascii=False)`.

## PDF

See [`../A-single-level/README.md`](../A-single-level/README.md) — no stable
URL for archived SLF PDFs is discoverable from the public API. `source.pdf` is
absent from this directory.
