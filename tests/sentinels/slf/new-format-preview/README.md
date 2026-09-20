# SLF 2026/27 CAAML preview material

**This directory holds no sentinel.** Every sentinel collector globs
`*/*/source.json` (`tests/sentinels/fidelity.py`,
`tests/sentinels/test_fidelity.py`, `tests/sentinels/test_sentinel_round_trip.py`),
and nothing here is called that, so nothing here is collected, rendered or
fidelity-classified. It is reference material for the changeover tickets —
SNOW-998, SNOW-999, SNOW-1000, SNOW-1001 — kept beside the sentinels it will
eventually become, and not one line of it is read by any test today.

## What is here

### `dangerRatingEvolution.schema.json`

From SLF, 2026-09-10, via SNOW-900. A JSON Schema fragment, not data: an
array of `{date, dangerRating}` with only `date` required and `dangerRating`
a `$ref` to the standard definition. The field is still under development at
SLF and appears in none of the sample payloads, so this is enough to model
against and not enough to test against.

It matters because it may make the provider the source of per-region rating
history — which today is `RegionDayRating`, derived by us in
`apps/bulletins/services/day_rating.py`. Whether the provider's series
replaces ours or corroborates it is SNOW-1000's question, not this file's
answer.

## What is missing, and why

Two of the three artefacts SLF sent on 2026-09-10 are **not here**:

- **the 133-bulletin JSON sample** — one bulletin per micro-region, the shape
  the whole changeover is about;
- **the GeoJSON variant** — the same properties plus a `fill` colour, with a
  five-point ring that is a bounding box rather than a region outline (so it
  is *not* a source of region geometry, and cannot replace the fixture-based
  dissolve in `compute_bulletin_grouping_boundary`).

Both are attached to SNOW-900 in Linear. They are ~8.9 MB and ~242,000 lines
of vendor JSON between them, and committing them wholesale was tried once and
rejected: SNOW-905 did exactly that and was closed with its PR abandoned. So
when they land here they land as **extracted sentinels** in the usual form —
one graded bulletin per directory with its own `source.json` and README —
rather than as the whole list.

Everything measured from those two files is written up in SNOW-900's comments,
so the findings survive whatever happens to the files themselves.
