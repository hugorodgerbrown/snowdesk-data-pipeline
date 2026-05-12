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

# Capture every fetched bulletin into sample_data/slf_archive.ndjson
# (deduped by bulletinID, sorted ascending by validTime.startTime).
# Independent of --commit: combine for full-fidelity capture, or use
# --stash alone to refresh the archive without DB writes.
poetry run python manage.py fetch_bulletins --stash
poetry run python manage.py fetch_bulletins --commit --stash

# Bootstrap an empty local DB end-to-end against the on-disk archive
# instead of the live SLF API. Requires the dev server to be running
# (the mirror view at /dev/slf-mirror/… is served by Django) and
# settings.SLF_API_LOCAL_MIRROR_URL to be configured (development.py).
poetry run python manage.py fetch_bulletins --source local-mirror --commit

# Multi-year backfill — pace API calls to be a good citizen on the
# public, no-auth SLF API. The delay applies between page fetches,
# not between individual bulletins.
poetry run python manage.py fetch_bulletins \
    --start-date 2014-11-01 --end-date 2024-04-30 \
    --delay 5 --commit

# Flags:
#   --start-date YYYY-MM-DD  default: latest DB bulletin's valid_from day,
#                            or settings.SEASON_START_DATE when the DB is empty.
#   --end-date   YYYY-MM-DD  default: today (UTC)
#   --date       YYYY-MM-DD  shortcut for --start-date == --end-date
#                            (mutually exclusive with the range flags)
#   --commit                 persist; omit for a read-only run
#   --force                  upsert existing bulletins instead of skipping
#   --source {live,local-mirror}
#                            default 'live' (real SLF API). 'local-mirror'
#                            replays sample_data/slf_archive.ndjson via the
#                            dev-only view; errors out if the mirror URL
#                            setting is not configured.
#   --stash                  append fetched bulletins to the on-disk archive
#   --delay      SECONDS     default 0 (no pause). Sleep N seconds between
#                            successive SLF API page fetches. Intended for
#                            multi-year backfills where being a good citizen
#                            on the public, no-auth API matters more than
#                            wall-clock speed.

# Rebuild the render model on stale bulletins (render_model_version < RENDER_MODEL_VERSION).
# Read-only by default — pass --commit to persist (same convention as fetch_bulletins).
poetry run python manage.py rebuild_render_models           # read-only
poetry run python manage.py rebuild_render_models --commit  # persist

# Flags: --commit, --all (every row), --bulletin-id <id> (single row), --batch-size N

# Re-derive every RegionDayRating row under the current v5 headline-only policy
# (min_rating = max_rating = headline danger key). Intended as a post-deployment
# step after a day-rating policy change. Read-only by default.
poetry run python manage.py recompute_day_ratings                    # read-only
poetry run python manage.py recompute_day_ratings --commit           # persist all pairs
poetry run python manage.py recompute_day_ratings \
    --start-date 2026-01-01 --end-date 2026-04-30 --commit          # narrow window

# Flags: --commit, --start-date YYYY-MM-DD, --end-date YYYY-MM-DD

# Compare SQL query counts against the committed baseline (SNOW-13).
# Read-only by default — --commit rewrites perf/query_counts.txt.
poetry run python manage.py monitor_query_counts           # CI / local gate
poetry run python manage.py monitor_query_counts --commit  # accept new counts

# Recompute the derived centre + bbox on L1/L2 EAWS fixtures from the
# union of their L4 children. Run after editing regions/fixtures/eaws.json
# (e.g. when EAWS publishes a new season). Read-only by default; --commit
# to write the consolidated fixture.
poetry run python manage.py refresh_eaws_fixtures           # diff-only
poetry run python manage.py refresh_eaws_fixtures --commit  # persist

# Diagnose RegionDayRating coverage gaps (SNOW-48). Pure SELECT — never
# writes. Partitions every fixture region into A/B/C buckets:
#   A: has at least one RegionDayRating row
#   B: appears in a raw bulletin's properties.regions but has no rating row
#      (local-bug suspect)
#   C: never appears in any raw bulletin (upstream-gap suspect)
poetry run python manage.py diagnose_region_coverage                       # whole archive
poetry run python manage.py diagnose_region_coverage --date 2026-04-15     # single day
poetry run python manage.py diagnose_region_coverage --verbose-table       # add per-region table

# Flags: --date YYYY-MM-DD (single day), --verbose-table (per-region table)

# Re-emit pipeline/fixtures/resorts.json from the current DB rows (SNOW-74).
# Use after a session of placing resort coordinates via the in-map editor
# at /map/?edit=resorts (DEBUG only) — without this step, edits live only
# in the local SQLite and disappear on the next loaddata. Read-only by
# default; --commit writes the file. Uses natural foreign keys so region
# round-trips as ["CH-4115"] rather than a numeric pk.
poetry run python manage.py dump_resorts_fixture           # preview diff only
poetry run python manage.py dump_resorts_fixture --commit  # write the fixture

# Export a CSV of day-character labels and the inputs that feed the
# five-rule cascade in bulletins.services.render_model.compute_day_character.
# One row per Bulletin. Pure SELECT — defaults to stdout, --output PATH
# writes a file. Use --lang/--start-date/--end-date to narrow the scan.
poetry run python manage.py export_day_character_csv > dc.csv               # whole archive
poetry run python manage.py export_day_character_csv --lang de > dc-de.csv  # one language
poetry run python manage.py export_day_character_csv \
    --start-date 2026-01-01 --end-date 2026-01-31 --lang de --output dc.csv

# Flags: --output PATH, --start-date YYYY-MM-DD, --end-date YYYY-MM-DD, --lang LANG
```

`SEASON_START_DATE` is read from the environment in
`config/settings/base.py` (default: `2025-11-01`) and is the first-run
backstop: a bare invocation against an empty DB captures the full
snowpack build-up. Once the DB has bulletins, `fetch_bulletins` prefers
the gentler default of "start at the latest bulletin's `valid_from` day"
so scheduled runs only re-walk a small same-day overlap (duplicates are
ignored downstream — it's the fetch that's being optimised).

---

## `fetch_weather` — fetch today's Open-Meteo weather for all regions

> **Legacy batch path.** As of SNOW-159 the primary live path for per-region
> weather data is the HTMX-triggered `public:weather_snippet` view, which
> fires a just-in-time fetch when a bulletin page renders without a
> `WeatherSnapshot`. `fetch_weather` is retained for manual probing and one-off
> bulk fetches; use `backfill_weather` for historical catch-up runs.

Reads weather data from the Open-Meteo forecast API and optionally
writes `WeatherSnapshot` rows (one per region). Read-only by default;
the API is always called even without `--commit`, making a bare
invocation a useful connectivity probe.

> **Note:** The Render cron entry for scheduled runs must be added
> separately in the Render dashboard — this command only provides the
> Django management-command entry point.

```bash
# Read-only probe for today — no DB writes; real API call.
poetry run python manage.py fetch_weather

# Persist today's weather for all regions.
poetry run python manage.py fetch_weather --commit

# Persist weather for a specific date.
poetry run python manage.py fetch_weather --date 2026-05-01 --commit

# Bootstrap against the on-disk archive instead of the live Open-Meteo API.
# Requires the dev server to be running and
# settings.WEATHER_API_LOCAL_MIRROR_BASE_URL to be configured (development.py).
poetry run python manage.py fetch_weather --source local-mirror --commit

# Capture today's weather to sample_data/openmeteo_archive.ndjson (no DB write).
poetry run python manage.py fetch_weather --stash

# Full-fidelity: persist and stash.
poetry run python manage.py fetch_weather --commit --stash

# Flags:
#   --date   YYYY-MM-DD  date to fetch for; default: today (local timezone)
#   --commit             persist WeatherSnapshot rows; omit for a read-only run
#   --source {live,local-mirror}
#                        default 'live' (real Open-Meteo forecast API).
#                        'local-mirror' replays sample_data/openmeteo_archive.ndjson
#                        via the dev-only view; errors out if the mirror URL
#                        setting is not configured.
#   --stash              append fetched weather records to the on-disk archive
```

---

## `backfill_weather` — backfill historical weather for a date range

Fetches historical weather from the Open-Meteo archive API for every
region across a date range. Requires `--start` and `--end`. Read-only
by default; pass `--commit` to persist.

The Open-Meteo archive endpoint enforces a tight free-tier rate limit,
so the command paces calls by default: `--delay` defaults to **1.0
second** between successive per-region archive calls (~60 calls/minute,
comfortably under the limit). Pass `--delay 0` to disable pacing if you
have a paid plan or a tiny region count; raise it for very long
backfills if you start to see 429 responses.

```bash
# Dry-run probe for a full season window (paced at 1 s/region by default).
poetry run python manage.py backfill_weather \
    --start 2025-12-01 --end 2026-04-30

# Persist the full season.
poetry run python manage.py backfill_weather \
    --start 2025-12-01 --end 2026-04-30 --commit

# Tighten pacing for a multi-year historical backfill.
poetry run python manage.py backfill_weather \
    --start 2020-11-01 --end 2025-04-30 --delay 2 --commit

# Disable pacing (only if you have a paid Open-Meteo plan).
poetry run python manage.py backfill_weather \
    --start 2025-12-01 --end 2026-04-30 --delay 0 --commit

# Replay from the local mirror (instant; dev server must be running).
poetry run python manage.py backfill_weather \
    --start 2026-01-01 --end 2026-01-31 --source local-mirror --commit

# Capture a range to sample_data/openmeteo_archive.ndjson (no DB write).
poetry run python manage.py backfill_weather \
    --start 2025-12-01 --end 2026-04-30 --stash

# Flags:
#   --start  YYYY-MM-DD  first date in the range (inclusive, required)
#   --end    YYYY-MM-DD  last date in the range (inclusive, required)
#   --commit             persist WeatherSnapshot rows; omit for read-only
#   --delay  SECONDS     sleep N seconds between region archive calls
#                        (default 1.0; pass 0 to disable pacing)
#   --source {live,local-mirror}
#                        default 'live' (real Open-Meteo archive API).
#                        'local-mirror' replays sample_data/openmeteo_archive.ndjson
#                        via the dev-only view; errors out if the mirror URL
#                        setting is not configured.
#   --stash              append fetched weather records to the on-disk archive
```
