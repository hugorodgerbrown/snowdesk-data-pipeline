---
name: rename-subscriptions-to-accounts
description: Rename subscriptions_* tables to accounts_*, fix django_migrations/django_content_type rows, avoid InconsistentMigrationHistory (SNOW-429)
status: current
last-reviewed: 2026-07-18
---

# Runbook — rename the `subscriptions` app to `accounts`

## When this applies

SNOW-429 renamed the `subscriptions` Django app to `accounts` in full —
module path, app label, URL namespace, template dirs, and table names. The
migration files were retargeted in place (dependency tuples and FK strings
rewritten from `"subscriptions.x"` to `"accounts.x"`), not regenerated, so
the graph shape is unchanged — but the four `subscriptions_*` tables and the
`django_migrations` / `django_content_type` bookkeeping rows on any
**already-migrated** database still say `subscriptions`. Once the renamed
code deploys, `manage.py migrate` on such a database raises
**`InconsistentMigrationHistory`**: `core.0003_requestlog_subscriber_fk` (and
`observations.0001_initial`) are recorded as applied, but their dependency —
now spelled `accounts.0001_initial` in the migration files — has no matching
row under the `accounts` app label, so Django considers it unapplied and
refuses to proceed.

Use this runbook to bring an existing database's bookkeeping in line with
the renamed app **before** deploying the renamed code. A **fresh** database
(new worktree, CI, a from-scratch `migrate`) needs nothing — the retargeted
migration files create `accounts_*` tables directly.

## What the script does

No data is touched or lost — this is a bookkeeping rename, not a rebuild:

```sql
BEGIN;
ALTER TABLE subscriptions_subscriber        RENAME TO accounts_subscriber;
ALTER TABLE subscriptions_subscription      RENAME TO accounts_subscription;
ALTER TABLE subscriptions_passkeycredential RENAME TO accounts_passkeycredential;
ALTER TABLE subscriptions_pushsubscription  RENAME TO accounts_pushsubscription;
UPDATE django_migrations   SET app = 'accounts'       WHERE app = 'subscriptions';
UPDATE django_content_type SET app_label = 'accounts' WHERE app_label = 'subscriptions';
COMMIT;
```

Works unmodified on both Postgres (production/staging) and SQLite (local
worktrees) — `ALTER TABLE ... RENAME TO` is supported by both, and neither
`django_migrations` nor `django_content_type` has engine-specific schema.

After the script, `accounts.0001`–`0005` show as already applied and the
four renamed tables satisfy every FK the retargeted migrations declare
(`core.0003`'s `to="accounts.subscriber"`, `observations.0001`'s
`to="accounts.subscriber"`) — `manage.py migrate` proceeds normally from
there.

**Postgres cosmetic note**: the script renames tables only. Index, sequence,
and constraint names Postgres generated from the old table name (e.g.
`subscriptions_subscriber_pkey`, `subscriptions_subscriber_user_id_key`)
keep their old names — this is harmless, since Django only *derives* names
when *creating* new objects, never when matching existing ones by
introspection. Rename them for cosmetic consistency only if you want to;
it is not required for `migrate` to succeed. Example (adjust names to match
`\d accounts_subscriber` output on your instance):

```sql
ALTER INDEX subscriptions_subscriber_pkey RENAME TO accounts_subscriber_pkey;
```

## Deploy ordering

Run the script on the target database, then deploy the renamed code
immediately after — the window between the two is when the **old** code
(still importing `subscriptions.*`) is running against tables it can no
longer find, and errors on every account/subscription code path. On a
pre-launch, low-traffic site this is a non-event if the window is kept
short; do it anyway in a quiet period, not mid-peak.

1. **Staging** (`main` branch, auto-deploys on merge): run the script on the
   staging DB immediately before or during the merge that lands this
   ticket, so the auto-deploy that follows lands on already-renamed tables.
2. **Production** (`release` branch, deploys when `release` fast-forwards to
   `main`): run the script on the production DB as part of cutting the
   release that includes this change — see
   [`docs/deployment.md`](../deployment.md) for the release flow. Coordinate
   the SQL run and the fast-forward so the gap between "tables renamed" and
   "renamed code live" is minutes, not hours.
3. **Local worktree DBs**: simplest is to skip the script entirely — delete
   `db.sqlite3` and let `bin/init-worktree` (or `migrate` + `loaddata
   test_data` + `seed_dev_users`) reseed from scratch against the renamed
   graph. Only run the script locally if you specifically want to preserve
   existing local data.

## Running the script

**Postgres** (staging/production, via `DATABASE_URL`):

```bash
psql "$DATABASE_URL" <<'SQL'
BEGIN;
ALTER TABLE subscriptions_subscriber        RENAME TO accounts_subscriber;
ALTER TABLE subscriptions_subscription      RENAME TO accounts_subscription;
ALTER TABLE subscriptions_passkeycredential RENAME TO accounts_passkeycredential;
ALTER TABLE subscriptions_pushsubscription  RENAME TO accounts_pushsubscription;
UPDATE django_migrations   SET app = 'accounts'       WHERE app = 'subscriptions';
UPDATE django_content_type SET app_label = 'accounts' WHERE app_label = 'subscriptions';
COMMIT;
SQL
```

**SQLite** (local worktree, if preserving data):

```bash
sqlite3 db.sqlite3 <<'SQL'
BEGIN;
ALTER TABLE subscriptions_subscriber        RENAME TO accounts_subscriber;
ALTER TABLE subscriptions_subscription      RENAME TO accounts_subscription;
ALTER TABLE subscriptions_passkeycredential RENAME TO accounts_passkeycredential;
ALTER TABLE subscriptions_pushsubscription  RENAME TO accounts_pushsubscription;
UPDATE django_migrations   SET app = 'accounts'       WHERE app = 'subscriptions';
UPDATE django_content_type SET app_label = 'accounts' WHERE app_label = 'subscriptions';
COMMIT;
SQL
```

## Verification

```bash
python manage.py migrate --check                  # exits 0 — no unapplied migrations
python manage.py showmigrations accounts core observations | grep '\[ \]'  # no output = all applied
```

- `python manage.py check` — no system-check errors.
- Confirm the four tables exist under the new name and the old names are
  gone: `\dt accounts_*` / `\dt subscriptions_*` (psql) or
  `sqlite3 db.sqlite3 ".tables"`.
- Hit `/subscribe/sign-in/` and confirm a magic-link sign-in round-trip
  still works end-to-end (Subscriber lookup, token verification, session
  login all touch the renamed tables).
- Confirm `django_content_type` rows for `accounts.subscriber` /
  `accounts.subscription` / `accounts.passkeycredential` /
  `accounts.pushsubscription` exist and the admin pages for each model
  load without a `ContentType` lookup error.

## Rollback

The inverse script, run before rolling the code back to the pre-rename
commit:

```sql
BEGIN;
ALTER TABLE accounts_subscriber        RENAME TO subscriptions_subscriber;
ALTER TABLE accounts_subscription      RENAME TO subscriptions_subscription;
ALTER TABLE accounts_passkeycredential RENAME TO subscriptions_passkeycredential;
ALTER TABLE accounts_pushsubscription  RENAME TO subscriptions_pushsubscription;
UPDATE django_migrations   SET app = 'subscriptions'       WHERE app = 'accounts';
UPDATE django_content_type SET app_label = 'subscriptions' WHERE app_label = 'accounts';
COMMIT;
```

Run this only if reverting to a pre-SNOW-429 deploy; a renamed-code deploy
against un-rolled-back tables raises the same `InconsistentMigrationHistory`
in reverse.
