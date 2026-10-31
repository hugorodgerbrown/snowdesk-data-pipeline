---
name: reference-data-cut-over
description: Deploy the reference-data stack — schedule_backfill_weather pause, mirror, copy_weather_to_service, rebuild_source_render_models, v2 tokens
status: current
last-reviewed: 2026-10-28
---

# Runbook — cutting over to the reference-data service

The stack that makes `apps/ingest` the one source of reference data
(SNOW-1248 to SNOW-1275,
[`decisions/reference-data-has-one-source-of-truth.md`](../decisions/reference-data-has-one-source-of-truth.md))
deploys like any release, but its migrations create the service's tables
empty or half-filled. A few one-off commands fill them, in an order that
matters. This runbook is that order.

Nothing here deletes or rewrites a row an instance already serves. Every
command is read-only without `--commit`: **run each one bare first** and
read its counts before committing.

## 0. Merge the stack

The PRs are stacked, each on the one before. Merge from the bottom
(#1249) up. When a PR merges and its branch is deleted, GitHub retargets
the next PR onto `main`; let CI go green on each before merging it.
`main` deploys to staging on every merge, so staging carries the stack
first.

## 1. Before production deploys: pause the backfill sweep

SNOW-1275 moves `backfill_weather` into the service: the sweep diffs
against `SourceWeather`, which is empty until step 3 copies production's
history into it. A sweep before that would spend the hour's Open-Meteo
budget re-fetching a season production already holds.

In production's admin, switch **`schedule_backfill_weather` off**
(Waffle → Switches). The scheduler reads it at each firing; the jobs on
either side carry on.

## 2. Deploy production

Ship the release as usual ([`deployment.md`](../deployment.md)). The
migrations run on deploy, including:

- `bulletins.0022`, which copies every `Bulletin` into `SourceBulletin`
  with its own timestamps (SNOW-1250);
- `ingest.0004` and `ingest.0005`, which add the render model, the target
  day and region index, and `SourceDayRating` (SNOW-1270), all empty on the
  copied rows.

`fetch_bulletins` and `fetch_weather` keep running throughout. Each new
bulletin is stored with its model, index and day ratings; each fetch
mirrors the curated estate into `SourceLocation` before it fetches.

## 3. Fill the service (production shell)

In this order:

```bash
# a. The estate: places, passes, resorts into the service's tables.
#    Also runs hourly at :40; running it now is what step b needs.
uv run python manage.py mirror_reference_data            # preview
uv run python manage.py mirror_reference_data --commit

# b. The weather history: every past Weather day of a reference place,
#    into SourceWeather. Re-run after (a) if it reports any `unstationed`.
uv run python manage.py copy_weather_to_service           # preview
uv run python manage.py copy_weather_to_service --commit

# c. Every stored bulletin's render model, index and day ratings.
#    Enqueues one task per bulletin (SNOW-1346) and returns; the db_worker
#    works through the archive in the background, at low priority.
uv run python manage.py rebuild_source_render_models           # preview
uv run python manage.py rebuild_source_render_models --commit
```

The worker rebuilds in the background: re-run the preview until it reports
nothing to enqueue, and read the worker log for a failed task. A failed
build is stored as the error sentinel at version 0 and stays stale, so the
next run enqueues it again; investigate and re-run rather than proceeding
with failures outstanding.

Then switch **`schedule_backfill_weather` back on**.

## 4. Check it

- **Ops MCP** (`/api/ops/mcp/`): `weather_freshness` and
  `location_estate_health` should read as they did before the deploy;
  `ingest_status` should show the morning's fetches.
- **The register**: `command_history` for the three step-3 commands, each
  `SUCCEEDED` with its tallies.
- **The feeds** answer under v2 for a token bound to `/api/data/v2/`:
  `locations`, `passes`, `resorts`, `bulletins`, `day-ratings`, `weather`.
- **The site**: today's map, a region sheet, a resort sheet and a weather
  sheet look as they did — production's own `Bulletin` and
  `RegionDayRating` rows are now copied from the service, and should be
  identical.

## 5. Credentials for v2

A token is bound to its URL prefix (RFC 8707), so v1 tokens do not open v2.

- **Staging** needs nothing new: with its client-credentials pair, the
  client asks for a token bound to `/api/data/v2/` itself.
- **A cloud environment** needs a token minted for v2, set as
  `DATA_FEED_V2_TOKEN` beside its existing `DATA_FEED_URL`:

```bash
uv run python manage.py mint_feed_token --client "Cloud dev feed" \
    --resource https://snowdesk.info/api/data/v2/ --commit
```

## 6. Move staging onto `pull_reference_data`

On staging's shell, preview, then commit:

```bash
uv run python manage.py pull_reference_data            # preview
uv run python manage.py pull_reference_data --commit
```

Staging's rows came from v1 pulls and already carry production's uuids and
slugs, so the first run adopts them: expect `unchanged` and `updated`,
very few `created`, and any `detached` or `deleted` only for what
production has removed since. A second `--commit` should write nothing.

Then the `snowdesk-staging-data-pull` cron in `render.yaml` runs
`pull_reference_data --commit` instead of `pull_production_data --commit`
(SNOW-1281). Render picks the new start command up from the blueprint on
the deploy that carries it; nothing is set by hand.

## Rolling back

v1 is untouched by the stack: `/api/data/v1/` and `pull_production_data`
serve and pull exactly as before, so staging can stay on v1 indefinitely.
The service's tables are additive; reverting the release leaves them in
place, unused. The one switch this runbook touches,
`schedule_backfill_weather`, is restored at the end of step 3.
