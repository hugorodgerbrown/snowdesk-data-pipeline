# Management commands

`fetch_bulletins` is the single entry point for fetching avalanche bulletins
from all supported providers (SLF, EUREGIO/ALBINA, and MeteoFrance). It
supersedes the old `fetch_data`, `backfill_data`, and
`fetch_euregio_bulletins` commands and follows the management-command design
convention in CLAUDE.md (read-only by default; opt in to writes with
`--commit`).

## Operational requirements

Two scheduled jobs keep the public site in sync with upstream data. Both
must be configured in the Render dashboard (the pipeline has no
in-process scheduler — if Render's cron is paused, no new data arrives).
Both run with `--commit` so they actually persist; both exit non-zero
on failure so a missed run is visible in Render's job history.

| Job | Command | Cadence | Purpose |
|-----|---------|---------|---------|
| Bulletin ingestion | `poetry run python manage.py fetch_bulletins --source slf euregio meteofrance --commit` | Daily, after the providers publish their morning re-issues | Fetches the latest bulletins from all three providers. Walks from each source's latest stored `valid_from` day up to today (UTC), so a missed run self-heals on the next invocation. |
| Weather backstop | `poetry run python manage.py fetch_weather --commit` | Daily, after bulletin ingestion | Pre-warms `WeatherSnapshot` rows for every region. The live path is the HTMX-triggered `public:weather_snippet` view (see [`async-operations.md`](async-operations.md)); this cron is a backstop so the first page-view of the day doesn't pay the Open-Meteo round-trip. |

Run order matters: bulletin ingestion first (it discovers new regions /
days), then the weather backstop (it iterates over the regions present
in the DB). Schedule the weather job 15–30 minutes after bulletin
ingestion to give the bulletin run headroom on a slow API day.

### Region & resort fixtures (auto-loaded on deploy)

`build.sh` and `build_headless.sh` run `loaddata` against all four
`regions/fixtures/eaws_*.json` files and `regions/fixtures/resorts.json`
on every deploy. The operator workflow for a fixture change is therefore:

1. Edit the source data (vendored EAWS files, CSV, resort coordinates).
2. Rebuild the on-disk fixture (`build_switzerland_fixture --commit`,
   `build_austria_fixture --commit`, `build_italy_fixture --commit`,
   `build_france_fixture --commit`, or `dump_resorts_fixture --commit`).
3. Commit and push. The next deploy reloads the fixture into production.

`loaddata` is idempotent (upsert by primary key, no orphan deletion),
so re-running on every deploy is safe. Manual `loaddata` against the
production DB is no longer required — but it remains the right call
for a same-day hotfix, before the next deploy lands.

### One-off operational commands

These are not scheduled. Reach for them after a code change or data
incident that invalidates derived state:

- `rebuild_render_models --commit` — after bumping `RENDER_MODEL_VERSION`.
  Re-runs the render-model derivation for every stale `Bulletin`.
- `recompute_day_ratings --commit` — after a day-rating policy change
  (e.g. v5 headline-only switch). Re-derives every `RegionDayRating`.
- `backfill_weather --start <YYYY-MM-DD> --end <YYYY-MM-DD> --commit` —
  to fill a historical gap (e.g. after adding a new region, or
  recovering from an outage longer than a day).
- `fetch_bulletins --source <src> --start-date <YYYY-MM-DD> --commit` —
  to backfill bulletins after a multi-day outage. Add `--delay 5` for
  multi-year backfills to stay polite to the public APIs.
- `audit_resort_regions --commit` — after editing resort coordinates or
  region polygons; refixes FKs and rewrites the resort fixture.

### Health checks (read-only)

- `monitor_query_counts` — diff against the committed query-count baseline
  (`perf/query_counts.txt`). Runs in CI; locally surfaces regressions
  before a PR.
- `diagnose_region_coverage` — partitions every fixture region into
  A/B/C buckets (has ratings / missing rating but present in raw /
  never seen). Run after a pipeline outage to confirm coverage has
  recovered.



`--source` is required. Pass one or more provider names (case-insensitive);
both space-separated (`--source slf euregio`) and repeated flags
(`--source slf --source euregio`) are accepted. Duplicates are silently
deduplicated.

The cron invocation for the standard nightly run is:
`fetch_bulletins --source slf euregio meteofrance --commit`

```bash
# Read-only walk, start date derived from DB for each source:
#   - populated DB: (latest bulletin valid_from day) → today
#                   (same-day overlap so morning-updates / prior-evening
#                    re-issues are refetched; duplicates are ignored)
#   - empty DB:     SEASON_START_DATE → today (first-run backstop)
# Useful as a "what would happen?" probe before committing.
poetry run python manage.py fetch_bulletins --source slf
poetry run python manage.py fetch_bulletins --source euregio
poetry run python manage.py fetch_bulletins --source meteofrance
poetry run python manage.py fetch_bulletins --source slf euregio meteofrance

# Persist the same gentle-default window (typical cron shape).
poetry run python manage.py fetch_bulletins --source slf euregio meteofrance --commit

# Today only.
poetry run python manage.py fetch_bulletins --source slf --today --commit
poetry run python manage.py fetch_bulletins --source euregio --today --commit
poetry run python manage.py fetch_bulletins --source meteofrance --today --commit

# Single day (typical one-off shape).
poetry run python manage.py fetch_bulletins --source slf --date 2024-06-15 --commit

# Explicit window — overrides the smart default. End is always today (UTC);
# there is no --end-date flag.
poetry run python manage.py fetch_bulletins --source slf --start-date 2024-01-01 --commit

# Re-pull existing rows.
poetry run python manage.py fetch_bulletins --source slf euregio meteofrance --commit --force

# Capture every fetched bulletin into each source's on-disk archive
# (deduped by bulletinID, sorted ascending by validTime.startTime).
# Independent of --commit: combine for full-fidelity capture, or use
# --stash alone to refresh the archive without DB writes.
poetry run python manage.py fetch_bulletins --source slf euregio meteofrance --stash
poetry run python manage.py fetch_bulletins --source slf euregio meteofrance --commit --stash

# Bootstrap an empty local DB against the on-disk archive instead of the
# live API. Requires the dev server to be running (SLF/EUREGIO) or a local
# file:// mirror directory (MeteoFrance) to be configured:
#   SLF:         settings.SLF_API_LOCAL_MIRROR_URL
#   EUREGIO:     settings.EUREGIO_API_LOCAL_MIRROR_URL
#   MeteoFrance: settings.METEOFRANCE_API_LOCAL_MIRROR_URL (file:// URI)
poetry run python manage.py fetch_bulletins --source slf --local-mirror --commit
poetry run python manage.py fetch_bulletins --source euregio --local-mirror --commit
poetry run python manage.py fetch_bulletins --source meteofrance --local-mirror --commit
poetry run python manage.py fetch_bulletins --source slf euregio meteofrance --local-mirror --commit

# Multi-year backfill — pace API calls to be a good citizen on the
# public, no-auth SLF API. The delay applies between page/CDN fetches,
# not between individual bulletins.
poetry run python manage.py fetch_bulletins --source slf \
    --start-date 2014-11-01 --delay 5 --commit

# Flags:
#   --source {slf,euregio,meteofrance} [...]
#                            required. One or more providers (case-insensitive):
#                            'slf' (SLF CAAML API), 'euregio' (ALBINA CDN),
#                            or 'meteofrance' (MeteoFrance DPBRA APIM).
#                            Space-separated or repeat the flag.
#                            Duplicates are deduplicated.
#   --start-date YYYY-MM-DD  default: latest DB bulletin's valid_from day per
#                            source, or settings.SEASON_START_DATE when empty.
#                            Mutually exclusive with --date and --today.
#   --date       YYYY-MM-DD  shortcut for a single-day window; sets both
#                            start and end to the given date. Mutually
#                            exclusive with --start-date and --today.
#   --today                  shortcut for today-only fetch. Mutually exclusive
#                            with --start-date and --date.
#   --commit                 persist; omit for a read-only run
#   --force                  upsert existing bulletins instead of skipping
#   --local-mirror           use the dev-only mirror URL for every requested
#                            source. Errors out if the mirror URL setting is
#                            not configured for that source.
#   --stash                  append fetched bulletins to each source's archive
#   --delay      SECONDS     default 0 (no pause). Sleep N seconds between
#                            successive API page fetches. Intended for
#                            multi-year backfills where pacing matters.

# One-off archive rebuild script (not a management command):
#   python scripts/fetch_euregio_archive.py [--start-date YYYY-MM-DD]
#                                           [--end-date YYYY-MM-DD]
#                                           [--regions AT-07 IT-32-BZ IT-32-TN]
#   Overwrites bulletins/local_mirrors/euregio_archive.ndjson from the live ALBINA CDN.
#   Incremental additions handled by: fetch_bulletins --source euregio --stash

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
# union of their L4 children. Run after editing regions/fixtures/eaws_CH.json
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

# Detect Resort → MicroRegion FK mismatches (SNOW-178). For every geocoded
# Resort, builds a Point(lon, lat) and tests which MicroRegion.boundary
# polygon contains it. Three buckets:
#   (a) FK correct — silent unless --verbosity 2
#   (b) FK wrong, correct region found — printed as actionable mismatch
#   (c) Point outside every polygon — warning; never auto-fixed
# Exits non-zero when bucket-(b) is non-empty and --commit was not passed.
# --commit re-FKs bucket-(b) resorts and calls dump_resorts_fixture's
# writer to refresh regions/fixtures/resorts.json. Then run:
#   loaddata regions/fixtures/resorts.json
poetry run python manage.py audit_resort_regions           # detect FK drift
poetry run python manage.py audit_resort_regions --commit  # fix FKs + fixture

# Export a CSV of day-character labels and the inputs that feed the
# five-rule cascade in bulletins.services.render_model.compute_day_character.
# One row per Bulletin. Pure SELECT — defaults to stdout, --output PATH
# writes a file. Use --lang/--start-date/--end-date to narrow the scan.
poetry run python manage.py export_day_character_csv > dc.csv               # whole archive
poetry run python manage.py export_day_character_csv --lang de > dc-de.csv  # one language
poetry run python manage.py export_day_character_csv \
    --start-date 2026-01-01 --end-date 2026-01-31 --lang de --output dc.csv

# Flags: --output PATH, --start-date YYYY-MM-DD, --end-date YYYY-MM-DD, --lang LANG

# Build (or rebuild) regions/fixtures/eaws_CH.json from EAWS source files
# (source: https://gitlab.com/eaws/eaws-regions — CC0):
#   reference_data/eaws/micro-regions/CH_micro-regions.geojson  — EAWS L4 IDs + geometry
#   reference_data/eaws/names/de.json                           — EAWS canonical German names
# L1/L2 name_native/name_en are carried through from the existing fixture
# (hand-maintained; EAWS does not publish names for CH L1/L2 prefixes).
# Produces 9 L1 MajorRegion, 21 L2 SubRegion, 149 L4 MicroRegion entries.
# Neighbour graph computed from GeoJSON geometry via Shapely buffer-intersects.
# Read-only by default — pass --commit to write the fixture.
poetry run python manage.py build_switzerland_fixture          # preview only
poetry run python manage.py build_switzerland_fixture --commit # write fixture

# Load the committed fixture into a local DB (production reloads via build.sh):
poetry run python manage.py loaddata regions/fixtures/eaws_CH.json

# Flags: --commit (write fixture; omit for a read-only summary)

# Build (or rebuild) regions/fixtures/eaws_FR.json from three source files:
#   reference_data/eaws/micro-regions/FR_micro-regions.geojson  — EAWS L4 IDs + geometry
#   reference_data/eaws/names/fr.json (+ en.json)               — EAWS canonical names
#   reference_data/meteofrance/liste-massifs.geojson            — MF mountain groupings
# Produces 4 L1 MajorRegion, 4 L2 SubRegion, 35 L4 MicroRegion entries.
# Read-only by default — pass --commit to write the fixture.
poetry run python manage.py build_france_fixture          # preview only
poetry run python manage.py build_france_fixture --commit # write fixture

# Load the committed fixture into a local DB (production reloads via build.sh):
poetry run python manage.py loaddata regions/fixtures/eaws_FR.json

# Flags: --commit (write fixture; omit for a read-only summary)

# Build (or rebuild) regions/fixtures/eaws_AT.json from vendored EAWS source files
# (source: https://gitlab.com/eaws/eaws-regions — CC0):
#   reference_data/eaws/micro-regions/AT-02_micro-regions.geojson.json … AT-08_micro-regions.geojson.json
# Produces 7 L1 MajorRegion (one per Austrian state), N L2 SubRegion, N L4 MicroRegion.
# Read-only by default — pass --commit to write the fixture.
poetry run python manage.py build_austria_fixture          # preview only
poetry run python manage.py build_austria_fixture --commit # write fixture

# Load the committed fixture into a local DB (production reloads via build.sh):
poetry run python manage.py loaddata regions/fixtures/eaws_AT.json

# Flags: --commit (write fixture; omit for a read-only summary)

# Build (or rebuild) regions/fixtures/eaws_IT.json from vendored EAWS source files
# (source: https://gitlab.com/eaws/eaws-regions — CC0):
#   reference_data/eaws/micro-regions/IT-21_micro-regions.geojson.json … (7 files)
# Produces 7 L1 MajorRegion, N L2 SubRegion, N L4 MicroRegion.
# Read-only by default — pass --commit to write the fixture.
poetry run python manage.py build_italy_fixture          # preview only
poetry run python manage.py build_italy_fixture --commit # write fixture

# Load the committed fixture into a local DB (production reloads via build.sh):
poetry run python manage.py loaddata regions/fixtures/eaws_IT.json

# Flags: --commit (write fixture; omit for a read-only summary)
```

`SEASON_START_DATE` is read from the environment in
`config/settings/base.py` (default: `2025-11-01`) and is the first-run
backstop: when a source has no bulletins in the DB, `fetch_bulletins`
falls back to `SEASON_START_DATE` so the full snowpack build-up is
captured. Once the DB has bulletins for a source, `fetch_bulletins`
prefers the gentler default of "start at the latest bulletin's
`valid_from` day" so scheduled runs only re-walk a small same-day
overlap (duplicates are ignored downstream — it's the fetch that's being
optimised).

**Render cron entry:** update the cron job to
`fetch_bulletins --source slf euregio --commit` after this change lands
(the previous entry `fetch_bulletins --commit` will error with a missing
`--source` argument).

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

# Capture today's weather to bulletins/local_mirrors/openmeteo_archive.ndjson (no DB write).
poetry run python manage.py fetch_weather --stash

# Full-fidelity: persist and stash.
poetry run python manage.py fetch_weather --commit --stash

# Flags:
#   --date   YYYY-MM-DD  date to fetch for; default: today (local timezone)
#   --commit             persist WeatherSnapshot rows; omit for a read-only run
#   --source {live,local-mirror}
#                        default 'live' (real Open-Meteo forecast API).
#                        'local-mirror' replays bulletins/local_mirrors/openmeteo_archive.ndjson
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

# Capture a range to bulletins/local_mirrors/openmeteo_archive.ndjson (no DB write).
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
#                        'local-mirror' replays bulletins/local_mirrors/openmeteo_archive.ndjson
#                        via the dev-only view; errors out if the mirror URL
#                        setting is not configured.
#   --stash              append fetched weather records to the on-disk archive
```
