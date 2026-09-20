---
name: the-slf-caaml-shape-is-detected-not-assumed-from-the-url
description: detect_caaml_shape, customData.CH.weather, SLF_API_LEGACY_URL — why the SLF CAAML shape is read off each response, not the URL
status: current
last-reviewed: 2026-09-20
---

# The SLF CAAML shape is detected, not assumed from the URL

**SNOW-900.** Every page `fetch_bulletin_page` returns is classified by
`detect_caaml_shape` and logged — at WARNING when the 2026/27 per-region
export is what arrived. `SLF_API_LEGACY_URL` exists beside
`SLF_API_BASE_URL`, overrides it whenever it is non-empty, and ships empty.

## Why

SLF is changing its CAAML export for the 2026/27 winter: the new version
takes over the existing unversioned URL and the current one moves to a
legacy path. The obvious design — "we fetch the legacy URL, therefore we
have the legacy format" — is wrong, and SLF said so on 2026-09-10.

The legacy endpoint preserves the response **format**. It does not
guarantee the previous **aggregation** into five or six bulletins. Those
are two different properties of a payload and only one of them travels
with the URL. So the ~150-entries-per-issue volume can arrive at either
endpoint, and where we fetched from tells us nothing about which shape we
are holding.

What makes this worth a guard rather than a wait-and-see is that the
changeover does not fail. Measured against SLF's 133-bulletin sample, every
stage already handles it: `normalise_bulletin_response` parses it,
`build_render_model` succeeds on all 133 with zero errors, and a full
`upsert_bulletin` pass writes 133 Bulletins, 133 RegionBulletins, 133
BulletinGroupings and 133 RegionDayRatings in 0.4s. A silent success at 25×
the row count and 25× the stored JSON is the failure mode — one that would
be noticed weeks later from a Postgres bill rather than on the day it
happened.

## How the shape is decided

Three signals, in descending order of confidence, over one page:

1. **A bulletin carrying more than one region is proof of aggregation** and
   outranks everything. The three committed SLF sentinels carry 93, 65 and
   13 regions; the new export carries exactly one per bulletin.
2. **A new-format marker field** — `customData.CH.weather` (the structured
   per-region weather block, 80% of the sample's payload by size) or
   `dangerRatingEvolution` (specified, not yet populated). Either settles it
   at any page size, which is what makes a short page classifiable.
3. **Page size**, at ten or more single-region bulletins.

Anything else is `unknown`, reported as such. A short page of single-region
bulletins genuinely could be either — a legacy page *can* hold a narrow
bulletin — and defaulting it to today's shape would reintroduce exactly the
silence this exists to break.

Aggregation outranking a marker is deliberate: if SLF ever ship both, the
region count is what multiplies the rows, so the volume is what gets
reported. The markers are carried on `CaamlPayloadShape` separately, so the
log line says both things happened.

## Why `SLF_API_LEGACY_URL` ships empty

SLF named `/api/bulletin/caaml/v3/{lang}/{type}` for the **single-bulletin**
endpoint. This pipeline reads the **paginated list** endpoint,
`bulletin-list/caaml/{lang}/json`, and whether that gets the same v3/v5
split is a question SLF have not answered. A guessed default would be worse
than a blank one, because a URL sitting in `base.py` reads as confirmed.

Empty, it is an environment variable: set it on the dyno, restart, and every
SLF fetch reads it instead — no deploy, no cron edit, on the day SLF flip
the endpoint before we are ready.

**The pin is applied in `slf_fetcher._resolve_base_url`, not in the
`fetch_bulletins` command.** It shipped in the command first, and that was
wrong: `BulletinAdmin.backfill_view` is the other caller of
`run_slf_pipeline` and passes no `base_url`, so an admin backfill during the
changeover would have fetched the live URL and ingested the new schema
straight past a configured rollback. A pin that one entry point ignores is
worse than no pin, because it reads as applied. In the resolver it holds for
every caller that exists now and every one added later.

`--local-mirror` still outranks it, because it passes an explicit `base_url`
and an explicit argument wins — so a pin left in a dev `.env` cannot silently
send mirror runs at the live API.

## Consequences

- **The pin defers the schema change only.** It does not solve the volume
  problem, because the legacy path does not promise the old aggregation.
  Anyone reaching for it should read the WARNING line afterwards to see
  whether it changed what arrived — which is why the log names the shape and
  the base URL together.
- **The WARNING repeats, every page, every fetch.** That is intended. It
  stays noisy until SNOW-998 handles the de-aggregated shape properly;
  silencing it before then would restore the original failure.
- **Detection never blocks a fetch.** It only describes the page, so
  `_region_count` is total rather than strict and a malformed entry is left
  for the real ingest to reject with an error that names the bulletin.
- **`_PER_REGION_MIN_BULLETINS` is calibrated, not principled.** It sits
  between 13 (the narrowest committed sentinel) and 1 (the new export), with
  no payload anywhere near it. If SLF ever publish genuinely narrow legacy
  pages, the markers are the signal that still holds.

## Not decided here

What to *do* about the new shape: SNOW-998 (de-aggregated ingest),
SNOW-999 (the tendency bulletin, and whether the map shows a rating for a
day that has not happened), SNOW-1000 (per-region weather and
`dangerRatingEvolution`) and SNOW-1001 (whether `BulletinGrouping`
survives). This ticket makes the changeover audible; it does not absorb it.
