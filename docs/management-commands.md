---
name: management-commands
description: Commands — fetch_bulletins, fetch_weather, import_resorts, import_locations, prune_forecast_points, sync_waffle_flags, fixture builders
status: current
last-reviewed: 2026-08-07
---

# Management commands

`fetch_bulletins` is the single entry point for fetching avalanche bulletins
from all supported providers (SLF, ALBINA, and MeteoFrance). It
supersedes the old `fetch_data`, `backfill_data`, and
`fetch_euregio_bulletins` legacy commands and follows the design rules
below (read-only by default; opt in to writes with `--commit`).

## Design rules

These rules apply to **every** new or refactored management command.
Existing commands that pre-date these rules are being migrated; don't
copy their old shape when adding new ones. (The headline rules are
summarised in CLAUDE.md; this is the full contract. Rationale:
[`dry-run-default-commands`](decisions/dry-run-default-commands.md).)

1. **Sensible defaults — runs with no arguments.** The bare invocation
   (`uv run python manage.py <name>`) must do the most useful thing
   for the common case. Required positional arguments are a smell —
   prefer optional flags with defaults derived from context (current
   date, settings, etc.).

2. **Never alter data by default — dry-run is the default.** A command
   invoked with no arguments must not write to the database, send mail,
   or call out to a paid/rate-limited external service. The user (or a
   script) must take an **explicit** step to commit changes.

3. **Pick one of the two safe shapes** — be consistent within a command:

   **Option A (preferred for new commands): explicit `--commit`.**
   Drop `--dry-run` entirely. The command is read-only by default;
   passing `--commit` is the only way to persist changes.

   **Option B: keep `--dry-run`, but require confirmation when absent.**
   Prompt the user (`Proceed? [y/N]`) before writing when `--dry-run`
   is not passed. For unattended runs (cron, APScheduler, CI), accept
   a `--no-input` flag that skips the prompt. Production callers must
   pass `--no-input` explicitly — never default it on.

   Don't mix shapes within one command.

4. **Always implement `--verbosity`** (Django gives this for free via
   `BaseCommand` — just respect it in log calls).

5. **Exit non-zero on failure.** Any unhandled error, or a partially
   failed batch (`records_failed > 0`), must surface as a non-zero exit
   so cron/CI can detect it.

6. **Stream a growable queryset — never materialise it, and count down as
   you go (SNOW-602).** A plain `for obj in qs:` loads every matching row
   into memory before the loop body runs once — invisible on a dev
   database, fatal on a production-sized table. Use the shared helpers in
   [`apps/core/command_iteration.py`](../apps/core/command_iteration.py)
   at every call site rather than hand-rolling the loop:

   - **`iterate_rows(cmd, queryset, *, verbosity, chunk_size=None,
     describe=None)`** — the default. Orders `queryset` by `-id` and
     streams it via `.iterator()`, printing each row's id (or
     `describe(row)`, for a command that already surfaces a domain id)
     before yielding it, so stdout reads as a countdown to 1 on a long
     run. Descending id order also means a row created mid-run sorts
     *ahead* of the cursor and is never re-visited. Pass `chunk_size`
     whenever the queryset carries a `prefetch_related` — Django raises
     without it.
   - **`countdown(cmd, items, *, total, verbosity, label)`** — for a loop
     whose unit of work is a derived value with no primary key of its own
     (e.g. a `(region, date)` pair from a `values_list`). Prints `"N
     <label> remaining"` per item instead of a descending id; `total` is
     typically a `.count()` taken before streaming `items`.

   Never hand-roll OFFSET/LIMIT batching to page through a queryset —
   re-querying a slice of the *same* filtered queryset on every page is
   O(n²), and if the loop body mutates the column the queryset filters on
   (rewriting an id, flipping a status flag), a row can drop out of a
   later page's slice and silently never be visited.

   Two exemptions, each carrying an inline `# SNOW-602 exempt: …` reason
   rather than silent divergence:

   - **Derived non-row unit of work** — the pair has no id, so `countdown`
     replaces the `-id` half of the rule (`recompute_day_ratings` is the
     reference example).
   - **stdout is carrying a data artefact, not log output** — a command
     that defaults to writing e.g. CSV to stdout must not interleave
     countdown lines into it. Keep the export's own row ordering (it's the
     contract, not `-id`) and only emit the countdown when an explicit
     `--output PATH` flag redirects the data elsewhere, freeing stdout
     (`export_day_character_csv` is the reference example).

   Bounded curated-data tables (a few hundred rows — `Resort`, EAWS region
   fixtures), commands with no queryset at all (fixture builders, seeders,
   provider-API fetchers), and commands with no queryset loop (flag sync,
   query-count monitor, the scheduler, one-shot dev/ops commands) are
   exempt outright — mark the exemption inline, don't just leave the loop
   as `for x in qs:` unexplained.

## Operational requirements

Two scheduled jobs keep the public site in sync with upstream data. Both
are driven by the `snowdesk-scheduler` Render Background Worker, which
runs `python manage.py run_scheduler` and uses APScheduler (SNOW-238) to
fire the jobs on their cron schedules via `django.core.management.call_command`.
The schedule is declared in [`schedule.py`](../schedule.py) at the repo root
and documented in [`render.yaml`](../render.yaml). Both jobs run with `--commit`
so they actually persist; both exit non-zero on failure so a missed run is
visible in the worker logs.

| Job | Command | Cadence | Purpose |
|-----|---------|---------|---------|
| Bulletin ingestion | `fetch_bulletins --source slf albina meteofrance --commit` | `0,5 * * * *` (every hour at :00 and :05 UTC) | Fetches the latest bulletins from all three providers. Walks from each source's latest stored `valid_from` day up to today (UTC), so a missed run self-heals on the next invocation. |
| Weather backstop | `fetch_weather --commit` | `0 0-18/6 * * *` (00:00, 06:00, 12:00, 18:00 UTC) | Pre-warms `WeatherSnapshot` rows for every region. The live path is the HTMX-triggered `public:weather_snippet` view (see [`async-operations.md`](async-operations.md)); this job is a backstop so the first page-view of the day doesn't pay the Open-Meteo round-trip. |

Run order is handled automatically: APScheduler fires both jobs
independently on their own triggers. The weather job fires less frequently
than bulletin ingestion, so by design the weather backstop typically runs
after bulletin data has already been refreshed for that hour.

### `seed_test_data` — build the navigable test dataset (local dev and CI)

Building the test dataset with the FactoryBoy factories in `tests/factories.py`
is **the default** way to populate a fresh dev/CI database (it replaced the old
`build_test_data` + `loaddata test_data` JSON-fixture path). Running the factories
against a live DB exercises them as a side benefit.

Load the region/resort reference data first, then seed:

```bash
uv run python manage.py loaddata eaws_CH resorts
uv run python manage.py seed_test_data --all --commit
```

This brings a freshly migrated DB to a fully navigable state:

- One `Bulletin` + `RegionBulletin` + `RegionDayRating` + `WeatherSnapshot` per
  CH MicroRegion for 2026-04-08 (the map-coverage layer), plus full April 2026
  for CH-4115 (Martigny-Verbier) — 178 of each model.
- All bulletins carry render models at `RENDER_MODEL_VERSION` (day ratings via the
  production `apply_bulletin_day_ratings` service); no rebuild step is needed.
- A small standalone `ForecastCell` / `ForecastCellWeather` / `Favourite` set.
- The two named dev accounts (a superuser and an active, CH-4115-subscribed
  normal user that owns the favourites — folded in from the former
  `seed_dev_users` command). Credentials: [`docs/worktrees.md`](worktrees.md).
  `seed_test_data --include user` seeds just the accounts.

The canonical preview URL after seeding is `/ch-4115/martigny-verbier/2026-04-08/`.

Read-only by default (prints intended counts); `--commit` persists. It refuses to
run when `DEBUG=False` and expects an empty/migrated DB — it creates deterministic
bulletin IDs and one `WeatherSnapshot` per (region, date), so re-seeding a
populated DB raises a clean `CommandError`. Exactly one selection flag is required:

- `--all` — seed every model.
- `--include MODEL [MODEL ...]` — seed only the named model(s).
- `--exclude MODEL [MODEL ...]` — seed everything except the named model(s).

Model names are case-insensitive and strongly typed against a `SeedModel`
enumeration (`bulletin`, `regionbulletin`, `regiondayrating`, `weathersnapshot`,
`forecastcell`, `forecastcellweather`, `favourite`, `user`); an empty or unknown value
lists the available models. FK prerequisites of a selected model are pulled in
automatically even if not named. The dataset shape (coverage, CAAML template,
danger gradient) lives in module-level helpers in the command; row values come
from the factories.

### `seed_test_week` — load the "golden week" of real bulletins

`seed_test_data` builds a *navigable* dataset from factories, but it is CH-only,
uses one date for the map, and rates every map-coverage region `moderate`. That
makes anything about behaviour **across** days untestable — whether the region
partition shifts between issues, whether a morning bulletin supersedes the
previous evening's, whether a past date is genuinely immutable.

`seed_test_week` loads the **golden week** instead: seven consecutive real days
(Mon 2026-02-09 → Sun 2026-02-15), all three providers, full regional coverage,
selected from the committed archives under `apps/bulletins/local_mirrors/` (SNOW-528).

```bash
uv run python manage.py loaddata eaws_CH eaws_AT eaws_IT eaws_FR resorts
uv run python manage.py seed_test_week --commit
```

Nothing is fetched — the full 2025/26 season is already committed and
git-tracked, so the command needs no network access and no running dev server
(unlike `fetch_bulletins --local-mirror`, which replays the same archives over
HTTP and always ends its window at today). Every record goes through the
production `upsert_bulletin`, so the corpus gets its `RegionBulletin` links,
`RegionDayRating` rows and `BulletinGrouping` boundaries from the same services
the live ingest path uses.

Loads roughly 356 bulletins: SLF 144 (morning **and** evening issues on all
seven days), ALBINA 51 (AT + IT), Météo-France 161. The week was picked for
structural interest — all five danger levels, and a real mid-week swing
(considerable → high → very_high on the Thursday and Friday → back down).

It is a **separate command**, not a `seed_test_data --include golden-week`
layer: `SeedModel` names *models*, whereas the golden week seeds the same models
from a different *source*, so folding it in would have made
`--include golden-week bulletin` a contradiction and left `--all` carrying a
special-case exclusion to protect the [SNOW-13 query-count
baseline](query-counts.md). Keeping the commands apart makes that baseline safe
structurally.

Read-only by default (a dry run writes nothing at all, not even a
`PipelineRun`); `--commit` persists. It refuses to run when `DEBUG=False`, fails
early with a copy-pasteable `loaddata` line when the region reference data is
missing, and exits non-zero if any record fails to ingest. Re-running is
idempotent — `upsert_bulletin` keys on `bulletinID`.

Week selection and per-provider target-day resolution live in
[`apps/bulletins/services/golden_week.py`](../apps/bulletins/services/golden_week.py); see
[`decisions/golden-week-derived-not-committed.md`](decisions/golden-week-derived-not-committed.md)
for why the corpus is derived at seed time rather than committed as a fixture.

### Region fixtures (auto-loaded on deploy)

`build.sh` and `build_headless.sh` run `loaddata` against all four
`apps/regions/fixtures/eaws_*.json` files (and `region_aliases.json` on the web
service) on every deploy. The operator workflow for a fixture change is
therefore:

1. Edit the source data (vendored EAWS files, CSV).
2. Rebuild the on-disk fixture (`build_switzerland_fixture --commit`,
   `build_austria_fixture --commit`, `build_italy_fixture --commit`, or
   `build_france_fixture --commit`).
3. Commit and push. The next deploy reloads the fixture into production.

`loaddata` is idempotent (upsert by primary key, no orphan deletion),
so re-running on every deploy is safe. Manual `loaddata` against the
production DB is no longer required — but it remains the right call
for a same-day hotfix, before the next deploy lands.

**`resorts.json` is deliberately not in that list.** `Resort` rows are
editable data owned by each environment's database (admin + map editor);
reloading the fixture on every deploy would silently revert every edit.
The fixture seeds fresh local/CI databases only, and bulk editorial changes
are applied by hand with `import_resorts` (below). Rationale:
[`resorts-are-editable-data`](decisions/resorts-are-editable-data.md).

### `compute_basemap_download` — precompute per-region offline tile coverage

Populates `basemap_download` on every `MicroRegion` via
`apps.regions.services.basemap_tiles.build_region_blob`, at the download's
single zoom band (`MICRO_BAND`), clipped to the region's real boundary plus one
margin tile rather than its bounding-box rectangle (SNOW-583).

Region boundaries are static reference data and the basemap tile grid never
changes, so this is a pure function of geometry — there is no incremental or
dirty-tracking state, and a full recompute on every run is safe and idempotent.
`MicroRegion` has no stored `bbox`, so it is derived on the fly from `boundary`;
a region with no `boundary` is skipped and **counted as a failure**, so the
command exits non-zero rather than silently under-covering the map.

`build.sh` runs it with `--commit` on every deploy (SNOW-521), after the
`loaddata` step above. The `eaws_CH` fixture already ships `basemap_download`
for Switzerland; this is what backfills the FR/AT/IT fixtures, which don't —
see [`docs/offline-map.md`](offline-map.md).

```bash
# Preview what would change (default — no writes).
uv run python manage.py compute_basemap_download

# Persist the computed blobs (the deploy shape).
uv run python manage.py compute_basemap_download --commit
```

Read-only by default; `--commit` is the only flag.

### `import_resorts` — reconcile Resort against the curated sheet

`Resort`'s editorial columns (operator, website, the `why_it_matters`
line, the map `tier`, elevations, lift/run counts, piste length, season
dates, curator notes) are curated in a
spreadsheet, exported to `apps/regions/data/resorts.tsv`. `import_resorts`
reconciles the database against it in three modes, all on by default:

| Mode | Effect |
|------|--------|
| `add` | Create a resort for a sheet row whose `uuid` the DB lacks. |
| `update` | Overwrite the editorial fields of rows that do match. |
| `delete` | Remove resorts the sheet does not list. |

The sheet's *live* set is every row whose `note` does **not** start with
`NOT_A_SKI_RESORT` — the marker retires an entry that was never a
lift-served area. `delete` therefore removes both the marked rows and any
resort absent from the export; `add`/`update` skip marked rows. A resort
created in the admin must be re-exported to the sheet or it is deleted by
the next full run — use `--mode add update` to reconcile without that risk.

`region` and `canton` are read only when creating, never overwritten, and
the editorial export does not yet carry them — so a row that would need
creating is reported as an error rather than guessed at. Geocoding fields
are never touched.

Read-only by default; the dry-run at `--verbosity 2` prints a field-level
diff. Validation failures (a bad season date, a non-numeric elevation, an
unknown region) abort the whole run — nothing is half-applied.

```bash
uv run python manage.py import_resorts                     # preview everything
uv run python manage.py import_resorts -v2                 # field-level diff
uv run python manage.py import_resorts --commit            # apply
uv run python manage.py import_resorts --mode update --commit   # fields only
uv run python manage.py import_resorts --file /path/to.tsv      # other export
```

After applying it locally, refresh the seed fixture with
`dump_resorts_fixture --commit` so fresh worktrees and CI start from the
same data. Against staging/production, run it by hand — it is not part of
any deploy.

### `import_locations` — reconcile the curated location estate

Locations are curated data on exactly the `import_resorts` conventions:
uuid-keyed, `--mode add/update/delete`, read-only unless `--commit`. It
reads **two** sheets, both under `apps/locations/data/`:

| File | One row per | Keyed by |
|---|---|---|
| `locations.tsv` | a place | `uuid` |
| `resort_locations.tsv` | a resort-to-location link | `(resort_uuid, location_uuid)` |

Two sheets rather than extra columns on the resort sheet, because
flattening would cap how many locations a resort can have and force Mont
Fort to be repeated once per resort — the duplication `Location` exists to
remove. One command applies both in one transaction: a link is meaningless
without its location, and a half-applied run would leave links dangling.

**`delete` is scoped to the curated estate** — the sweep runs over
`Location.objects.named()` only. Anonymous rows minted from favourites and
observations are not the sheet's to own, and deleting them would destroy
user data.

**There is no elevation column.** Elevation is always derived
([`docs/locations.md`](locations.md)); `link_location_forecast_cells`
resolves it. That makes it a **check on the curation**: compare a resolved
height against the resort sheet's `base_elevation_m` / `top_elevation_m`
before committing, because a location whose height is nowhere near the
expected figure has been mis-pinned.

Like `import_resorts`, deliberately **not** wired into `build.sh` — these
rows are editable data owned by each environment's database.

```bash
uv run python manage.py import_locations                    # preview everything
uv run python manage.py import_locations -v2                # row-level diff
uv run python manage.py import_locations --commit           # apply
uv run python manage.py import_locations --mode update --commit  # fields only
```

### `link_location_forecast_cells` — resolve each Location's height and cell

The companion to `import_locations`, in the role
`link_resort_forecast_points` plays for `import_resorts`: the import writes
what a curator typed, this resolves the two fields needing an external call.

Per unresolved location it makes **two** Open-Meteo calls. `fetch_elevation`
gives the location's *own* height — deliberately not the forecast cell's
`elevation`, which is the representative height of whichever pin minted the
cell and is shared by everything in it. Mont Fort must carry 3328 m, not the
cell's average. Then `resolve_forecast_cell` reuses or creates the shared
cell, which is what puts the location into `ForecastCell.objects.active()`
and so into the scheduled `fetch_weather` point pass — no scheduler change.

Both calls are made even in a dry run, so the reported outcome is real; only
the writes are gated. A per-location failure is logged and counted, never
aborts the batch, and makes the command exit non-zero.

**Scoped to `Location.objects.named()`** — the curated estate. That filter
is what keeps the cost bounded: a favourite's location already carries its
cell from creation and is never unresolved, and an observation's
deliberately has none, because an observation shows no forecast panel.
Without it, every historical field report would bill an elevation lookup
for weather nothing renders.

```bash
uv run python manage.py link_location_forecast_cells             # preview
uv run python manage.py link_location_forecast_cells --commit    # persist
uv run python manage.py link_location_forecast_cells --commit --delay 2
```

### `link_region_centroid_locations` — anchor each region to a Location

Backfill for SNOW-696. Gives every `MicroRegion` with a `centre` a
`Location` at that centroid, with its elevation and forecast cell resolved
— which is what lets the bulletin page render the same multi-day forecast
panel the resort page and the favourite card already have, instead of the
one-day condition icon, hi/lo and snowfall total `WeatherSnapshot` carries.

⚠️ **Read the cost before running with `--commit`.** Up to 461 new forecast
cells, one per micro-region across AT (153), CH (149), IT (124) and FR (35)
— fewer in practice, since the 750 m / 150 m reuse thresholds fold a region
centre onto an existing resort cell where the two are close. Each new cell
adds one Open-Meteo call per fetch cycle and `fetch_weather` runs four
times daily, so roughly **1,800 additional calls per day**. Confirm the
headroom on the current plan first — the dry run reports created-versus-
reused, which is the number to check against.

**A centroid is not a place anyone goes.** The minted location carries no
name and no kind: it represents the region and sits at whatever elevation
the polygon's middle happens to fall at. Any surface showing its weather
must say which elevation it represents — the bulletin page's eyebrow reads
"Weather — region centre" for exactly this reason.

It resolves its own cell rather than deferring to
`link_location_forecast_cells`, which is scoped to `named()` so an
observation's location is never billed for a lookup it has no use for. A
region centroid is anonymous but *does* need a cell, so it resolves here,
where the candidate set is bounded by the region fixture rather than by
user activity.

`WeatherSnapshot` is untouched and keeps its job as the masthead's
day/night visual.

```bash
uv run python manage.py link_region_centroid_locations           # preview + cost
uv run python manage.py link_region_centroid_locations --commit  # apply
```

### `backfill_favourite_locations` — mint a Location per existing Favourite

One-shot backfill for SNOW-704. Every favourite created before that ticket
stores its own coordinates and points straight at a `ForecastCell`; this
mints the `Location` each one *is* and repoints the FK.

**Not a data migration** — CLAUDE.md forbids bulk dataset updates in
migrations, so this is a `--commit`-gated command.

**No external calls.** The favourite already carries the elevation and the
resolved cell from when it was created; copying them is correct and free,
and re-resolving could return a *different* answer for a pin whose weather
users have already been reading.

One transaction **per favourite**, not one for the batch: a pin that fails
must not roll back the pins already migrated, and a single transaction over
a growable user table is the lock this command exists to avoid. The minted
location carries no name and no kind — a pin's label is the user's own text
and stays on the favourite.

```bash
uv run python manage.py backfill_favourite_locations           # preview
uv run python manage.py backfill_favourite_locations --commit  # apply
```

Run it **before** the follow-up that drops `Favourite.forecast_point` /
`latitude` / `longitude` / `elevation`. Those columns stay in place for now
precisely because `build.sh` migrates on every deploy: a migration removing
them would land before an operator could run this.
### `backfill_observation_locations` — mint a Location per field report

One-shot backfill for SNOW-709, the sibling of
`backfill_favourite_locations`. Mints the `Location` each pre-SNOW-709
report happened at and points its FK there. **Not a data migration**, for
the same reason.

**Field observations are user data and immutable event records.** The
provenance model does not move and is not touched: `gps_latitude` /
`gps_longitude` (the raw device fix), `location_source` and
`accuracy_radius_km` are data about the *report*, not about the place. The
gap between the report coordinate and the device fix — "I was standing
here" versus "I tapped roughly here" — stays recoverable.

**No forecast cell.** An observation shows no forecast panel, so resolving
one would mean an Open-Meteo round trip per historical report for weather
nothing renders. `link_location_forecast_cells` is scoped to `named()` for
the same reason, so these rows are never picked up by it either.

One row per report — coordinates are not merged on equality. Exact float
equality on a user coordinate is a false economy, and wrongly merging two
reports into one place is worse than an extra row. Locations are shared by
*curation*, not by automatic dedup.

```bash
uv run python manage.py backfill_observation_locations           # preview
uv run python manage.py backfill_observation_locations --commit  # apply
```

Run it before SNOW-714 drops `FieldObservation.latitude`/`longitude`.

### `sync_waffle_flags` — reconcile waffle.Flag rows to the manifest

Reconciles the DB's `waffle.Flag` rows to the declarative manifest at
`apps/core/fixtures/waffle_flags.json` (SNOW-502) by **create + delete
only** — an existing flag is never edited in place, so an operator's
live admin-tuned targeting (`superusers`, `everyone`, `percent`, …)
always survives a re-run. See [`docs/feature-flags.md`](feature-flags.md)
for the manifest shape and the add/remove-a-flag workflow.

```bash
# Read-only: print the create/delete diff, write nothing.
uv run python manage.py sync_waffle_flags

# Persist the diff (run on every deploy via build.sh).
uv run python manage.py sync_waffle_flags --commit

# Point at an alternate manifest (mainly for tests).
uv run python manage.py sync_waffle_flags --commit --manifest path/to/flags.json
```

Read-only by default; `--commit` persists. `--manifest PATH` overrides
the default manifest path (`apps/core/fixtures/waffle_flags.json`). Respects
`--verbosity`. A missing manifest file, malformed JSON, an entry missing
`name`/`note`, a duplicate `name`, or an unrecognised key all raise a
`CommandError` (non-zero exit).

### One-off operational commands

These are not scheduled. Reach for them after a code change or data
incident that invalidates derived state:

- `rebuild_render_models --commit` — after bumping `RENDER_MODEL_VERSION`.
  Re-runs the render-model derivation for every stale `Bulletin`.
- `reformat_mf_comments --commit` — one-off retroactive formatter for FR bulletins
  ingested before SNOW-207 added the HTML formatter (plain-text prose fields only;
  already-HTML rows are skipped). Filters to `bulletin_id__startswith='FR'`.
  Rebuilds render models and refreshes RegionDayRating for all touched rows.
  Read-only by default; pass `--commit` to persist. Exits non-zero if any
  render-model build or day-rating recompute fails.

  ```bash
  # Read-only walk — counts what would change, no DB writes.
  uv run python manage.py reformat_mf_comments

  # Persist (run on Render after merge).
  uv run python manage.py reformat_mf_comments --commit

  # Single bulletin (must start with 'FR').
  uv run python manage.py reformat_mf_comments --bulletin-id FR-11-2026-05-18 --commit

  # Persist without refreshing day ratings.
  uv run python manage.py reformat_mf_comments --commit --skip-day-ratings
  ```

  Flags: `--commit`, `--bulletin-id ID`, `--batch-size N` (default 500 —
  the streamed-queryset iterator's chunk size, SNOW-602), `--skip-day-ratings`.
- `rekey_meteofrance_bulletins --commit` — one-off migration of FR bulletins from
  the old `FR-{NN}-{covered date}` identifier to
  `FR-{NN}-{covered date}-{publication timestamp}` (SNOW-559). The old id could
  not distinguish the two BRAs Météo-France publishes for one massif-day, so one
  silently overwrote the other. Derives each new id from that row's own
  `raw_data`, skips rows already on the new grammar (so it is idempotent), and
  refreshes `RegionDayRating` for every touched (region, day). Exits non-zero if
  any row cannot be re-keyed — a row with no usable publication timestamp is
  reported and left alone rather than given a guessed identity.

  ```bash
  # Read-only walk — reports what would change.
  uv run python manage.py rekey_meteofrance_bulletins

  # Persist.
  uv run python manage.py rekey_meteofrance_bulletins --commit
  ```

  Flags: `--commit`, `--bulletin-id ID`, `--batch-size N` (default 500 —
  the streamed-queryset iterator's chunk size, SNOW-602), `--skip-day-ratings`.

  Re-keying alone does not restore the issues that were previously overwritten.
  It also cannot run against a database still holding a full season of
  pre-SNOW-559 rows — none of them carry a usable publication timestamp, so
  every row fails. For that population, load the rebuilt archive and then use
  `purge_legacy_meteofrance_bulletins` below
  ([runbook](runbooks/rebuild-meteofrance-archive.md),
  [why](decisions/meteofrance-archive-replace-not-merge.md)).
- `purge_legacy_meteofrance_bulletins --commit` — deletes old-grammar
  `FR-{NN}-{covered date}` bulletins once the rebuilt archive has already
  supplied a new-grammar `FR-{NN}-{covered date}-{publication timestamp}`
  replacement (SNOW-559, SNOW-562). A candidate is deleted only when that
  exact replacement already exists — never guessed, never deleted on a
  region/date match alone. Reports a per-massif candidate/replaceable/
  unreplaced table, plus the `RegionBulletin`/`BulletinGrouping` rows that will
  cascade, the `RegionDayRating` rows whose `source_bulletin` will be nulled,
  and any `BulletinShare` rows referencing a candidate. Refreshes
  `RegionDayRating` for every touched (region, day) after deleting. Exits
  non-zero if any candidate is unreplaced, if a live `BulletinShare` references
  a candidate (without `--allow-orphaned-shares`), or if a day-rating
  recompute fails.

  ```bash
  # Read-only walk — reports the pre-flight table, deletes nothing.
  uv run python manage.py purge_legacy_meteofrance_bulletins

  # Persist.
  uv run python manage.py purge_legacy_meteofrance_bulletins --commit

  # Persist even though some candidates have a live BulletinShare.
  uv run python manage.py purge_legacy_meteofrance_bulletins --commit --allow-orphaned-shares
  ```

  Flags: `--commit`, `--batch-size N` (default 500 — the streamed-queryset
  iterator's chunk size and the delete step's chunk size, SNOW-602),
  `--skip-day-ratings`, `--allow-orphaned-shares`.
- `recompute_day_ratings --commit` — after a `DAY_RATING_VERSION` bump or
  any day-rating policy change. Re-derives every `RegionDayRating`.
- `backfill_pdf_urls --commit` — populate `Bulletin.pdf_url` for rows
  where it is currently empty. Dispatches by detecting the source from
  the raw payload's `customData` (via `render_model.detect_source()` —
  deliberately not `render_model["source"]`, which can be stale) to the
  correct per-provider URL helper (SLF / ALBINA / Météo-France).
  Read-only by default; pass `--commit` to persist. Idempotent — rows
  with an existing `pdf_url` are skipped unconditionally. For Météo-France
  rows the command hits the archive index endpoint (one call per bulletin)
  with a 0.2 s polite delay between requests; the endpoint is being
  decommissioned so failures fail open (empty URL, no abort).

  ```bash
  # Dry-run — counts what would be populated, no DB writes.
  uv run python manage.py backfill_pdf_urls

  # Persist (run on Render after deploying SNOW-295).
  uv run python manage.py backfill_pdf_urls --commit
  ```

  Flags: `--commit`.

- `backfill_bulletin_groupings --commit` — one-off post-deploy step
  after SNOW-323: computes `BulletinGrouping` rows for all bulletins
  that lack one. Groupings are normally created inline by `upsert_bulletin`
  at ingest time (via `compute_bulletin_grouping_boundary`); this command
  backfills historical rows that pre-date the ingest hook. Read-only by
  default; pass `--commit` to persist. Idempotent — bulletins that already
  have a grouping are skipped. Bulletins with no boundaried regions produce
  no row (not counted as failures). Raises `CommandError` and exits non-zero
  if any bulletin fails so cron/CI can detect partial failures.

  ```bash
  # Dry-run — counts how many bulletins would be processed.
  uv run python manage.py backfill_bulletin_groupings

  # Persist (run on Render after deploying SNOW-323).
  uv run python manage.py backfill_bulletin_groupings --commit
  ```

  Flags: `--commit`.

- `backfill_bulletin_target_dates --commit` — one-off post-deploy step
  after SNOW-560: populates `Bulletin.target_date` for rows that predate
  the field. `target_date` is normally set inline by `upsert_bulletin` at
  ingest time (`target_day_for_valid_from(valid_from)`, the same rule
  `recompute_region_day` uses); this command back-fills historical rows.
  Read-only by default; pass `--commit` to persist. The queryset
  (`target_date__isnull=True`) is itself the idempotency mechanism — a
  second run selects zero rows. Raises `CommandError` and exits non-zero
  if any row fails to derive a value so cron/CI can detect partial failures.

  ```bash
  # Dry-run — counts how many bulletins would be populated.
  uv run python manage.py backfill_bulletin_target_dates

  # Persist (run on Render after deploying SNOW-560).
  uv run python manage.py backfill_bulletin_target_dates --commit
  ```

  Flags: `--commit`.

- `backfill_bulletin_source --commit` — one-off post-deploy step after
  SNOW-581: populates `Bulletin.source` for rows that predate the field.
  The provider is normally set inline by `upsert_bulletin` at ingest time.
  Detection reads `raw_data.properties.customData` via `detect_source` —
  deliberately **not** `render_model["source"]`, which can hold a stale
  legacy value (`"euregio"`), is absent when the render model failed to
  build, and is rewritten wholesale by `rebuild_render_models`. Provenance
  is an ingest fact, so it is derived from the raw payload every time.
  Read-only by default; the dry-run prints a per-provider breakdown of what
  would be written. The queryset (`source=""`) is the idempotency
  mechanism — a second run selects only rows whose payload carries no known
  marker. Raises `CommandError` and exits non-zero if any row fails to
  detect, after writing the ones that succeeded.

  ```bash
  # Dry-run — per-provider breakdown of what would be populated.
  uv run python manage.py backfill_bulletin_source

  # Persist (run on Render after deploying SNOW-581).
  uv run python manage.py backfill_bulletin_source --commit
  ```

  Flags: `--commit`.

- `fetch_weather --start <YYYY-MM-DD> --end <YYYY-MM-DD> --commit` —
  to fill a historical gap (e.g. after adding a new region, or
  recovering from an outage longer than a day).
- `fetch_bulletins --source <src> --start-date <YYYY-MM-DD> --commit` —
  to backfill bulletins after a multi-day outage. Add `--delay 5` for
  multi-year backfills to stay polite to the public APIs.
- `audit_resort_regions --commit` — after editing resort coordinates or
  region polygons; refixes FKs and rewrites the resort fixture.
- `link_resort_forecast_points --commit` — one-off backfill for SNOW-503:
  anchors every geocoded, unlinked `Resort` to a shared `weather.ForecastCell`
  via `apps.weather.services.forecast_cells.resolve_forecast_cell` (the same
  SNOW-416 resolution/reuse machinery `create_favourite` uses). Widens
  `ForecastCell.objects.active()` (favourite-OR-resort) so the scheduled
  `fetch_weather` point pass picks up linked resorts automatically — no
  scheduler change. Read-only by default; pass `--commit` to persist the
  FK. Idempotent — a resort with `forecast_point` already set is excluded
  from the candidate set, so a second run selects nothing. Per-resort
  elevation-lookup failures are caught, logged, and counted (`failed`);
  they never abort the batch. Run once after this ships, and again after
  any future geocoding session (`?edit=resorts`).

  ```bash
  # Dry-run — resolves and reports, writes no FK.
  uv run python manage.py link_resort_forecast_points

  # Persist the resolved links.
  uv run python manage.py link_resort_forecast_points --commit

  # Tighten pacing between the per-resort elevation calls (default 1.0s).
  uv run python manage.py link_resort_forecast_points --commit --delay 2
  ```

  Flags: `--commit`, `--delay SECONDS` (default 1.0).

- `prune_forecast_points --commit` — deletes every `ForecastCell` that no
  `Favourite` and no `Resort` references (SNOW-633). A point becomes
  unreferenced when the last pin holding it goes away: a favourite deleted
  by its owner, or a resort retired by `import_resorts`. It is already
  invisible to the pipeline at that moment — the `fetch_weather` point pass
  iterates `ForecastCell.objects.active()` — so its `ForecastCellWeather`
  and `ForecastCellWeatherHistory` rows can only grow staler. Both child
  tables are `CASCADE`, so they go with the point.

  Fail-safe by FK: `Favourite.forecast_point` and `Resort.forecast_point`
  are `PROTECT`, so a point that gains a reference between the walk and the
  delete raises `ProtectedError` instead of taking a live favourite's
  weather with it. That row is counted as `failed`, the batch continues, and
  the command exits non-zero.

  Run it after any bulk resort deletion. The first production
  `import_resorts --commit` run (2026-08-04) retired 22 resorts and left 15
  unreferenced points holding 75 weather rows.

  ```bash
  # Dry-run — reports the points, weather and history rows it would delete.
  uv run python manage.py prune_forecast_points

  # Persist the deletions.
  uv run python manage.py prune_forecast_points --commit
  ```

  Flags: `--commit`.

- `uppercase_resort_choice_values --commit` — one-off post-deploy step for
  SNOW-582: rewrites `Resort.geocode_source` from its legacy lower-case
  stored values (`"manual"`, `"import"`) to the upper-case `GeocodeSource`
  choices. Read-only by default; the dry-run prints a breakdown of what
  would convert, grouped by target value. Idempotent by queryset — a
  second run selects nothing, because no lower-case values remain.

  ```bash
  # Dry-run — breakdown of what would be converted.
  uv run python manage.py uppercase_resort_choice_values

  # Persist.
  uv run python manage.py uppercase_resort_choice_values --commit
  ```

  Flags: `--commit`.

- `uppercase_account_choice_values --commit` — one-off post-deploy step for
  SNOW-582: rewrites `Subscription.geo_match_kind` and
  `PushSubscription.mechanism` from their legacy lower-case stored values
  to their upper-case `TextChoices` members. Read-only by default;
  idempotent by queryset per field.

  ```bash
  # Dry-run — breakdown of what would be converted, both fields.
  uv run python manage.py uppercase_account_choice_values

  # Persist.
  uv run python manage.py uppercase_account_choice_values --commit
  ```

  Flags: `--commit`.

- `uppercase_bulletin_choice_values --commit` — one-off post-deploy step for
  SNOW-582: rewrites `PipelineRun.status`, `Bulletin.source`, and
  `RegionDayRating.source` from their legacy lower-case stored values, plus
  a targeted rewrite of the `render_model["source"]` JSON key on rows whose
  stored value is still lower case (a plain `rebuild_render_models --commit`
  would be a no-op once every row's `render_model_version` is current, and
  `--all` would regenerate every render-model field for ~8,000 production
  rows — this rewrites exactly the one key). Read-only by default;
  idempotent by queryset/JSON-path lookup per field/key.

  ```bash
  # Dry-run — breakdown of what would be converted, all fields plus the JSON key.
  uv run python manage.py uppercase_bulletin_choice_values

  # Persist.
  uv run python manage.py uppercase_bulletin_choice_values --commit
  ```

  Flags: `--commit`.

  **Post-deploy ordering.** Between deploying the SNOW-582 choices change
  and running the three `uppercase_*_choice_values` commands above, stored
  values are lower case while the enums are upper case — reads that compare
  against an enum member (the ALBINA calendar CSS selectors, the headline
  variant matrix) fall back to their default/generic branch rather than
  erroring. Run the three commands in any order immediately after deploy,
  then verify with a read-only invocation of each (nothing left to convert).

### Health checks (read-only)

- `dump_settings` — print every environment-derived setting with secrets
  redacted, plus any validation problem, so "what is this environment
  actually configured with" does not mean reading the Render dashboard
  field by field (SNOW-580). The rows come from
  `apps.core.settings_spec.SETTINGS_SPEC`, the same spec the
  `core.settings` system check validates, so the dump and the deploy gate
  can never disagree. Read-only by construction — no `--commit` flag,
  because there is nothing to commit.

  ```bash
  # Everything, secrets redacted.
  uv run python manage.py dump_settings

  # Just what is broken — useful against a service whose deploy is failing.
  uv run python manage.py dump_settings --problems-only

  # Include each setting's purpose.
  uv run python manage.py dump_settings -v 2
  ```

  Secrets print as `***redacted***` — never a prefix or a hash, because a
  leaked prefix is still a leak in a pasted support thread. A setting that
  is empty prints `(unset)`, so "I forgot this" is visually distinct from
  "this is set to something".

- `monitor_query_counts` — diff against the committed query-count baseline
  (`perf/query_counts.txt`). Runs in CI; locally surfaces regressions
  before a PR.
- `diagnose_region_coverage` — partitions every fixture region into
  A/B/C buckets (has ratings / missing rating but present in raw /
  never seen). Run after a pipeline outage to confirm coverage has
  recovered.
- `parse_wet_snow_coverage` — reports the hit rate of the wet-snow prose
  parser over the local bulletin archive. For each `(lang, source)` pair
  that contains wet-snow or gliding-snow problems it prints counts for
  total wet-snow problems, unstructured problems, and how many the prose
  parser resolved. Pure SELECT — never writes to the database.

  ```bash
  # Scan all sources.
  uv run python manage.py parse_wet_snow_coverage

  # Restrict to one source.
  uv run python manage.py parse_wet_snow_coverage --source slf

  # Verbose: also show already-structured count per row.
  uv run python manage.py parse_wet_snow_coverage --verbosity 2
  ```

  Flags: `--source {slf,albina,meteofrance}` (default: all sources).

`--source` is required. Pass one or more provider names (case-insensitive);
both space-separated (`--source slf albina`) and repeated flags
(`--source slf --source albina`) are accepted. Duplicates are silently
deduplicated.

The cron invocation for the standard nightly run is:
`fetch_bulletins --source slf albina meteofrance --commit`

```bash
# Read-only walk, start date derived from DB for each source:
#   - populated DB: (latest bulletin valid_from day) → today
#                   (same-day overlap so morning-updates / prior-evening
#                    re-issues are refetched; duplicates are ignored)
#   - empty DB:     SEASON_START_DATE → today (first-run backstop)
# Useful as a "what would happen?" probe before committing.
uv run python manage.py fetch_bulletins --source slf
uv run python manage.py fetch_bulletins --source albina
uv run python manage.py fetch_bulletins --source meteofrance
uv run python manage.py fetch_bulletins --source slf albina meteofrance

# Persist the same gentle-default window (typical cron shape).
uv run python manage.py fetch_bulletins --source slf albina meteofrance --commit

# Today only.
uv run python manage.py fetch_bulletins --source slf --today --commit
uv run python manage.py fetch_bulletins --source albina --today --commit
uv run python manage.py fetch_bulletins --source meteofrance --today --commit

# Single day (typical one-off shape).
uv run python manage.py fetch_bulletins --source slf --date 2024-06-15 --commit

# Explicit window — overrides the smart default. End is always today (UTC);
# there is no --end-date flag.
uv run python manage.py fetch_bulletins --source slf --start-date 2024-01-01 --commit

# Re-pull existing rows.
uv run python manage.py fetch_bulletins --source slf albina meteofrance --commit --force

# Capture every fetched bulletin into each source's on-disk archive
# (deduped by bulletinID, sorted ascending by validTime.startTime).
# Independent of --commit: combine for full-fidelity capture, or use
# --stash alone to refresh the archive without DB writes.
uv run python manage.py fetch_bulletins --source slf albina meteofrance --stash
uv run python manage.py fetch_bulletins --source slf albina meteofrance --commit --stash

# Bootstrap an empty local DB against the on-disk archive instead of the
# live API. Requires the dev server to be running (SLF/ALBINA) or a local
# file:// mirror directory (Météo-France) to be configured:
#   SLF:          settings.SLF_API_LOCAL_MIRROR_URL
#   ALBINA:       settings.ALBINA_API_LOCAL_MIRROR_URL
#   Météo-France: settings.METEOFRANCE_API_LOCAL_MIRROR_URL (file:// URI)
uv run python manage.py fetch_bulletins --source slf --local-mirror --commit
uv run python manage.py fetch_bulletins --source albina --local-mirror --commit
uv run python manage.py fetch_bulletins --source meteofrance --local-mirror --commit
uv run python manage.py fetch_bulletins --source slf albina meteofrance --local-mirror --commit

# Multi-year backfill — pace API calls to be a good citizen on the
# public, no-auth SLF API. The delay applies between page/CDN fetches,
# not between individual bulletins.
uv run python manage.py fetch_bulletins --source slf \
    --start-date 2014-11-01 --delay 5 --commit

# Flags:
#   --source {slf,albina,meteofrance} [...]
#                            required. One or more providers (case-insensitive):
#                            'slf' (SLF CAAML API), 'albina' (avalanche.report CDN),
#                            or 'meteofrance' (Météo-France DPBRA APIM).
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
#   python scripts/fetch_albina_archive.py [--start-date YYYY-MM-DD]
#                                          [--end-date YYYY-MM-DD]
#                                          [--regions AT-07 IT-32-BZ IT-32-TN]
#   Overwrites apps/bulletins/local_mirrors/albina_archive.ndjson from the live ALBINA CDN.
#   Incremental additions handled by: fetch_bulletins --source albina --stash

# Rebuild the render model on stale bulletins (render_model_version < RENDER_MODEL_VERSION).
# Read-only by default — pass --commit to persist (same convention as fetch_bulletins).
uv run python manage.py rebuild_render_models           # read-only
uv run python manage.py rebuild_render_models --commit  # persist

# Flags: --commit, --all (every row), --bulletin-id <id> (single row),
#   --batch-size N (streamed-queryset iterator chunk size, default 500, SNOW-602)

# Re-derive every RegionDayRating row under the current v8 policy: min/max
# come from an elevation-band split (distinct all_day band keys) or, failing
# that, a time-period split (afternoon level above morning level); otherwise
# min_rating = max_rating = headline danger key. AM/PM fields are set whenever
# both morning and afternoon trait levels exist. Intended as a post-deployment
# step after a day-rating policy change. Read-only by default.
uv run python manage.py recompute_day_ratings                    # read-only
uv run python manage.py recompute_day_ratings --commit           # persist all pairs
uv run python manage.py recompute_day_ratings \
    --start-date 2026-01-01 --end-date 2026-04-30 --commit          # narrow window

# Flags: --commit, --start-date YYYY-MM-DD, --end-date YYYY-MM-DD

# Compare SQL query counts against the committed baseline (SNOW-13).
# Read-only by default — --commit rewrites perf/query_counts.txt.
uv run python manage.py monitor_query_counts           # CI / local gate
uv run python manage.py monitor_query_counts --commit  # accept new counts

# Recompute the derived centre + bbox on L1/L2 EAWS fixtures from the
# union of their L4 children. Run after editing apps/regions/fixtures/eaws_CH.json
# (e.g. when EAWS publishes a new season). Read-only by default; --commit
# to write the consolidated fixture.
uv run python manage.py refresh_eaws_fixtures           # diff-only
uv run python manage.py refresh_eaws_fixtures --commit  # persist

# Diagnose RegionDayRating coverage gaps (SNOW-48). Pure SELECT — never
# writes. Partitions every fixture region into A/B/C buckets:
#   A: has at least one RegionDayRating row
#   B: appears in a raw bulletin's properties.regions but has no rating row
#      (local-bug suspect)
#   C: never appears in any raw bulletin (upstream-gap suspect)
uv run python manage.py diagnose_region_coverage                       # whole archive
uv run python manage.py diagnose_region_coverage --date 2026-04-15     # single day
uv run python manage.py diagnose_region_coverage --verbose-table       # add per-region table

# Flags: --date YYYY-MM-DD (single day), --verbose-table (per-region table)

# Re-emit pipeline/fixtures/resorts.json from the current DB rows (SNOW-74).
# Use after a session of placing resort coordinates via the in-map editor
# at /?edit=resorts (DEBUG only) — without this step, edits live only
# in the local SQLite and disappear on the next loaddata. Read-only by
# default; --commit writes the file. Uses natural foreign keys so region
# round-trips as ["CH-4115"] rather than a numeric pk.
uv run python manage.py dump_resorts_fixture           # preview diff only
uv run python manage.py dump_resorts_fixture --commit  # write the fixture

# Detect Resort → MicroRegion FK mismatches (SNOW-178). For every geocoded
# Resort, builds a Point(lon, lat) and tests which MicroRegion.boundary
# polygon contains it. Three buckets:
#   (a) FK correct — silent unless --verbosity 2
#   (b) FK wrong, correct region found — printed as actionable mismatch
#   (c) Point outside every polygon — warning; never auto-fixed
# Exits non-zero when bucket-(b) is non-empty and --commit was not passed.
# --commit re-FKs bucket-(b) resorts and calls dump_resorts_fixture's
# writer to refresh apps/regions/fixtures/resorts.json. Then run:
#   loaddata apps/regions/fixtures/resorts.json
uv run python manage.py audit_resort_regions           # detect FK drift
uv run python manage.py audit_resort_regions --commit  # fix FKs + fixture

# Export a CSV of day-character labels and the inputs that feed the
# five-rule cascade in apps.bulletins.services.render_model.compute_day_character.
# One row per Bulletin. Pure SELECT — defaults to stdout, --output PATH
# writes a file. Use --lang/--start-date/--end-date to narrow the scan.
uv run python manage.py export_day_character_csv > dc.csv               # whole archive
uv run python manage.py export_day_character_csv --lang de > dc-de.csv  # one language
uv run python manage.py export_day_character_csv \
    --start-date 2026-01-01 --end-date 2026-01-31 --lang de --output dc.csv

# Flags: --output PATH, --start-date YYYY-MM-DD, --end-date YYYY-MM-DD, --lang LANG

# Build (or rebuild) apps/regions/fixtures/eaws_CH.json from EAWS source files
# (source: https://gitlab.com/eaws/eaws-regions — CC0):
#   reference_data/eaws/micro-regions/CH_micro-regions.geojson  — EAWS L4 IDs + geometry
#   reference_data/eaws/names/de.json                           — EAWS canonical German names
# L1/L2 name_native/name_en are carried through from the existing fixture
# (hand-maintained; EAWS does not publish names for CH L1/L2 prefixes).
# Produces 9 L1 MajorRegion, 21 L2 SubRegion, 149 L4 MicroRegion entries.
# Neighbour graph computed from GeoJSON geometry via Shapely buffer-intersects.
# Read-only by default — pass --commit to write the fixture.
uv run python manage.py build_switzerland_fixture          # preview only
uv run python manage.py build_switzerland_fixture --commit # write fixture

# Load the committed fixture into a local DB (production reloads via build.sh):
uv run python manage.py loaddata apps/regions/fixtures/eaws_CH.json

# Flags: --commit (write fixture; omit for a read-only summary)

# Build (or rebuild) apps/regions/fixtures/eaws_FR.json from three source files:
#   reference_data/eaws/micro-regions/FR_micro-regions.geojson  — EAWS L4 IDs + geometry
#   reference_data/eaws/names/fr.json (+ en.json)               — EAWS canonical names
#   reference_data/meteofrance/liste-massifs.geojson            — MF mountain groupings
# Produces 4 L1 MajorRegion, 4 L2 SubRegion, 35 L4 MicroRegion entries.
# Read-only by default — pass --commit to write the fixture.
uv run python manage.py build_france_fixture          # preview only
uv run python manage.py build_france_fixture --commit # write fixture

# Load the committed fixture into a local DB (production reloads via build.sh):
uv run python manage.py loaddata apps/regions/fixtures/eaws_FR.json

# Flags: --commit (write fixture; omit for a read-only summary)

# Build (or rebuild) apps/regions/fixtures/eaws_AT.json from vendored EAWS source files
# (source: https://gitlab.com/eaws/eaws-regions — CC0):
#   reference_data/eaws/micro-regions/AT-02_micro-regions.geojson.json … AT-08_micro-regions.geojson.json
# Produces 7 L1 MajorRegion (one per Austrian state), N L2 SubRegion, N L4 MicroRegion.
# Read-only by default — pass --commit to write the fixture.
uv run python manage.py build_austria_fixture          # preview only
uv run python manage.py build_austria_fixture --commit # write fixture

# Load the committed fixture into a local DB (production reloads via build.sh):
uv run python manage.py loaddata apps/regions/fixtures/eaws_AT.json

# Flags: --commit (write fixture; omit for a read-only summary)

# Build (or rebuild) apps/regions/fixtures/eaws_IT.json from vendored EAWS source files
# (source: https://gitlab.com/eaws/eaws-regions — CC0):
#   reference_data/eaws/micro-regions/IT-21_micro-regions.geojson.json … (7 files)
# Produces 7 L1 MajorRegion, N L2 SubRegion, N L4 MicroRegion.
# Read-only by default — pass --commit to write the fixture.
uv run python manage.py build_italy_fixture          # preview only
uv run python manage.py build_italy_fixture --commit # write fixture

# Load the committed fixture into a local DB (production reloads via build.sh):
uv run python manage.py loaddata apps/regions/fixtures/eaws_IT.json

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

**Scheduler note:** `fetch_bulletins` is invoked by the `snowdesk-scheduler`
Background Worker via `schedule.py` — see the Operational requirements section
at the top of this file. To add or change sources, update `_run_fetch_bulletins` in `schedule.py`
and redeploy the worker.

---

## `fetch_weather` — unified batch weather command (cron, backfill, bootstrap)

Canonical batch command for fetching Open-Meteo weather data. Covers
today's forecast fetch, historical backfills, and the local-mirror
bootstrap path. The HTMX `public:weather_snippet` lazy-load view
remains the per-request live path for individual bulletin pages.

**Default window** (no `--date`/`--start`/`--end`): start is derived
from the first non-null of (1) the latest `WeatherSnapshot.valid_for_date`
already in the DB, (2) the earliest `Bulletin.valid_from` date, (3)
`settings.SEASON_START_DATE`. End defaults to today (local timezone).

**Per-date routing** — the command splits the window at the day boundary
automatically: dates strictly before today use the Open-Meteo archive
endpoint; today uses the forecast endpoint. A single invocation therefore
covers the entire default window correctly regardless of what is already
in the DB.

**Active-ForecastCell pass (SNOW-416, widened SNOW-417)** — when the
resolved window reaches today, the command also fetches a **7-day** window
(`POINT_FORECAST_DAYS`) of daily forecast data — plus a **2-day**
(`POINT_HOURLY_DAYS`) near-term hourly series of ski-relevant variables
(temperature, snowfall, precipitation, wind speed/gusts, freezing level) —
for every **active** `ForecastCell` (a point referenced by at least one
`Favourite` **or** `regions.Resort` — widened by SNOW-503's
`link_resort_forecast_points` backfill so every geocoded resort gets a
precise, elevation-downscaled forecast too; see
[`favourites`/`accounts` glossary entries](glossary.md)),
passing the point's stored `elevation` explicitly so the forecast is
statistically downscaled to the pin's altitude. One API call per point
returns the whole window; one `ForecastCellWeather` row per day is
persisted. Each row's `freezing_level_height` is derived as the daily
maximum of that day's hourly values (Open-Meteo has no daily freezing-level
aggregate); `hourly_series` is populated for the first `POINT_HOURLY_DAYS`
rows only, `None` beyond, to keep the JSON payload bounded. No `models=`
parameter is sent, so Open-Meteo picks the highest-resolution model it has
for the point's coordinates — the same policy the region pass follows
(SNOW-699). Points
are **forecast-only**: there is no archive/backfill path for them, and
they do not participate in `--local-mirror` or `--stash` — both are
skipped cleanly for the point pass regardless of the flag values. Pass
`--skip-points` to fetch region weather only. Point failures are merged
into the same `failed` total that triggers the command's non-zero exit;
`created`/`updated` counters sum across every day of every point's window,
not one count per point.

**Short model horizons are not failures (SNOW-628)** — a day whose
`weather_code`, `sunrise` or `sunset` comes back null is dropped before the
write loop opens its transaction, and the days that did resolve are stored.
A model that covers fewer than seven days therefore stores fewer than seven
rows. This is the normal outcome and is not counted as `failed`. A payload
where *no* day resolves is malformed rather than short, and still counts.
Before this, one null day raised `NotNullViolation` inside the SNOW-546
transaction and rolled back the whole window — a point whose model ran
short wrote nothing at all.

**Forecast history (SNOW-575, opt-in since SNOW-629)** — with
`--add-history`, each stored day of the point pass also writes a
`ForecastCellWeatherHistory` row keyed on `(forecast_cell,
valid_for_date, issued_date)`, in the same transaction as its
`ForecastCellWeather` twin. It is **off by default**: nothing user-facing
reads the table, so retention is switchable without touching the
operational write. The scheduled run passes the flag when
`FETCH_WEATHER_ADD_HISTORY` is set in the environment (read at fire time,
so flipping the Render variable and restarting `snowdesk-scheduler` is
enough — no deploy). Because `ForecastCellWeather` is upserted on
`(point, date)`, a forecast day is overwritten on every run and only the
final day-of view survives; the history table retains the earlier ones, so
how a forecast moved as its day approached can be read back
(`ForecastCellWeatherHistory.objects.convergence_for(point, day)`).
`issued_date` is the run's anchor date, so the four scheduled runs in a day
collapse to one row and a forecast day accrues one row per day of its
window. The payload is a narrow subset — the scalars whose movement is the
signal — with `hourly_series` and sunrise/sunset excluded. The table starts
empty and cannot be backfilled: a past forecast no longer exists to fetch.
The counters above are unaffected; they still count `ForecastCellWeather`
rows only.

Read-only by default; the API is always called even without `--commit`,
making a bare invocation a useful connectivity probe.

> **Scheduler note:** `fetch_weather` is invoked by the `snowdesk-scheduler`
> Background Worker via `schedule.py` — see the Operational requirements section
> at the top of this file. To change the schedule, update `_run_fetch_weather` in `schedule.py`
> and redeploy the worker.

```bash
# Read-only probe over the default window — no DB writes.
uv run python manage.py fetch_weather

# Persist over the default window.
uv run python manage.py fetch_weather --commit

# Persist weather for a specific date.
uv run python manage.py fetch_weather --date 2026-05-01 --commit

# Persist weather for an explicit range (splits at today automatically).
uv run python manage.py fetch_weather \
    --start 2025-12-01 --end 2026-04-30 --commit

# Bootstrap against the on-disk archive instead of the live Open-Meteo API.
# Requires the dev server to be running and
# settings.WEATHER_API_LOCAL_MIRROR_BASE_URL to be configured (development.py).
uv run python manage.py fetch_weather --local-mirror --commit

# Capture the default window to apps/weather/local_mirrors/openmeteo_archive.ndjson.
uv run python manage.py fetch_weather --stash

# Full-fidelity: persist and stash.
uv run python manage.py fetch_weather --commit --stash

# Tighten pacing for a long historical backfill.
uv run python manage.py fetch_weather \
    --start 2020-11-01 --end 2025-04-30 --delay 2 --commit

# Region weather only — skip the active-ForecastCell pass.
uv run python manage.py fetch_weather --commit --skip-points

# Also retain the per-issue point-forecast history (SNOW-575).
uv run python manage.py fetch_weather --commit --add-history

# Flags:
#   --date         YYYY-MM-DD  single date; mutually exclusive with --start/--end
#   --start        YYYY-MM-DD  start of window (inclusive); defaults to DB-derived
#   --end          YYYY-MM-DD  end of window (inclusive); defaults to today
#   --commit                   persist WeatherSnapshot/ForecastCellWeather rows;
#                              omit for a read-only run
#   --local-mirror             replay from apps/weather/local_mirrors/openmeteo_archive.ndjson
#                              via the dev-only view (development.py only); the
#                              active-ForecastCell pass is skipped under this flag
#   --delay        SECONDS     seconds between per-region archive calls (default 1.0;
#                              pass 0 to disable; no effect on the forecast endpoint)
#   --stash                    append fetched weather records to the on-disk archive
#                              (region weather only — points never participate)
#   --skip-points              skip the active-ForecastCell forecast pass; fetch
#                              region weather only
#   --add-history              also retain a ForecastCellWeatherHistory row per
#                              stored day (SNOW-575); off by default, and passed
#                              by the scheduler when FETCH_WEATHER_ADD_HISTORY is set
```

## Development & one-shot setup commands

These commands never run on a schedule. `dev_magic_link` is dev-only and
refuses to run unless `DEBUG=True`; `mint_vapid_keypair` is a one-time
bootstrap command intended for production setup (dry-run by default, like
every other command here).

- `dev_magic_link` — prints a ready-to-open magic-link URL for an
  account so that the subscription / passkey flow can be tested
  locally without a working SMTP stack. Creates the account (verified)
  if one does not already exist. Refuses to run when `DEBUG` is `False`.

  ```bash
  uv run python manage.py dev_magic_link --email you@example.com
  ```

  Flags: `--email EMAIL` (required).

- The two well-known local dev accounts (superuser `admin@snowdesk.dev` and the
  active, CH-4115-subscribed normal user `dev@snowdesk.dev`) are seeded by
  `seed_test_data` as the `user` model — run `seed_test_data --include user` to
  create just them, or `--all` to get them alongside the full dataset. The former
  standalone `seed_dev_users` command was folded into `seed_test_data` (SNOW-454).
  Credentials: `docs/worktrees.md`.

- `mint_vapid_keypair` — generates a VAPID P-256 keypair for Web Push
  and writes the raw private scalar (single-line URL-safe-base64) to the
  secret file at `VAPID_PRIVATE_KEY_PATH`. Dry-run by default; pass
  `--commit` to write to disk. Refuses to overwrite an existing file —
  delete it manually to rotate (rotating invalidates every live
  `PushSubscription` row). Prints the `VAPID_PUBLIC_KEY` and
  `VAPID_CLAIM_EMAIL` values ready for pasting into the Render
  environment tab.

  ```bash
  # Dry-run — shows what would be generated without touching disk.
  uv run python manage.py mint_vapid_keypair

  # Generate a real keypair and write the secret file.
  uv run python manage.py mint_vapid_keypair --commit

  # Show the PEM rendering alongside the scalar (for human inspection only).
  uv run python manage.py mint_vapid_keypair --commit --verbosity 2
  ```

  Flags: `--commit` (write the secret file; omit for a read-only run).

---

## Local DB bootstrap

Two paths for seeding a fresh local development database:

### `bin/bootstrap-dev-db` — one-command seed (mirrors required)

Runs migrate, loads all region/resort fixtures, fetches bulletins from
all three providers, and backfills weather over the default window — all
via the local mirrors served by the running dev server.

**Prerequisite:** the Django dev server must already be running on :8000
before you execute this script. The SLF, ALBINA, and Open-Meteo local
mirrors are served by dev-only views in the running server.

```bash
# In one terminal — keep this running.
uv run python manage.py runserver

# In another terminal.
./bin/bootstrap-dev-db
```

Once complete, open a bulletin page to verify:
`http://localhost:8000/ch-4115/martigny-verbier/<today>/`

### Fully-offline alternative (no server required)

Seed the navigable dataset from the factories, which covers the
Martigny-Verbier region for April 2026 plus map-coverage bulletins and is
suitable for most UI work:

```bash
uv run python manage.py loaddata eaws_CH resorts
uv run python manage.py seed_test_data --all --commit
```
