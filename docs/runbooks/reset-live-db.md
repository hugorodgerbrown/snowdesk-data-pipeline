---
name: reset-live-db
description: Reset the live Render Postgres DB after a rewritten migration graph (SNOW-313): drop schema, migrate, loaddata, re-ingest, rollback
status: current
last-reviewed: 2026-06-14
---

# Runbook — reset the live database

## When this applies

Use this runbook when the migration graph has been **rewritten** (not just
extended), so production cannot migrate forward and the database must be
rebuilt from scratch. The trigger case was
[SNOW-313](https://linear.app/snowdesk/issue/SNOW-313) — *Restore Django User
model, demote Subscriber to a profile*:

- `AUTH_USER_MODEL = "subscriptions.Subscriber"` was **removed**, reverting to
  plain `auth.User`. Django **cannot un-swap a custom user model on an existing
  database** — it is an unsupported, one-way operation.
- `subscriptions.0001_initial` was redefined (Subscriber is now a
  `OneToOneField` profile with a `BigAutoField` PK and no `email` field) and
  migrations `0003`–`0006` were deleted. The commit message states the
  assumption outright: *"Old migrations 0003–0006 squashed away (rebuild-DB
  assumption)."*
- The live `django_migrations` table records the **old** graph as applied, so
  `manage.py migrate` fails — the recorded `subscriptions.0001` no longer
  matches the file on disk. The production `build.sh` → `migrate` step is
  already failing.

There is no forward migration path. The database must be dropped and rebuilt
from the new graph.

> **Do not** reach for this runbook for an ordinary additive migration — that
> deploys normally. This is only for a rewritten/squashed history that leaves
> the recorded graph incompatible with the code.

## What is lost vs. what is restored automatically

| Lost (the DB is the only source)        | Restored automatically on rebuild                          |
| --------------------------------------- | ---------------------------------------------------------- |
| Subscribers + push subscriptions        | Schema — `migrate` from the new graph (`build.sh`)          |
| Ingested bulletins, pipeline runs, weather | Reference data — **NOT automatic**, see Step 5b            |
| Request logs, day ratings, share clicks | —                                                          |
| Django superuser(s)                     | (recreate manually — Step 6)                               |
| Bulletins                               | (re-ingest via `fetch_bulletins` — Step 7)                 |

Everything except real subscriber sign-ups is reproducible. **Before running
anything, confirm the subscriber count (Step 1).** On a pre-launch, English-only
site this is usually zero/test data; if there are real sign-ups, stop and
export them first.

## Production topology

Postgres on Render via `DATABASE_URL`. One web service (`snowdesk-website`) plus
two workers (`snowdesk-scheduler`, `snowdesk-background-tasks`), all pinned to
the `release` branch and auto-deploying when CI passes on the fast-forward of
`release` to `main` (see [`render.yaml`](../../render.yaml)). Unless noted, run commands in the
**Render Shell of `snowdesk-website`**.

---

## Runbook

### 1. Pre-flight — confirm what you're destroying

```bash
# Subscriber/user count on the CURRENTLY deployed code.
python manage.py shell -c "from django.contrib.auth import get_user_model as g; print('users:', g().objects.count())"
# New code -> auth.User; old code -> Subscriber. Either way, a non-trivial
# count = real data. STOP and export before continuing.
```

### 2. Take a backup (non-negotiable — this is the rollback)

In the Render dashboard → **snowdesk-db → Backups**, trigger a manual backup.
If on a plan without managed backups:

```bash
pg_dump "$DATABASE_URL" > /tmp/pre-reset-$(date +%Y%m%d-%H%M).sql
```

Confirm the dump is non-empty before proceeding. It is the only way back.

### 3. Freeze writes

In the Render dashboard, **suspend both workers** so cron ingest and the email
worker cannot write mid-reset:

- `snowdesk-scheduler` → Suspend
- `snowdesk-background-tasks` → Suspend

### 4. Drop and recreate the schema

```bash
psql "$DATABASE_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO PUBLIC;"
```

This wipes every table including `django_migrations`, giving Django a clean
slate that matches the new graph.

### 5. Rebuild via a fresh deploy (uses the committed deploy path)

Trigger **Manual Deploy → Deploy latest commit** on `snowdesk-website`. Its
`build.sh` runs, against the now-empty DB:

```
manage.py migrate          # builds the NEW graph from scratch — succeeds
```

Watch the deploy log: confirm `migrate` completes cleanly. Using the deploy
path (not an ad-hoc shell `migrate`) guarantees the rebuild runs against the
**new committed code**, not a stale build left live by a failed deploy.

**The deploy no longer seeds reference data.** It used to run `loaddata`
over the EAWS fixtures on every deploy; that was removed because a deploy
that times out mid-write leaves 461 rows half applied, and because the
reload silently NULLed `MicroRegion.centroid_location` every time
(SNOW-771). Seeding is now an explicit operator step.

### 5b. Seed the reference data

From the Render Shell of the environment's web service. Order matters —
each step reads what the one before it wrote.

```bash
# Regions: the EAWS hierarchy and the aliases.
uv run --no-sync python manage.py loaddata \
    apps/regions/fixtures/eaws_CH.json \
    apps/regions/fixtures/eaws_FR.json \
    apps/regions/fixtures/eaws_AT.json \
    apps/regions/fixtures/eaws_IT.json \
    apps/regions/fixtures/region_aliases.json

# Offline-basemap tile coverage. Only the CH fixture ships it; this fills
# FR/AT/IT from their geometry.
uv run --no-sync python manage.py compute_basemap_download --commit

# Resorts: editable data, applied from the curated sheet rather than a
# fixture (see docs/decisions/resorts-are-editable-data.md).
uv run --no-sync python manage.py import_resorts --commit

# Weather anchors: a Location per region centroid, then one per geocoded
# resort. Both offline and idempotent.
uv run --no-sync python manage.py link_region_centroid_locations --commit
uv run --no-sync python manage.py link_resort_locations --commit
```

Confirm with the diagnostic in
[`region-centroid-backfill.md`](region-centroid-backfill.md).

### 6. Recreate the superuser

```bash
python manage.py createsuperuser
# Non-interactive:
# DJANGO_SUPERUSER_USERNAME=<email> DJANGO_SUPERUSER_EMAIL=<email> \
# DJANGO_SUPERUSER_PASSWORD=<pw> python manage.py createsuperuser --no-input
```

Username must equal the email (production invariant: email normalised
lowercase).

### 7. Re-ingest bulletins

```bash
python manage.py fetch_bulletins --source slf albina meteofrance --commit --start-date <season-start>
```

Choose `--start-date` for the depth you want back. Then run any downstream
rebuild steps the pipeline needs (`rebuild_render_models`,
`recompute_day_ratings`, `fetch_weather`) per
[`docs/management-commands.md`](../management-commands.md).

### 8. Resume workers

In the dashboard, **resume** `snowdesk-scheduler` and `snowdesk-background-tasks`.
Each redeploys and runs `build_headless.sh`, which installs dependencies and
nothing else — it applies no migrations and writes no rows, so resuming the
workers cannot disturb the database you have just rebuilt. The web service is
the only migrator (see [`deployment.md`](../deployment.md)), so make sure its
deploy has finished first. Confirm both workers reach **Live**.

### 9. Verify

```bash
python manage.py migrate --check                  # exits 0 — no unapplied migrations
python manage.py showmigrations | grep '\[ \]'     # no output = all applied
```

- Hit the homepage (the map) and one bulletin page.
- Confirm admin login works with the new superuser.
- Confirm the scheduler logs the next cron tick and the task worker is
  idle-listening.

---

## Rollback

If anything in Steps 5–7 goes wrong, restore from the Step 2 backup:

```bash
psql "$DATABASE_URL" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
psql "$DATABASE_URL" < /tmp/pre-reset-<stamp>.sql
```

Then redeploy the **previous** (pre-SNOW-313) commit — that is the code whose
schema matches the dump.

## Cautions

- Do this in a low-traffic window: the site serves errors between Step 4 and
  Step 7 completing.
- Run the reset **once**, on the web service, with the workers suspended — this
  avoids three services racing on `migrate` against an empty DB.
