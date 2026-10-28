---
name: data-feed
description: Data feed GET /api/data/v1/, service clients, pull_production_data, DATA_FEED_URL, DATA_FEED_TOKEN, mint_feed_token (cloud dev)
status: current
last-reviewed: 2026-10-27
---

# The production data feed

Production ingests bulletins from three providers and weather from
Open-Meteo on a schedule, and holds the curated resort estate edited in its
admin and map editor. Staging has no scheduler, so it never gets any of
that on its own. The data feed (SNOW-1197) makes production's data
available to other services over HTTPS. Staging is the first client, and
the same feed serves any later one.

Why a feed and not a database copy:
[`decisions/data-reaches-other-environments-through-a-feed.md`](decisions/data-reaches-other-environments-through-a-feed.md).

## The two halves

| Half | Where | What |
|------|-------|------|
| Server | `apps/data_feed/feeds.py`, `views.py`, `auth.py`; `apps/ingest/api/` | `GET /api/data/v1/<feed>/`, read-only, service clients only. The bulletins feed and the paging rule are ingest's (SNOW-1250); the host mounts them |
| Client | `apps/data_feed/client.py`, `services/apply.py`, `pull_production_data` | Gets a token, walks a feed, applies each item idempotently |
| Contract | `apps/data_feed/contract.py` | The field allowlists both halves read and write |

## Endpoints

All four are `GET`, take the same parameters and answer the same shape.

| URL | Item key | What is served |
|-----|----------|----------------|
| `/api/data/v1/bulletins/` | `bulletin_id` | Every bulletin ingest has stored (`SourceBulletin`): its CAAML as normalised, plus `pdf_url` |
| `/api/data/v1/weather/` | location `uuid` + `observed_on` | `Weather` rows for `Location.objects.public()` only |
| `/api/data/v1/locations/` | `uuid` | `Location.objects.public()`, with `centroid_of` region codes |
| `/api/data/v1/resorts/` | `slug` | Every resort with a slug: region code, pin uuid, passes, location links |

Parameters, all optional: `since` (ISO 8601 datetime **with an offset**,
filters on `updated_at`), `cursor` (the previous page's `next_cursor`) and
`limit` (default 100, at most 500).

```json
{
  "feed": "bulletins",
  "version": 1,
  "count": 412,
  "results": [{"bulletin_id": "…", "updated_at": "…", "pdf_url": "…", "caaml": {…}}],
  "next_cursor": "MjAyNi0xMC0yM1QwNjo…"
}
```

**Where the bulletins come from** (SNOW-1250). The bulletins feed reads
ingest's `SourceBulletin`, which `fetch_bulletins` writes before it applies
anything to `Bulletin`, and which changes only when a bulletin's CAAML hash
does. Production's own `Bulletin` rows are applied from the same wire item
the feed sends, so staging and production derive their bulletins from
identical input. Migration `bulletins.0022` copied every existing
`Bulletin` across, with its own timestamps, so a resumed pull saw nothing
new on the release.

`count` is on the first page only. Paging is a keyset on
`(updated_at, id)` ascending, so a row updated mid-walk moves to the end
and is still reached. Every response is `Cache-Control: no-store`. A bad
parameter is a `400` with an `error`.

**Cost of a page.** A page is a fixed number of queries, whatever its
size: one for bulletins and weather, two for locations and five for resorts
(their relations are prefetched). `tests/data_feed/test_feeds.py` and
`tests/ingest/api/test_bulletins_feed.py` fail if a page of 12 rows costs
more queries than a page of 2. Bulletins and weather have an
`(updated_at, id)` index (`ingest_srcb_keyset` on `SourceBulletin`,
`weather_feed_keyset`), so each page reads the index from the cursor
rather than scanning and sorting the table. `count` costs one extra query,
on the first page only. Locations and resorts have no such index because
they are a few thousand rows, read whole. On the client,
`FeedClient.pages` is a generator and the command applies items as they
arrive, so a pull holds only one page (at most 500 items) in memory.

**What is never served.** The four querysets are the only reads, and no
user-owned table is among them. A favourite's pin is fetched for weather
(`Location.objects.active()`), but it is one person's saved place, so its
location and its weather never leave production. The column lists in
`contract.py` are allowlists too: a new column is not published until
someone adds it, and `tests/data_feed/test_contract.py` fails until they
decide.

## Authentication: OAuth 2 client credentials

The feed uses the same OAuth server as the MCP endpoints
([`oauth.md`](oauth.md)), with the client-credentials grant. A request is
admitted only when its bearer token:

1. carries the `data` scope;
2. is bound (RFC 8707 `resource`) to a URL prefix of the path called.
   django-oauth-toolkit checks the prefix, and an unbound token is refused
   because the toolkit would otherwise treat it as good everywhere;
3. belongs to a **service client**. That means a confidential
   client-credentials application created by hand in the admin
   (`registration_source = manual`), as defined by
   `apps.oauth.validators.is_service_client`.

Rule 3 is also enforced where tokens are issued, by mcp-auth (v0.1.8):
registration refuses any grant but the authorization code, and the token
endpoint refuses client credentials to any client not made by hand. That
matters because registration is open. Before SNOW-1197 a client that
registered for `client_credentials` could get a userless token from
`/oauth/token/`.

A person's MCP token is never a service client's, even with the `data`
scope. A service client's token is never an MCP reader's, because the
connect rule refuses a token with no user (mcp-auth's `can_connect`).

## Setting up a client (staging)

**1. On production, create the service client.** In Django admin go to
**Django OAuth Toolkit → Applications → Add**:

| Field | Value |
|-------|-------|
| Name | `Staging data feed` |
| Client type | Confidential |
| Authorization grant type | Client credentials |
| Redirect URIs | *(empty)* |
| Hash client secret | ticked |
| User | *(empty — a service client acts as no one)* |

Copy the **client secret before saving**. It is hashed on save and cannot
be read back. If it is lost, set a new one on the same application.

**2. On staging, set three env vars.** Render → snowdesk-staging →
Environment (they are declared `sync: false` in `render.yaml`):

```
DATA_FEED_URL=https://<production host>/api/data/v1/
DATA_FEED_CLIENT_ID=<client id from step 1>
DATA_FEED_CLIENT_SECRET=<client secret from step 1>
```

**3. Dry run from the staging shell.** This reads the feed and writes
nothing:

```bash
uv run python manage.py pull_production_data
```

**Revoking a client** is deleting its application on production. Its
tokens go with it, so the next request is a 401.

## Setting up a cloud dev environment (SNOW-1254)

A Claude Code on the web session cannot hold a client secret in a form it
can exchange safely, so it is given a **minted token** instead: an ordinary
access token issued to a service client with the `data` scope, bound to the
feed's URL, carrying no user and expiring after a set number of days.
`require_feed_token` admits it unchanged.

**1. On production, create a separate service client** named
`Cloud dev feed`, exactly as in step 1 above. It must not be staging's:
revoking the cloud token should never touch staging's credentials.

**2. Mint the token** from production's shell. It is printed once and
stored only as a hash:

```bash
uv run python manage.py mint_feed_token --client "Cloud dev feed"           # preview
uv run python manage.py mint_feed_token --client "Cloud dev feed" --commit  # 90 days
uv run python manage.py mint_feed_token --client "Cloud dev feed" --days 30 --commit
```

**3. On the cloud environment**, set two environment variables and open
production's host (`snowdesk.info`) in its network policy
([allowlist](environment-network-allowlist.md)):

```
DATA_FEED_URL=https://snowdesk.info/api/data/v1/
DATA_FEED_TOKEN=<the minted token>
```

**4. In a session**, pulling is an explicit step. Seeding stays
fixtures-first and nothing pulls at session start:

```bash
uv run python manage.py pull_production_data            # preview
uv run python manage.py pull_production_data --commit   # apply
```

When `DATA_FEED_TOKEN` is set, `pull_production_data` sends it as it is and
needs neither `DATA_FEED_CLIENT_ID` nor `DATA_FEED_CLIENT_SECRET`. A minted
token cannot be renewed, so a 401 stops the pull with "expired or been
revoked": mint a new one. **Revoking** is deleting the token (Django OAuth
Toolkit → Access tokens) or the `Cloud dev feed` application on production.
`purge_expired_oauth_tokens` clears expired tokens daily.

What the token reaches is what the feed serves: bulletins, public-location
weather, curated locations and resorts. No user-owned table is served, so
none is reachable. Why a cloud session may hold this token when it holds no
provider key: [`decisions/cloud-sessions-hold-no-provider-credentials.md`](decisions/cloud-sessions-hold-no-provider-credentials.md).

## `pull_production_data`

```bash
uv run python manage.py pull_production_data                      # preview everything
uv run python manage.py pull_production_data --commit             # apply everything
uv run python manage.py pull_production_data --type resorts --commit
uv run python manage.py pull_production_data --type bulletins --since 2026-10-01 --commit
```

- **`--type resorts|bulletins|weather`** can be repeated; the default is all
  three. `resorts` pulls the curated locations first, then the resorts.
  Weather hangs off locations, so with no `--type` the order is resorts,
  bulletins, weather.
- **`--since`** takes a date (midnight UTC) or an ISO datetime with an
  offset, and applies to bulletins and weather. Without it, each type
  resumes from the start of the last successful `--commit` run that pulled
  it, less an hour of overlap (read from the `CommandRun` register). The
  first pull of a type goes back seven days.
- **Locations and resorts are always pulled whole.** There are a few
  thousand rows, and a pass or a location link can change without the
  resort's `updated_at` moving.

Every item is matched on its natural key and compared with the row already
there. Identical rows are tallied `unchanged` and not written, so
re-running is free. Each feed is written through the code that owns the
table:

- **Bulletins** are replayed through `upsert_bulletin`, so region links,
  day ratings, grouping outlines and render models are rebuilt by
  *staging's* code. Staging therefore tests its own code against real
  input rather than showing production's output.
- **Weather** goes through `upsert_weather`. A past day already recorded is
  `kept` and never rewritten.
- **Locations and resorts** are written field by field. A resort's pin is
  copied as the location production points at; it is not re-placed
  through `set_resort_pin`.

Tallies are recorded per feed and outcome (`bulletins_created`,
`weather_kept`, …). A failed item is logged with its traceback, counted, and
does not stop the pull. Any failure exits non-zero, and the run is
recorded `PARTIAL`. A feed that cannot be read at all exits immediately.

The command refuses to run when a setting is missing, when the URL is plain
HTTP to a host other than localhost or 127.0.0.1, or when the URL points at
its own `SITE_BASE_URL`.

## Running daily (SNOW-1200)

Staging has no scheduler, so the pull runs as its own Render cron job,
`snowdesk-staging-data-pull` in [`render.yaml`](../render.yaml):
`pull_production_data --commit` at 07:20 UTC, after production's 06:00
`fetch_weather` and the morning bulletin issue. Each type resumes from its
last successful `--commit` run, so a missed day is caught up by the next.
The job reads the same three `DATA_FEED_*` variables as the web service,
set on the job in the dashboard. Any failed item makes it exit non-zero,
which Render shows as a failed run; the item and its error are in the log,
and the run's counts are in the `CommandRun` register.

A first dry run on an empty client reports what a commit would do: a
location it would create counts as present for the weather and resorts
after it (SNOW-1198). Each feed page's lookups are read in one query per
kind of row, not one per item (SNOW-1199).

## What it does not do

- **Delete.** A resort, location or bulletin removed on production stays on
  the client. `bin/sync-staging-data`
  ([runbook](runbooks/refresh-staging-from-production.md)) is still the way
  to reset staging wholesale.
- **Copy anything derived.** Region links, day ratings and groupings are
  rebuilt on the client, never sent.
