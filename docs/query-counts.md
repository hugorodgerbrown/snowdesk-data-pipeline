---
name: query-counts
description: Query-count monitoring — monitor_query_counts baseline in perf/query_counts.txt and the X-DB-Query-Count header
status: current
last-reviewed: 2026-08-25
---

# Query-count monitoring (SNOW-13)

Per-page SQL query counts are tracked in `perf/query_counts.txt` — a
committed plain-text file with one `<name> <count>` pair per monitored
URL. The Lighthouse CI workflow runs `manage.py monitor_query_counts`
(read-only) after loading fixtures; any mismatch against the baseline
fails the check, so a reviewer sees the delta in the PR diff the same
way they see a Lighthouse-score delta.

## Two surfaces

- `apps.core.middleware.QueryCountMiddleware` attaches an
  `X-DB-Query-Count` header to every response when
  `settings.QUERY_COUNT_HEADER_ENABLED` is truthy — on in
  `development` and `perf`, off in `production`. Useful for ad-hoc
  measurement: open DevTools → Network and read the header.
- `manage.py monitor_query_counts` measures the same counts for a
  fixed URL list via the Django test client and compares / writes the
  `perf/query_counts.txt` baseline.

## Adding a new monitored URL

Append a `(name, url)` tuple to `MONITORED_URLS` in
`apps/core/management/commands/monitor_query_counts.py`, then run
`uv run python manage.py monitor_query_counts --commit` to seed
the new baseline row.

## When the count legitimately changes

When a new feature touches more of the DB, or adds a new prefetch: run
`--commit` and include the `perf/query_counts.txt` delta in the same
PR so reviewers can sanity-check the new number.

## The measured database must match a deployed one

CI builds the database the check runs against in
[`.github/workflows/lighthouse.yml`](../.github/workflows/lighthouse.yml):
`migrate`, then `sync_waffle_flags --commit`, then `loaddata` +
`seed_test_data`. That middle step mirrors
[`bin/build.sh`](../bin/build.sh) and is load-bearing, not decorative.

`apps/core/fixtures/waffle_flags.json` is the source of truth for which
flags exist, and waffle charges very different amounts depending on
whether a flag has a DB row: **three cold queries for a flag that
exists** (the `waffle_flag` row plus the `users` and `groups` m2m
lookups) against **one** for a name it never finds. So a manifest flag
missing from the CI database makes the measured page look two queries
cheaper than the deployed page actually is, and the baseline silently
becomes a number no environment produces.

**The historical case, worth keeping because it is how this rule was
learned.** SNOW-690 hit it twice, in both directions, back when the
manifest carried five flags. `routes` was seeded by a migration and absent
from the manifest, so it existed in CI and was deleted on every deploy;
`weather_layer` was the mirror image — manifest-only, with no seeding
migration, so it existed on every deployed environment and never in CI.
The committed `home` baseline of 9 was measuring a database that had the
first flag and not the second, a combination no environment has ever run.
Adding the sync step moved it to 11: not a regression, just the first
honest measurement.

Keep the CI database's flag state reconciled to the manifest, and
regenerate baselines against a database built the same way.

### This applies to your local database too

The same rule binds the local pre-push gate, which runs against the
worktree's `db.sqlite3` rather than a CI-built one. `bin/init-worktree`
seeds that DB and — since this was first hit on `main` at 7771540 —
runs `sync_waffle_flags --commit` alongside `migrate`, exactly as
`bin/build.sh` does.

A worktree seeded before that step existed still holds pre-manifest flag
rows, and the symptom is distinctive: **the gate fails with a reduction,
not a regression.** Each superuser-targeted flag out of step costs 2
queries (3 present against 1 absent), so a handful of stale rows moved
`home` by four before SNOW-724 cut the manifest to one flag. The surface
is smaller now — `sync_log` is the only row left, and no monitored URL
reads it — but the failure mode is unchanged: a database whose flag rows
disagree with the manifest measures a page no environment serves.

A reduction looks like someone's prefetch landing, which is why the rule
is worth stating plainly: **do not `--commit` a baseline to resolve a
reduction you cannot attribute to a specific merged change.** Committing
one would replace a correct baseline with a number no environment
produces, and turn a local-only failure into a CI failure for everyone.
A reduction you *can* attribute is a different matter — SNOW-724 removed
four flag checks from the anonymous homepage and took `home` down
accordingly, which belongs in the PR body as arithmetic, not in this file
as a warning. Either way, reconcile the database first:

```bash
uv run python manage.py sync_waffle_flags --commit
```

then re-run the monitor and see whether a delta survives.
