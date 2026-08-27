---
name: refresh-staging-from-production
description: bin/sync-staging-data, snowdesk-staging-data-sync cron, PRODUCTION_DATABASE_URL read-only role — production bulletins/weather into staging
status: current
last-reviewed: 2026-08-27
---

# Runbook — refresh staging's data from production

Staging runs one web dyno with no scheduler and no task worker
([`render.yaml`](../../render.yaml)), so its database never ingests a
bulletin or a weather forecast of its own. [`bin/sync-staging-data`](../../bin/sync-staging-data)
copies the provider-derived tables out of production instead, and the
`snowdesk-staging-data-sync` Render cron job runs it nightly at 07:20 UTC.

It is a `pg_dump`/`psql` replace, not an incremental merge: every run clears
the target tables and reloads them, preserving production's primary keys.
SNOW-736 replaced a row-by-row ORM copy that pushed ~230
`INSERT … ON CONFLICT` statements for a single table and put the staging
database into crash recovery.

## What is copied, and what is not

| Copied | Not copied |
| ------ | ---------- |
| `bulletins.PipelineRun` | `auth_user`, `accounts.Account`, `accounts.Subscription` |
| `bulletins.Bulletin` | `accounts.PasskeyCredential`, `accounts.PushSubscription` |
| `bulletins.RegionBulletin` | `favourites.Favourite`, `observations.FieldObservation` |
| `bulletins.RegionDayRating` | `routes.Route`, `core.RequestLog` |
| `bulletins.BulletinGrouping` | `bulletins.BulletinShare` / `BulletinShareClick` |
| `weather.ForecastCell` | EAWS regions (fixture-loaded by `build.sh` on both sides) |
| `weather.ForecastCellWeather` | |
| `weather.ForecastCellWeatherHistory` | |
| `weather.WeatherSnapshot` | |
| `regions.Resort` | |
| `locations.Location` *(curated rows only — see below)* | |
| `locations.ResortLocation` | |

Four of the "not copied" tables — `favourites.Favourite`,
`observations.FieldObservation`, `bulletins.BulletinShare` and
`BulletinShareClick` — are **truncated on staging and left empty**, because
they reference the copied tables. Staging's user accounts, passkeys and
routes survive untouched.

**No user data crosses the boundary.** That is what makes the job safe to
run unattended with no anonymisation step, and it matters more here than in
most environments: staging runs `config.settings.staging`, whose
`ImmediateBackend` sends email inline on the request. A copied subscriber
list would be a copied mailing list on a box that can actually mail it.

`bulletins.PipelineRun` **is** copied, unlike under the old ORM version:
`Bulletin.pipeline_run_id` points at it, and preserving primary keys means
its lack of a natural key — the only reason it was excluded before — stops
mattering. It is ingest telemetry, not user data.

`BulletinShare` and `BulletinShareClick` are excluded: a click row
foreign-keys `RequestLog`, which carries IP and geo. They are truncated on
staging (they reference the copied bulletins) and not refilled.

### `locations.Location` is the one mixed table

A `Location` is a resort's village, mid-station or peak — curated — but
`apps/favourites/services.py` and `apps/observations/views.py` also mint one
per saved map pin and per field report, straight from user input. The table
therefore holds curated and personal rows side by side, and copying it whole
would put somebody's saved positions on staging.

The copy restricts it to rows that a `ResortLocation` references — a
**structural** test of "is this curated", not a heuristic on whether the row
happens to have a name. It is a `\copy` of a filtered `SELECT` rather than a
`pg_dump --table`, so an unreferenced Location is never read out of
production at all, rather than read and then discarded.

Region-centroid Locations are *not* copied: `regions_microregion` is not in
the set. Its `centroid_location_id` is released before the truncate and
rebuilt afterwards by `link_region_centroid_locations --commit`, which the
script runs for you.

### Why copying resorts does not contradict the resorts-are-editable ADR

[`resorts-are-editable-data`](../decisions/resorts-are-editable-data.md) keeps
`resorts.json` out of the deploy-time `loaddata` list, because reloading the
fixture made it authoritative *over* production and silently reverted edits
made in the admin and map editor. This sync runs the other way: production is
the authoritative curated set, and copying it to staging is the only route
those edits have ever had. St. Moritz's coordinate, placed by hand in the
production map editor on 2026-07-28, reaches staging by nothing else.

Staging's own resort edits are overwritten by design — staging is disposable,
and the point is for it to look like production.

The authoritative lists are `LOAD_ORDER` and `CLEAR_ONLY` in
[`bin/sync-staging-data`](../../bin/sync-staging-data).
[`tests/bin/test_sync_staging_data.py`](../../tests/bin/test_sync_staging_data.py)
recomputes the foreign-key closure from Django's model metadata and fails if
those lists drift — which they will, silently, the first time a new model
points at a copied table. It also asserts no user-owned table is ever
loaded.

## One-time setup

### 1. Create a read-only role on the production database

The script only ever reads from production — `pg_dump` and a `\copy … TO
STDOUT` — but the role is the guarantee. The code is only the intent.

In the Render dashboard → **snowdesk-db → Connect → PSQL Command**, then:

Generate the password from letters and digits only — it has to survive being
pasted into a URL (see "Password characters" below):

```bash
python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(40)))"
```

```sql
-- Ask for a SCRAM-SHA-256 password hash before creating the role. Without
-- this, CREATE ROLE warns "MD5 password support is deprecated and will be
-- removed in a future release of PostgreSQL" — see the note below.
SET password_encryption = 'scram-sha-256';

CREATE ROLE staging_sync WITH LOGIN PASSWORD '<generate one>';
GRANT CONNECT ON DATABASE snowdesk TO staging_sync;
GRANT USAGE ON SCHEMA public TO staging_sync;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO staging_sync;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO staging_sync;
```

**On the MD5 deprecation warning.** Current PostgreSQL defaults
`password_encryption` to `scram-sha-256`, so seeing that warning means this
server is set to `md5` (or predates the default change in PG 14). The warning
is not an error: the role is created and MD5 authentication still works —
it is deprecated, not removed — so the sync connects either way. But the
hash is weak and the method is on its way out, so prefer SCRAM.

`password_encryption` only affects passwords set *after* it changes, so
setting it does nothing to a role that already exists. If the role was
already created and warned, re-issue its password in the same session:

```sql
SET password_encryption = 'scram-sha-256';
ALTER ROLE staging_sync WITH PASSWORD '<the same value, or a new one>';
```

If a new value is used, update `PRODUCTION_DATABASE_URL` to match. If the
`SET` is refused, the upstream procedure is a `postgresql.conf` change, which
Render does not expose — leave the role on MD5 and raise it separately.

The rest of the roles on this database, the application role included, are
almost certainly on MD5 for the same reason. That is a pre-existing posture
question for the whole instance rather than anything this runbook introduces;
worth a ticket, not worth blocking the sync on.

**The last line is not optional.** `GRANT SELECT ON ALL TABLES` applies only
to the tables that exist at the moment it runs. Without the default-privileges
grant, the first migration that adds a table leaves that table unreadable to
`staging_sync`, and `pg_dump` fails the run with a permission error naming
it.

If the production plan offers a read replica, point the role at the replica
instead of the primary so a full load never competes with live traffic.

### Password characters

`PRODUCTION_DATABASE_URL` is a URL, so the password is URL syntax and `@`,
`/`, `?`, `#`, `%` and `:` all mean something in it. An `@` is the one that
bites first: it separates userinfo from host, so `psql` and anything else
going through libpq reject the DSN outright. Percent-encoding is the
documented answer (`@` is `%40`), but generating an alphanumeric password
avoids the question in psql, libpq, the Render UI and the shell at once.

Django is misleadingly forgiving here, which is why this is worth writing
down: `urlsplit` splits userinfo on the **last** `@`, so
`dj_database_url.parse` recovers the right password from an unencoded one and
the app connects while `psql` with the same string does not. Do not take a
working Django connection as evidence the DSN is well-formed.

To change the password on an existing role — which is also how to move it off
MD5, since `password_encryption` only affects passwords set after it changes:

```sql
SET password_encryption = 'scram-sha-256';
ALTER ROLE staging_sync WITH PASSWORD '<new alphanumeric password>';
```

Then update `PRODUCTION_DATABASE_URL` to match.

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

Read-only first — this writes nothing:

```bash
./bin/sync-staging-data
```

It verifies the region primary keys match, prints a production-vs-staging
row count per table, and stops. Confirm the production counts look like a
live database and that `regions_microregion verified identical` appears.

Check the client tools are present and new enough while you are there —
`pg_dump` refuses to read a server newer than itself:

```bash
pg_dump --version && psql -At -c 'SHOW server_version;' "$DATABASE_URL"
```

### 4. Load

```bash
./bin/sync-staging-data --commit
```

Minutes, not tens of minutes: each table moves in a single `COPY`.

**It clears before it loads, and the blast radius is wider than the table
list.** Staging's own favourites, field observations and bulletin shares
reference the copied tables, so they are truncated too and are *not*
refilled. Staging's user accounts, passkeys and routes are untouched. The
script's `CLEAR_ONLY` array is the exhaustive list.

Region centroids are released before the truncate
(`regions_microregion.centroid_location_id`) and rebuilt at the end by
`link_region_centroid_locations --commit`, which the script runs for you.

## The nightly job

`snowdesk-staging-data-sync` (see [`render.yaml`](../../render.yaml)) runs
at 07:20 UTC — after production's 06:00 `fetch_weather` pass and the hourly
`fetch_bulletins` run that picks up the morning issue
([`schedule.py`](../../schedule.py)) — so staging opens the working day
already current.

```
./bin/sync-staging-data --commit
```

Every run is a full replace, so there is no incremental window to fall
behind and no partial state to reconcile: a failed run costs one day's
freshness and the next run is a clean slate. Running it twice in a row is
harmless.

## Troubleshooting

### `regions_microregion primary keys differ between the two databases`

The script stops before touching anything. `COPY` preserves production's
`region_id` foreign keys verbatim, so mismatched region ids would attach
every bulletin to the *wrong region* — silently, with no error and no failed
row. That is why it is checked on every run rather than assumed.

The region fixtures are loaded by natural key with no explicit `pk`, so ids
come from insertion order and are equal only because `build.sh` loads the
same fixtures in the same order on both sides. If they have diverged, the
fix is to reconcile the fixtures and reload, not to bypass the check.

### `cannot truncate a table referenced in a foreign key constraint`

A model gained a foreign key into the copied set and its table is not in the
script's `CLEAR_ONLY` list. Postgres names the offender because the script
truncates **without** `CASCADE` — deliberately, since
`regions_microregion.centroid_location_id` references `locations_location`
and a `CASCADE` would reach the region table and most of the database
through it.

Add the named table to `CLEAR_ONLY`, or — if it must survive and its column
is nullable — null it first the way `centroid_location_id` is handled.
`tests/bin/test_sync_staging_data.py` recomputes this closure from the model
graph and should have failed in CI first; if it did not, the test needs
fixing too.

### `pg_dump: server version mismatch`

`pg_dump` refuses to read a server newer than itself. Check both:

```bash
pg_dump --version && psql -At -c 'SHOW server_version;' "$PRODUCTION_DATABASE_URL"
```

The client comes from the Render image, so the fix is an image or plan
change rather than anything in this repo.

### `DATABASE_URL and PRODUCTION_DATABASE_URL point at the same database`

Exactly what it says, and the reason staging and production must never
share a database ([`docs/deployment.md`](../deployment.md)). The script
truncates its target, so it refuses rather than risk it. Fix the env group;
do not work around it.

### Staging's favourites and observations have gone

Expected. They reference the copied tables, so they are truncated with them
and not refilled — see step 4. Staging user accounts, passkeys and routes
survive.

## Reverting

There is no undo, and none is needed: nothing here writes to production, and
staging's data is disposable by definition. Re-running the script is itself
the recovery path — every run is a full replace, so a half-finished run is
repaired by the next one rather than needing to be unwound.
