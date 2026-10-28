---
name: ingestion-is-a-rehostable-unit
description: Why apps/ingest imports no other apps.* package and callers use apps.ingest.public, enforced by tests/ingest/test_boundary.py
status: current
last-reviewed: 2026-10-28
---

# Ingestion is a rehostable unit

**Decision.** Fetching the providers' data, normalising it and serving it is
one package, `apps/ingest`, that imports nothing from any other `apps.*`
package (SNOW-1247). The rest of Snowdesk reaches it only through
`apps/ingest/public.py`. `tests/ingest/test_boundary.py` enforces both rules
by parsing source with `ast`, and fails the build on a breach. The extraction
was staged across five tickets, then widened on 28 Oct 2026 to everything a
signed-out visitor sees on the map
([`reference-data-has-one-source-of-truth.md`](reference-data-has-one-source-of-truth.md),
which holds the current ticket list):

1. SNOW-1248 moves the pure normalisation code.
2. SNOW-1249 moves the HTTP sources and splits fetch from apply.
3. SNOW-1250 adds ingest's own `SourceBulletin` table and serves the bulletin
   feed from it.
4. SNOW-1253, re-scoped, owns the weather stations (`SourceLocation`) and
   `SourceWeather`, and serves them at `/api/data/v2/weather/`.
5. SNOW-1271 adds the passes and resorts (`SourcePass`, `SourceResort`),
   held as documents ingest stores and never reads, and serves them with
   the places at `/api/data/v2/{locations,passes,resorts}/`.
6. SNOW-1275 moves the weather backfill sweep into ingest
   (`apps/ingest/backfill.py`), so the service holds the history it serves;
   the app applies each row and copied its existing history in once.
7. SNOW-1270 moves the render model builder, its prose parser, the period
   transition and the day-character cascade into `apps/ingest/normalise/`;
   the instance keeps the labels and copy.
8. SNOW-1252 (the `lookup_point` facade) is under review.

**Why.**

- **The sync is one-way, and should stay that way.** Production is the only
  platform that calls the providers. Staging, and cloud dev environments
  since SNOW-1254, pull from production's feed. Ingestion could one day run
  as its own service that both pull from. Today it does not, and nothing in
  this decision moves it. What it buys is that the move would be a copy, not
  a rewrite.
- **Own tables, not a view over Snowdesk's.** Ingest stores what the
  providers sent, keyed by what they sent, so the feed can be served from
  ingest alone. The project's `Bulletin` and `Weather` rows stay derived from
  ingest's, through the same `upsert_*` functions every environment already
  replays through. This was chosen on 27 Oct 2026, over a facade that reads
  Snowdesk's tables, because such a facade would carry the whole schema with
  it on any rehost.
- **A boundary nobody checks does not hold.** The same lesson as `ds-lint`
  and the e2e cap. An `ast` walk catches an import inside a function or
  behind `TYPE_CHECKING`, which a grep for `^from apps.` would miss.
- **One facade, so the contract is one file.** What `public.py` lists is
  what a rehosted service must keep. Everything else may move freely.

**Consequences.**

- `apps/ingest` may import Django and third-party packages, never
  `apps.core`. Its models (SNOW-1250 on) declare their own id, uuid and
  timestamps rather than inheriting `BaseModel`. They keep the rest of the
  model kit: admin, `to_string`, ordering, queryset and factory.
- Data the code needs travels with it: the Météo-France massif catalogue now
  lives at `apps/ingest/normalise/meteofrance/massifs.json`, not under
  `docs/research/`.
- **Ingest fetches; the host decides what to write** (SNOW-1249).
  `fetch_bulletins` walks a provider and yields a `NormalisedBulletin` or a
  `FetchOutcome`. The bulletin pipelines keep the dry-run, "already have it"
  and write decisions and the run's counts (`apply_bulletin`,
  `count_outcome`). The weather services likewise keep the walk over
  `Location` rows and call `fetch_forecast` with a bare coordinate. Ingest
  reads settings only through `apps/ingest/conf.py`, which lists every name
  a rehosted service must set.
- **Production's bulletins are applied from ingest's, one at a time**
  (SNOW-1250). A fetch stores each bulletin in `SourceBulletin`, then
  applies the stored row, serialised as the feed would send it, through
  `apply_feed_item` — the step `pull_production_data` uses. The scoping
  comment described this as a second pass over a local feed transport
  after the walk. It is per bulletin instead, because a second pass from
  the run's start would never revisit a bulletin whose apply failed (an
  unresolvable region): ingest's hash would be unchanged on the next run,
  so it would never be applied, where today it is retried every run. The
  apply step compares against `Bulletin`, so it is retried.
- **A migration that reads a host table lives in the host app.** The copy
  of existing bulletins into `SourceBulletin` is `bulletins.0022`, not an
  ingest migration: ingest's migration graph must not depend on an app a
  rehosted ingest would not carry.
- The management commands stay in the host apps as thin `RegisteredCommand`
  wrappers. Ingest's functions are plain and return counts, because
  `RegisteredCommand` lives in `apps.core`.
- Ingest's views are undecorated. The host mounts them behind its own
  authentication (`require_feed_token`).
- Tests and the offline `scripts/` are exempt from the facade rule. A test
  may exercise a private helper. The scripts run without Django and so
  cannot import a facade that will carry models.
