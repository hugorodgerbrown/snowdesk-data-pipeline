# SLF 2026/27 CAAML preview material

**This directory holds no sentinel.** Every sentinel collector globs
`*/*/source.json` (`tests/sentinels/fidelity.py`,
`tests/sentinels/test_fidelity.py`, `tests/sentinels/test_sentinel_round_trip.py`),
and nothing here is called that, so nothing here is collected, rendered or
fidelity-classified. It is reference material for the changeover tickets —
SNOW-998, SNOW-999, SNOW-1000, SNOW-1001 — kept beside the sentinels, and not one
line of it is read by any test.

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

## Where the sample went (SNOW-998)

Two of the three artefacts SLF sent on 2026-09-10 are not in this
directory, and that gap is now closed — not by committing them, but by
extracting from them:

- **the 133-bulletin JSON sample** — one bulletin per micro-region, the
  shape the whole changeover is about — became sentinels
  [`../D-per-region-single-problem/`](../D-per-region-single-problem/README.md)
  and [`../E-per-region-two-problems/`](../E-per-region-two-problems/README.md),
  plus the twelve-bulletin [`../per-region-slice.ndjson`](../per-region-slice.ndjson)
  the end-to-end ingest test replays;
- **the GeoJSON variant** — the same properties plus a `fill` colour, with a
  five-point ring that is a bounding box rather than a region outline (so it
  is *not* a source of region geometry, and cannot replace the fixture-based
  dissolve in `compute_bulletin_grouping_boundary`) — is used nowhere.

Both files live on the `samples/slf-2026-27-preview` branch (commit
`44695ba0`), which is read from and never merged. They are ~8.9 MB and
~242,000 lines of vendor JSON between them, and committing them wholesale
was tried once and rejected: SNOW-905 did exactly that and was closed with
its PR abandoned.

Everything measured from those two files is written up in SNOW-900's comments,
so the findings survive whatever happens to the files themselves.
