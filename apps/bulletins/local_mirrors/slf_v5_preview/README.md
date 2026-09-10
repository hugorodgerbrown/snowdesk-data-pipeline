# SLF v5 CAAML preview payloads

Sample payloads for the CAAML interface SLF will serve from the 2026/27
winter, sent by SLF on 2026-09-10 in answer to questions raised against
SNOW-900. They are the reference for every piece of that work.

| File | What it is |
|---|---|
| `bulletins.json` | The JSON response: `{bulletins: [...], customData: {...}}`, 133 bulletins. |
| `bulletins.geojson.json` | The same content in the GeoJSON variant: a `FeatureCollection`, one feature per bulletin. |
| `dangerRatingEvolution.schema.json` | A **schema fragment** for the forthcoming per-region danger history — not data. |

Endpoints, per SLF: the legacy (current) interface stays at
`/api/bulletin/caaml/v3/{lang}/{type}` where `type` is `json` or `geojson`;
the unversioned endpoint moves to v5. The legacy endpoint preserves the
response *format* but not necessarily the current aggregation into five or
six bulletins.

## What these payloads show

Every bulletin covers exactly **one** warning region — the de-aggregation
SLF announced. The 133 region IDs all resolve against our EAWS fixtures
(of 149 CH micro-regions, so this is partial coverage, not a full issue).

The structured weather lives at `customData.CH.weather[]`, namespaced
alongside `aggregation`: three daily periods, each carrying `wind`,
`newSnow` by elevation, and 24 hourly `temperatures` and `snowfallLimit`
readings. `weatherReview.comment` carries the same figures as HTML, so it
is additive rather than a replacement.

That array is 13.3 KB of the 16.6 KB each bulletin occupies — 80% of the
payload, and the dominant term in the ~1 GB a season would cost at two
issues a day.

## Caveats — read before building anything on these

The payload is **generated, not a real issue**. Every bulletin is stamped
`publicationTime` 2026-09-10T09:00:00Z with a six-hour `validTime` window
and `unscheduled: true`, in September, out of season.

Three things it does not exercise:

- **No `weatherForecast`.** Only `weatherReview` is present, on all 133.
  Whether the forecast field is gone or merely absent from the sample is
  an open question with SLF; `weatherForecast.comment` is a *rendered*
  path in `tests/sentinels/fidelity.py`.
- **No next-day bulletin.** Nothing has `validTime.startTime` later than
  `publicationTime`, and no bulletin carries `tendency`. SLF's advice is
  that the reduced next-day bulletin carries no explicit marker and is
  identified by exactly that comparison.
- **The GeoJSON geometry is a bounding box** — each ring is five points,
  not a region outline — so it cannot replace the fixture dissolve in
  `compute_bulletin_grouping_boundary`. Its `fill` property is the danger
  colour.

## Why here and not in `tests/sentinels/`

`tests/sentinels/` discovers cases by globbing `*/*/source.json`, and each
is one graded bulletin with a README and a round-trip test. These are a
133-bulletin list, and their shapes are not yet settled. Sentinels get
written from them once SNOW-900 answers what the new fields mean.

Unlike the `*_archive.ndjson` files beside this directory, nothing loads
these — no dev-mirror view, no `seed_test_week`. They are reference data
for humans and for one-off measurement scripts.
