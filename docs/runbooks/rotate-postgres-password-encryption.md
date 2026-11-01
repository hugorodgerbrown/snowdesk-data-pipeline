---
name: rotate-postgres-password-encryption
description: Move the production and staging Postgres roles off MD5 to SCRAM-SHA-256 — SET password_encryption, ALTER ROLE, DATABASE_URL; no rollback
status: current
last-reviewed: 2026-11-01
---

# Runbook — move the Postgres roles from MD5 to SCRAM-SHA-256

## When this applies

Use this runbook to move the application role on the production and staging
Postgres databases off MD5 password hashing and onto SCRAM-SHA-256
([SNOW-730](https://linear.app/hugorodgerbrown/issue/SNOW-730)). MD5 is
deprecated upstream: Postgres warns "MD5 password support is deprecated and
will be removed in a future release of PostgreSQL" whenever an MD5 password
is set, and the hash is weak. The `staging_sync` role was created with SCRAM
from the start (see
[`refresh-staging-from-production.md`](refresh-staging-from-production.md#1-create-a-read-only-role-on-the-production-database));
the application roles almost certainly were not.

The move is a **password rotation**. `password_encryption` only affects
passwords set after it changes, so the only way to re-hash a role is to give
it a password again, in a session that asked for SCRAM. That means every
service holding the old password in its `DATABASE_URL` has to be handed the
new one.

The fact the procedure is built around: **Postgres authenticates only at
connect time.** Established sessions survive an `ALTER ROLE … PASSWORD`. The
risk is the window between the `ALTER ROLE` committing and each service
reconnecting with the new value — keep that window short, and do it off-peak.

> Nothing here changes code, settings or `render.yaml`. Postgres and the env
> groups are dashboard-managed (`render.yaml`'s header comment).

## Who reads `DATABASE_URL`

Production and staging are **separate Postgres databases**, each with its own
application role, read through its own env group.

| Environment | Service | Kind | Picks up a new value by |
|---|---|---|---|
| Production | `snowdesk-website` | web, the only migrator | a restart |
| Production | `snowdesk-scheduler` | worker (`run_scheduler`) | a restart |
| Production | `snowdesk-background-tasks` | worker (`db_worker`) | a restart |
| Staging | `snowdesk-staging` | web | a restart |
| Staging | `snowdesk-staging-data-pull` | cron, 07:20 UTC (`pull_reference_data --commit`) | its next run — a fresh process each time |

All three production services read `fromGroup: Production`; both staging
consumers read `fromGroup: Staging`. The commented-out
`snowdesk-staging-data-sync` cron is disabled in the Blueprint — **confirm in
the dashboard that it is suspended or deleted** rather than assume so. If it
still exists, it is a third staging consumer.

`PRODUCTION_DATABASE_URL` (the `staging_sync` DSN on the Staging env group) is
a different role and is **not** touched by this runbook.

## Pre-flight — establish the facts

None of these is recorded in the repo, so the runbook establishes them first.
Do this **once per environment**, each in a `psql` session connected to that
environment's own database (Render dashboard → the database → **Connect →
PSQL Command**).

```sql
-- The server version. SCRAM has been the default since PG 14.
SHOW server_version;

-- What a password set now would be hashed with. 'md5' is why the roles are
-- on MD5; 'scram-sha-256' means only the old passwords are.
SHOW password_encryption;

-- Which login roles hold which kind of hash: md5… or SCRAM-SHA-256$….
SELECT rolname, left(rolpassword, 14) AS hash_prefix
FROM pg_authid
WHERE rolcanlogin;
```

`pg_authid` needs superuser. If the dashboard's role cannot read it
(`permission denied for table pg_authid`), the fallback is the warning itself:
any `ALTER ROLE … PASSWORD` issued under `password_encryption = 'md5'` prints
the MD5 deprecation warning, and one issued under SCRAM does not. You will see
which you got in step 2.

Then, from the dashboard:

- **The application role's name** — the user part of the environment's
  current `DATABASE_URL` (env group → `DATABASE_URL` → reveal):
  `postgres://<app_role>:<password>@<host>/<db>`.
- **Whether an env-group edit restarts the dependent services on its own.**
  Render's behaviour here has changed over time; check the env group's save
  dialog. This runbook assumes neither: it restarts each service explicitly.

Write the four facts down for each environment before going on.

## The change — production

Off-peak. Have the Production env group open in one tab and a `psql` session
connected to the **production** database in another. Every SQL statement
below runs **in that one session** — `SET` is per-session, so an `ALTER ROLE`
in a different session would hash with the server default again.

1. **Generate the password** from letters and digits only, so it survives
   being pasted into a URL (why:
   [Password characters](refresh-staging-from-production.md#password-characters)):

   ```bash
   python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(40)))"
   ```

2. **Re-issue the password under SCRAM.** This is the irreversible step: the
   old password stops authenticating the moment it commits.

   ```sql
   SET password_encryption = 'scram-sha-256';
   ALTER ROLE <app_role> WITH PASSWORD '<the new password>';
   ```

   No MD5 warning should print. If the `SET` itself is refused, stop: the
   upstream fix is a `postgresql.conf` change Render does not expose, and the
   role stays on MD5 (nothing has changed yet).

3. **Immediately** update `DATABASE_URL` on the **Production** env group,
   replacing only the password part, and save.

4. **Restart, migrator first.** `snowdesk-website` first — it serves traffic,
   so it is the fastest signal the new value works — then
   `snowdesk-scheduler` and `snowdesk-background-tasks`.

5. **Verify** (below) before touching staging.

## The change — staging

A fully separate rotation. **Close the production `psql` session**, then open
a **new** one connected to the **staging** database, repeat the pre-flight
checks there, and run staging's own statements in that new session, against
staging's own application role.

Pick a time well clear of `snowdesk-staging-data-pull`'s 07:20 UTC run: a run
that starts between the `ALTER ROLE` and the env-group save fails to
authenticate.

1. Generate a new password (as above).
2. In the staging session:

   ```sql
   SET password_encryption = 'scram-sha-256';
   ALTER ROLE <staging_app_role> WITH PASSWORD '<the new password>';
   ```

3. Update `DATABASE_URL` on the **Staging** env group and save.
4. Restart `snowdesk-staging`.
5. `snowdesk-staging-data-pull` takes the new value on its next run. Prove it
   now with the cron's **Trigger run** in the dashboard; it should exit 0.

## Verify

For each environment:

- **A fresh connect with the new DSN** — `psql "<new DATABASE_URL>" -c 'select 1'`.
  A DSN Django accepts but `psql` rejects is the `@` trap (Troubleshooting).
- **The hash prefix** — the pre-flight `pg_authid` query now shows
  `SCRAM-SHA-256$` for the application role.
- **`/healthz` green** on each web service (`snowdesk-website`,
  `snowdesk-staging`). It performs a database read. `/livez` deliberately does
  not touch Postgres, so it proves nothing here.
- **The workers' logs** (`snowdesk-scheduler`, `snowdesk-background-tasks`)
  show a clean reconnect and no `password authentication failed`.

## There is no rollback

The old password stops authenticating the instant the `ALTER ROLE` commits,
and Postgres keeps no copy of it. There is nothing to roll back to: the only
way forward from a bad state is to rotate again — generate another password,
re-issue it under SCRAM, update the env group, restart.

Everything **before** step 2 commits (the pre-flight, the generated password,
the open sessions) can be abandoned at no cost.

## Troubleshooting

- **`password authentication failed for user "<app_role>"` after the edit.**
  The env group was not saved, or a service was not restarted and is still
  connecting with the old value. Check the saved `DATABASE_URL` against the
  password you issued, then restart the failing service.
- **Django connects but `psql` rejects the same DSN.** The password contains
  an `@` (or another URL-significant character): `urlsplit` splits on the
  last `@`, so Django recovers it while libpq does not. Regenerate an
  alphanumeric password and rotate again — see
  [Password characters](refresh-staging-from-production.md#password-characters).
- **The MD5 deprecation warning printed on the `ALTER ROLE`.** The `SET`
  ran in a different session, or not at all. The role is still on MD5 with
  the new password; re-run both statements together in one session.
