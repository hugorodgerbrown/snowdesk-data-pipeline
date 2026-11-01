---
name: data-feed
description: Data feed /api/data/v1/ + v2, from/to windows, service clients, pull_reference_data, pull_production_data, DATA_FEED_*, mint_feed_token
status: current
last-reviewed: 2026-11-01
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
| Client | `apps/data_feed/client.py`, `services/apply.py`, `services/reference.py`; `pull_reference_data` (v2), `pull_production_data` (v1) | Gets a token, walks a feed, applies each item idempotently |
| Contract | `apps/data_feed/contract.py` | The field allowlists both halves read and write |

## Endpoints

All ten are `GET`, take the same parameters and answer the same shape.

| URL | Item key | What is served |
|-----|----------|----------------|
| `/api/data/v1/bulletins/` | `bulletin_id` | Every bulletin ingest has stored (`SourceBulletin`): its render model (with its `derived` block) and the identity fields a client files it by — never its CAAML (SNOW-1337) |
| `/api/data/v1/weather/` | location `uuid` + `observed_on` | `Weather` rows for `Location.objects.public()` only |
| `/api/data/v1/locations/` | `uuid` | `Location.objects.public()`, with `centroid_of` region codes |
| `/api/data/v1/resorts/` | `slug` | Every resort with a slug: region code, pin uuid, passes, location links |
| `/api/data/v2/bulletins/` | `bulletin_id` | The v1 bulletins feed served under v2, so one v2 token reads the whole service; windowed by `target_date` (SNOW-1272) |
| `/api/data/v2/weather/` | `SourceLocation.uuid` + `observed_on` | Every `SourceWeather` row the weather service has stored (SNOW-1253), tombstoned places included |
| `/api/data/v2/locations/` | `uuid` (`SourceLocation`) | Every reference place, with `deleted_at`: name, exact coordinate and height under `fields`, the host's `document` (kind, short id, what3words, `centroid_of`) |
| `/api/data/v2/passes/` | `uuid` (`SourcePass`) | Every season pass, with `slug`, `deleted_at` and the `document` `{name, website}` (SNOW-1271) |
| `/api/data/v2/resorts/` | `uuid` (`SourceResort`) | Every resort, with `slug`, `deleted_at` and the `document`: `fields`, `region` code, pin, `passes` and `locations` roles, each named by a source uuid (SNOW-1271) |
| `/api/data/v2/ski-network/` | `uuid` (`SourceSkiSegment`) | Every segment of swisstopo's ski-touring network, with `slug`, `deleted_at` and the `document`: `segment_id`, `discipline`, the undocumented `direction` / `access` / `route_info` codes as published, and the line as a WGS84 `MultiLineString` simplified to 5 m (SNOW-1276) |
| `/api/data/v2/ski-tours/` | `uuid` (`SourceSkiTour`, swisstopo's `route_uuid`) | Every named SAC ski tour, in the same shape: SAC ids, `name` and `names` by language, `grade` and `difficulty`, `ascent_m` and time, `descent_m`, the summit, the SAC `urls` and the line (SNOW-1276); beside `document`, the service's walk and drawing of it, `slope_samples` and `track_render`, null until `render_ski_tours` reaches it (SNOW-1316) |
| `/api/data/v2/day-ratings/` | `region_code` + `date` | Every `SourceDayRating`: the danger range the governing bulletin gives a region's day, naming that bulletin by `bulletin_id`, with the `version` of the rule it was computed under (SNOW-1270) |

**v2 weather (SNOW-1253).** The item has the same shape and the same columns
as v1's. The one change is the key: `location` is the weather service's id
for the place (`SourceLocation.uuid`). A client holds that id as
`Location.source_uuid`, which the v1 `locations` feed now sends, so it never
needs production's own location ids. v1 stays served until staging's pull
moves to v2 (SNOW-1272). A token is bound to its URL prefix, so a client
needs a token bound to `/api/data/v2/` to read it.

**A weather item is one place's one day (SNOW-1337).** Each fetch writes
seven `SourceWeather` rows a place, today to six days ahead, each with its
`hourly` and a `forecast_on` (the day of the fetch that wrote it); the feed
serves them one-to-one, so a client's week is the next rows for that
place. There is no `forecast` list any more, and a day's icon is derived
from its own `weather_code`.

**v2 places, passes and resorts (SNOW-1271).** Served from the
reference-data service's own tables, which `mirror_reference_data` fills
from production's curated estate every hour. Every item is keyed by the
service's uuid, which a client holds as `source_uuid`, so nothing on the
wire is a production id. Every item carries `deleted_at`: a removal is
served as a tombstone, so a client paging `since` learns of it (v1 cannot
delete). A resort's document names its passes and places by their
service uuids, so a client applies places, then passes, then resorts.

**v2 ski-touring lines (SNOW-1276).** swisstopo's ski-touring map,
imported weekly into the service by `import_ski_tours` and served as two
document feeds like passes and resorts. About 20,000 segments and 2,900
tours, some 7 MB of geometry, which changes about once a year, so a
client pulls them incrementally (`since` the newest line it holds) rather
than whole. A tombstone deletes the line: no user data points at one.
Licence OGD, credit "© swisstopo".

**v2 day ratings (SNOW-1270).** The service rates every (region, day) from
its own bulletins whenever it stores one, by the rule an instance uses: the
latest-`valid_from` bulletin targeting the day, read by
`rate_render_model`. A client applies an item with
`apply_day_rating_item`: it copies the item when it was computed under the
client's own `DAY_RATING_VERSION` and the governing bulletin is held, and
recomputes the pair from its own bulletins otherwise. Apply bulletins
before day ratings. Production copies its own service's ratings as it
fetches (`copy_service_day_ratings`), so what a staging pull copies is
what production shows.

Parameters, all optional: `since` (ISO 8601 datetime **with an offset**,
filters on `updated_at`), `cursor` (the previous page's `next_cursor`) and
`limit` (default 100, at most 500). The dated v2 feeds also take `from` and
`to` (ISO dates, inclusive), which window them by the day each row is about
— a bulletin's `target_date`, a day rating's `date`, a weather row's
`observed_on` (SNOW-1272). An undated feed answers `from` or `to` with a
`400`.

```json
{
  "feed": "bulletins",
  "version": 1,
  "count": 412,
  "results": [{"bulletin_id": "…", "updated_at": "…", "pdf_url": "…", "content_hash": "…", "render_model": {…}, "valid_from": "2026-03-06T16:00:00+00:00", "target_date": "2026-03-07", "region_codes": ["CH-4115", "CH-4116"], "valid_to": "2026-03-07T16:00:00+00:00", "issued_at": "2026-03-06T16:00:00+00:00", "next_update": "2026-03-07T07:00:00+00:00", "lang": "en", "unscheduled": false, "source": "SLF", "regions": [{"regionID": "CH-4115", "name": "Martigny-Verbier"}, …]}],
  "next_cursor": "MjAyNi0xMC0yM1QwNjo…"
}
```

**What a bulletin item carries** (SNOW-1341, SNOW-1337). Not the CAAML:
the service keeps it, and serves the leanest dataset a client can display
from ([reference-data-has-one-source-of-truth](decisions/reference-data-has-one-source-of-truth.md)).
The service indexes every bulletin it stores — `valid_from` (the CAAML's
`validTime.startTime`), `target_date` (the day it forecasts) and the sorted
`region_codes` — and reads the rest of what a `Bulletin` is filed by off
the CAAML at serialise time (`bulletin_identity`): `valid_to`, `issued_at`,
`next_update`, `lang`, `unscheduled`, `source` and the CAAML's `regions`
(each entry's `regionID` and `name`, which `RegionBulletin` records). A
value the CAAML lacks is `null`, and the client refuses the item.
`content_hash` is the sha256 of the CAAML the item was built from; a
client stores it and treats an item with the same hash and PDF link as
unchanged. The render model carries its `derived` block — terrain rules,
period transition, problem family, headline inputs, alert state and danger
levels ([render-model.md](render-model.md)) — so a client copying it
derives none of it. A client still reads an item from an older service
that sent `caaml` and no identity fields, reading them off that CAAML.

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

**What is never served.** The eight querysets are the only reads, and no
user-owned table is among them. A favourite's pin is never fetched: it
borrows the nearest curated forecast (SNOW-1265), so nothing about it
leaves production. The column lists in
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

## `pull_reference_data` (SNOW-1272)

The v2 client: it seeds an empty instance from the reference-data service,
or tops one up, and is the successor to `pull_production_data`.

```bash
uv run python manage.py pull_reference_data                          # preview
uv run python manage.py pull_reference_data --commit                 # estate + last 14 days
uv run python manage.py pull_reference_data --recent 3 --commit      # estate + last 3 days
uv run python manage.py pull_reference_data --golden-week --commit   # estate + 9–15 Feb 2026
```

- **The estate is pulled whole, every time:** places, then passes, then
  resorts, each matched on `source_uuid`. A new place takes the service's
  uuid as its own `uuid` too, as the curating instance's rows do; a row a
  v1 pull made (same `uuid`, or same slug for a pass or resort with no
  `source_uuid` yet) is adopted rather than duplicated. A row keyed to
  another uuid is never re-keyed: an item whose slug it holds (a resort
  removed and recreated under a new uuid) is retried after the
  tombstones, once the old row has gone, and fails only if it still
  collides. A place's `centroid_of` claims those regions'
  centres and releases any it no longer names.
- **Tombstones are applied after every live item** — resorts, then passes,
  then places. A row is deleted, unless this instance's user data points
  at it: a place with a favourite, trip or field report, or a resort with a
  favourite, is **detached** instead — its `source_uuid` is cleared and its
  estate links (roles, pins, region centres) dropped, so it leaves
  `reference_data()` while the user's row keeps its target.
- **The ski-touring lines are pulled incrementally:** `ski-network`, then
  `ski-tours`, `since` the newest `source_updated_at` held, into
  `SkiSegment` and `SkiTour`. An item whose `updated_at` matches the row's
  is `unchanged` without its geometry being compared; a tombstone deletes
  (`deleted`, or `absent` when never held).
- **The time series are a window of days:** bulletins, then day ratings,
  then weather, with `from`/`to` sent to the feed. `--recent N` (default 14)
  is from N days ago with no upper bound, so forecasts come too;
  `--golden-week` is the seven days `seed_test_week` covers, for
  deterministic user testing. Bulletins go through `apply_bulletin`, day
  ratings through `apply_day_rating_item`, weather through `apply_weather`
  (a past day already here is `kept`; a day of a place this run
  tombstoned is `skipped`).
- **The regions are not pulled.** They are code-held fixtures every
  instance loads with its migrations.

Tallies are per feed and outcome (`locations_detached`,
`day-ratings_recomputed`, …), zeros included. A failed item is logged,
counted and does not stop the pull; any failure exits non-zero.

**Credentials.** It reads `DATA_FEED_URL` and swaps its trailing `/v1/` for
`/v2/`. With the client-credentials pair it asks for a v2 token itself. A
minted token is bound to the prefix it was minted for, so a cloud
environment needs one minted for v2, set as `DATA_FEED_V2_TOKEN`:

```bash
uv run python manage.py mint_feed_token --client "Cloud dev feed" \
    --resource https://snowdesk.info/api/data/v2/ --commit
```

## `pull_production_data`

The v1 client staging runs daily until the cut-over moves it onto
`pull_reference_data`.

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

- **Bulletins** are filed through `write_bulletin` from the item's fields,
  so region links, day ratings and grouping outlines are rebuilt by
  *staging's* code. The render model is the service's (SNOW-1270), copied
  as served. Staging holds no CAAML (SNOW-1337), so it cannot build its
  own: when production's service built the model at an older
  `RENDER_MODEL_VERSION` than staging's — staging is testing a builder
  change — the row reads as the stale-model sentinel until the service is
  rebuilt and pulled again. An item whose `content_hash` is unchanged but
  whose current-version model differs from the stored one is a service
  rebuild, and is applied.
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
`pull_reference_data --commit` at 07:20 UTC (`pull_production_data` until
SNOW-1281), after production's 06:00 `fetch_weather` and the morning
bulletin issue. It reads the estate whole and the last 14 days of the time
series, so a run missed for up to a fortnight is caught up by the next.
The job reads the same three `DATA_FEED_*` variables as the web service,
set on the job in the dashboard. Any failed item makes it exit non-zero,
which Render shows as a failed run; the item and its error are in the log,
and the run's counts are in the `CommandRun` register.

A v2 feed that cannot be read at all — refused, unreachable, or not yet
served because production is on an older release than staging's `main` —
stops only that feed: the ones after it are still pulled, and the run
exits non-zero naming it (`feeds_unread` in the register). Only the
estate (`locations`, `passes`, `resorts`) is fatal, since the series
resolve their places against it. Before this, `ski-network` answering 404
from production's release cut staging's weather off from 29 October 2026.

A first dry run on an empty client reports what a commit would do: a
location it would create counts as present for the weather and resorts
after it (SNOW-1198). Each feed page's lookups are read in one query per
kind of row, not one per item (SNOW-1199).

## What it does not do

- **Delete (v1).** A resort, location or bulletin removed on production
  stays on a client that pulls v1. `pull_reference_data` honours v2's
  tombstones for places, passes and resorts; a bulletin is never deleted. `bin/sync-staging-data`
  ([runbook](runbooks/refresh-staging-from-production.md)) is still the way
  to reset staging wholesale.
- **Copy anything derived.** Region links, day ratings and groupings are
  rebuilt on the client, never sent.
