# Management commands

`fetch_bulletins` is the single entry point for fetching SLF bulletins —
it supersedes the old `fetch_data` and `backfill_data` commands and
follows the management-command design convention in CLAUDE.md
(read-only by default; opt in to writes with `--commit`).

```bash
# Read-only walk, start date derived from DB:
#   - populated DB: (latest bulletin valid_from day) → today
#                   (same-day overlap so morning-updates / prior-evening
#                    re-issues are refetched; duplicates are ignored)
#   - empty DB:     SEASON_START_DATE → today (first-run backstop)
# Useful as a "what would happen?" probe before committing.
poetry run python manage.py fetch_bulletins

# Persist the same gentle-default window.
poetry run python manage.py fetch_bulletins --commit

# Single day (typical one-off shape).
poetry run python manage.py fetch_bulletins --date 2024-06-15 --commit

# Explicit window — overrides the smart default.
poetry run python manage.py fetch_bulletins \
    --start-date 2024-01-01 --end-date 2024-12-31 --commit

# Re-pull existing rows.
poetry run python manage.py fetch_bulletins --commit --force

# Flags:
#   --start-date YYYY-MM-DD  default: latest DB bulletin's valid_from day,
#                            or settings.SEASON_START_DATE when the DB is empty.
#   --end-date   YYYY-MM-DD  default: today (UTC)
#   --date       YYYY-MM-DD  shortcut for --start-date == --end-date
#                            (mutually exclusive with the range flags)
#   --commit                 persist; omit for a read-only run
#   --force                  upsert existing bulletins instead of skipping

# Rebuild the render model on stale bulletins (render_model_version < RENDER_MODEL_VERSION).
# Read-only by default — pass --commit to persist (same convention as fetch_bulletins).
poetry run python manage.py rebuild_render_models           # read-only
poetry run python manage.py rebuild_render_models --commit  # persist

# Flags: --commit, --all (every row), --bulletin-id <id> (single row), --batch-size N

# Compare SQL query counts against the committed baseline (SNOW-13).
# Read-only by default — --commit rewrites perf/query_counts.txt.
poetry run python manage.py monitor_query_counts           # CI / local gate
poetry run python manage.py monitor_query_counts --commit  # accept new counts

# Recompute the derived centre + bbox on L1/L2 EAWS fixtures from the
# union of their L4 children. Run after editing pipeline/fixtures/regions.json
# (e.g. when EAWS publishes a new season). Read-only by default; --commit
# to write the L1/L2 fixtures.
poetry run python manage.py refresh_eaws_fixtures           # diff-only
poetry run python manage.py refresh_eaws_fixtures --commit  # persist
```

`SEASON_START_DATE` is read from the environment in
`config/settings/base.py` (default: `2025-11-01`) and is the first-run
backstop: a bare invocation against an empty DB captures the full
snowpack build-up. Once the DB has bulletins, `fetch_bulletins` prefers
the gentler default of "start at the latest bulletin's `valid_from` day"
so scheduled runs only re-walk a small same-day overlap (duplicates are
ignored downstream — it's the fetch that's being optimised).
