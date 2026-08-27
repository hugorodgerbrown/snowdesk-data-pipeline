---
name: refresh-staging-from-production
description: sync_from_production, snowdesk-staging-data-sync cron, PRODUCTION_DATABASE_URL read-only role — production bulletins/weather into staging
status: current
last-reviewed: 2026-08-27
---

# Runbook — refresh staging's data from production

Staging runs one web dyno with no scheduler and no task worker
([`render.yaml`](../../render.yaml)), so its database never ingests a
bulletin or a weather forecast of its own. `manage.py sync_from_production`
copies the provider-derived tables out of production instead, and the
`snowdesk-staging-data-sync` Render cron job runs it nightly at 07:20 UTC.

## What is copied, and what is not

| Copied | Not copied |
| ------ | ---------- |
| `bulletins.Bulletin` | `auth_user`, `accounts.Account`, `accounts.Subscription` |
| `bulletins.RegionBulletin` | `accounts.PasskeyCredential`, `accounts.PushSubscription` |
| `bulletins.RegionDayRating` | `favourites.Favourite`, `observations.FieldObservation` |
| `bulletins.BulletinGrouping` | `routes.Route`, `core.RequestLog` |
| `weather.ForecastCell` | `bulletins.BulletinShare` / `BulletinShareClick` |
| `weather.ForecastCellWeather` | `bulletins.PipelineRun` |
| `weather.ForecastCellWeatherHistory` | regions/resorts (fixture-loaded by `build.sh` on both sides) |
| `weather.WeatherSnapshot` | |

**No user data crosses the boundary.** That is what makes the job safe to
run unattended with no anonymisation step, and it matters more here than in
most environments: staging runs `config.settings.staging`, whose
`ImmediateBackend` sends email inline on the request. A copied subscriber
list would be a copied mailing list on a box that can actually mail it.

`PipelineRun` is excluded because it is telemetry about *production's*
ingest runs, has no natural key to upsert on, and
`Bulletin.pipeline_run` is `null=True, on_delete=SET_NULL` — so the copied
bulletins simply carry a null reference. `BulletinShareClick` is excluded
because it foreign-keys `RequestLog`, which carries IP and geo.

The authoritative table plan is `SYNC_PLAN` in
[`apps/core/services/production_sync.py`](../../apps/core/services/production_sync.py);
`tests/core/services/test_production_sync.py::test_plan_copies_no_user_data`
fails if a future table in the plan reaches a user by foreign key.

## One-time setup

### 1. Create a read-only role on the production database

Nothing in the sync issues a write against production, and
`ProductionReadOnlyRouter` blocks migrations on the alias — but the role is
the guarantee. The code is only the intent.

In the Render dashboard → **snowdesk-db → Connect → PSQL Command**, then:

```sql
CREATE ROLE staging_sync WITH LOGIN PASSWORD '<generate one>';
GRANT CONNECT ON DATABASE snowdesk TO staging_sync;
GRANT USAGE ON SCHEMA public TO staging_sync;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO staging_sync;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO staging_sync;
```

**The last line is not optional.** `GRANT SELECT ON ALL TABLES` applies only
to the tables that exist at the moment it runs. Without the default-privileges
grant, the first migration that adds a table leaves that table unreadable to
`staging_sync` — and because `information_schema.columns` only shows columns
the connected role has a privilege on, the sync reports it as
`Table 'x' does not exist in the production database` rather than as a
permissions error.

If the production plan offers a read replica, point the role at the replica
instead of the primary so a full `--all` load never competes with live
traffic.

### 2. Set `PRODUCTION_DATABASE_URL` on the Staging env group

Render dashboard → **Env Groups → Staging → Add**:

```
PRODUCTION_DATABASE_URL=postgresql://staging_sync:<password>@<host>/snowdesk
```

Set it on the **Staging** group, never Production. `config/settings/staging.py`
is the only settings module that turns the variable into a database
connection, so production has nothing to connect to even if the variable
leaks into its environment — but keep the blast radius small anyway.

Confirm the shape before deploying: `apps.core.settings_spec.postgres_dsn`
runs as a Django system check, and `build.sh` runs `migrate` (which runs
checks first), so a malformed DSN fails the staging deploy rather than the
cron job six hours later.

### 3. Verify from the staging shell

Read-only first — this touches nothing:

```bash
python manage.py sync_from_production --all
```

Expect a per-table `read=… written=… skipped=…` line and a
`[READ-ONLY] dry-run` summary. Skip counts in a dry run are meaningless
(no parent rows were written, so children cannot resolve new parents) and
the summary says so.

### 4. First full load

```bash
python manage.py sync_from_production --all --commit
```

**`--all` is required for the first load.** The default is a seven-day
`updated_at` window, which on an empty staging database copies recent
bulletins whose parents were never synced — every child row is then skipped
and the command exits non-zero.

At production volume (~8,000 bulletins) this reads the full `raw_data` and
`hourly_series` JSON columns. Reads are keyset-paginated 500 rows at a
time, so memory stays flat, but the run is not instant.

## The nightly job

`snowdesk-staging-data-sync` (see [`render.yaml`](../../render.yaml)) runs
at 07:20 UTC — after production's 06:00 `fetch_weather` pass and the hourly
`fetch_bulletins` run that picks up the morning issue
([`schedule.py`](../../schedule.py)) — so staging opens the working day
already current.

```
uv run --no-sync python manage.py sync_from_production --commit
```

The seven-day default window absorbs a few consecutive failed runs without
anyone noticing. Every table upserts on its natural key, so re-copying a row
is a no-op; running the job twice is harmless.

## Troubleshooting

### `skipped N with an unresolvable foreign key`

A child row's parent is not on staging. Almost always means the window
missed the parent — re-run with `--all --commit` to close the gap.

If it persists after an `--all` run, the missing parent is a `MicroRegion`:
production has a region id the staging fixtures do not. Check which:

```bash
python manage.py sync_from_production --all --only bulletins.RegionBulletin -v 2
```

The fix is a fixture update on `main`, not a change here — the region
fixtures are meant to be identical on both sides.

### `Table 'x' does not exist in the production database`

Either the table genuinely predates production's current migration state
(possible: staging deploys from `main`, production from `release`, so
staging can be several migrations ahead), or `staging_sync` lacks `SELECT`
on it — see the default-privileges note in step 1. `information_schema`
cannot tell the two apart, so check the grant first.

A column that exists only on staging is *not* an error: reads use the
intersection of both schemas and the missing values fall back to their
Django field defaults.

### `DATABASE_URL and PRODUCTION_DATABASE_URL point at the same database`

Exactly what it says, and the reason staging and production must never
share a database ([`docs/deployment.md`](../deployment.md)). Fix the env
group; do not work around it.

### Timestamps look wrong

`created_at` and `updated_at` on a synced row are the time it reached
staging, not the time production created it — both are
`auto_now_add`/`auto_now` on `BaseModel` and `bulk_create` stamps them.
Every timestamp that carries domain meaning (`issued_at`, `valid_from`,
`target_date`, `fetched_at`, `valid_for_date`) copies verbatim.

## Reverting

There is no undo, and none is needed: nothing here writes to production, and
staging's data is disposable by definition. To start staging over, drop and
rebuild it with [`reset-live-db.md`](reset-live-db.md) pointed at the
staging database, then run the first full load again.
