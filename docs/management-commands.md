---
name: management-commands
description: Commands — fetch_bulletins, fetch_weather, purge_request_logs, import_resorts, import_locations, link_region_centroid_locations
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

Three scheduled jobs run on the worker: two keep the public site in sync
with upstream data, and one enforces a data-retention window. All three
are driven by the `snowdesk-scheduler` Render Background Worker, which
runs `python manage.py run_scheduler` and uses APScheduler (SNOW-238) to
fire the jobs on their cron schedules via `django.core.management.call_command`.
The schedule is declared in [`schedule.py`](../schedule.py) at the repo root
and documented in [`render.yaml`](../render.yaml). All run with `--commit`
so they actually persist; all exit non-zero on failure so a missed run is
visible in the worker logs.

| Job | Command | Cadence | Purpose |
|-----|---------|---------|---------|
| Bulletin ingestion | `fetch_bulletins --source slf albina meteofrance --commit` | `0,5 * * * *` (every hour at :00 and :05 UTC) | Fetches the latest bulletins from all three providers. Walks from each source's latest stored `valid_from` day up to today (UTC), so a missed run self-heals on the next invocation. |
| Weather ingestion | `fetch_weather --commit` | `0 0,6,12,18 * * *` (four times a day, on the hour UTC) | Fetches today's Open-Meteo forecast for every active location. Four runs because a location has no live on-demand fetch behind its page render the way a bulletin region does — the scheduled batch is the only thing keeping today's row current. |
| Request-log retention | `purge_request_logs --commit` | `30 3 * * *` (daily, 03:30 UTC) | Deletes `RequestLog` rows past the twelve-month retention window the Privacy Policy states (SNOW-775). Runs at :30 on an hour no fetch job uses, because it holds a delete transaction over a table the request path writes to. |

### `purge_request_logs` — enforce the RequestLog retention window

`RequestLog` is written at sign-up, sign-in, subscribe, add-region and
share-click, and every row carries `ip_address`, `city`, `latitude`,
`longitude`, `user_agent` and `session_key`. Until SNOW-775 nothing deleted
any of it, while `/privacy/` told readers technical request data was kept
for fourteen days — a promise no code made true. This command is what makes
the stated period real.

```bash
uv run python manage.py purge_request_logs            # report only
uv run python manage.py purge_request_logs --commit   # delete
uv run python manage.py purge_request_logs --days 90  # try a stricter window
```

**Twelve months, not fourteen days.** The table is not an access log. Rows
exist to give `Account.acquisition_request` and `Subscription.subscribed_via`
their geo and language context, so a two-week window would blank that for
every account older than a fortnight and defeat the reason the rows are
kept. `RETENTION_DAYS` in the command module is the source of the period —
**the Privacy Policy quotes it, so changing one means changing the other.**

The delete is a hard delete, matching the erasure decision in SNOW-774: an
account deletion removes its rows outright rather than anonymising them, and
a retention sweep that only blanked columns would leave the two paths
disagreeing about what a spent row looks like.

Rows referenced by `Account.acquisition_request` or
`Subscription.subscribed_via` are deleted like any other — both FKs are
`SET_NULL`, so the referring row survives with the pointer cleared. That is
intended: the account keeps its history, the identifiers behind it expire.
`BulletinShareClick.request` is `CASCADE` (SNOW-774), so a click goes with
the request context it consists of; it was `PROTECT`, which would have
aborted the whole nightly run on the first aged click.

Reported counts distinguish request rows from cascaded rows — `delete()`
returns a total that includes both, and reporting that total would overstate
the purge in the one log line an auditor would read.

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

- One `Bulletin` + `RegionBulletin` + `RegionDayRating` per
  CH MicroRegion for 2026-04-08 (the map-coverage layer), plus full April 2026
  for CH-4115 (Martigny-Verbier) — 178 of each model.
- All bulletins carry render models at `RENDER_MODEL_VERSION` (day ratings via the
  production `apply_bulletin_day_ratings` service); no rebuild step is needed.
- A small standalone `Location` / `Favourite` set.
- The two named dev accounts (a superuser and an active, CH-4115-subscribed
  normal user that owns the favourites — folded in from the former
  `seed_dev_users` command). Credentials: [`docs/worktrees.md`](worktrees.md).
  `seed_test_data --include user` seeds just the accounts.

The canonical preview URL after seeding is `/ch-4115/martigny-verbier/2026-04-08/`.

Read-only by default (prints intended counts); `--commit` persists. It refuses to
run when `DEBUG=False` and expects an empty/migrated DB — it creates deterministic
bulletin IDs, so re-seeding a populated DB raises a clean `CommandError`. Exactly one selection flag is required:

- `--all` — seed every model.
- `--include MODEL [MODEL ...]` — seed only the named model(s).
- `--exclude MODEL [MODEL ...]` — seed everything except the named model(s).

Model names are case-insensitive and strongly typed against a `SeedModel`
enumeration (`bulletin`, `regionbulletin`, `regiondayrating`,
`bulletingrouping`, `location`, `favourite`, `user`); an empty or unknown value
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
([`docs/locations.md`](locations.md)); an out-of-band resolution pass
resolves it via `fetch_elevation`. That makes it a **check on the curation**: compare a resolved
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

### `dump_locations_sheets` — write the location estate back to its sheets

The other half of `import_locations`, and what makes an edit durable. The
in-map location editor (`/?edit=locations` — SNOW-755) and any hand edit
write to **this environment's database only**; until the sheets under
`apps/locations/data/` carry the change, the next `import_locations`
reconciliation would delete it. This command renders both sheets from the
current rows, prints a per-file `+added/-removed` summary, and writes only
under `--commit`.

Three things about the emitted shape matter:

- **`note` is carried forward, not derived.** It is a sheet column with no
  database column behind it — a curator's working note, which
  `import_locations` reads and discards. The dump reads the notes off the
  sheet already on disk, keyed by uuid, and writes them back. A location
  the sheet has never seen gets an empty note.
- **`elevation_m` is never written**, for the reason `import_locations`
  never reads one: it is derived.
- **Only `named()` locations are dumped** — the same boundary
  `import_locations`'s `delete` mode respects.

Rows are ordered by uuid (links by the location's, then the resort's), so
two consecutive runs produce identical files and a `git diff` shows only
what actually changed. The round trip is the property to check after a
curation session: `dump_locations_sheets --commit`, then
`import_locations` must report **no changes**.

```bash
uv run python manage.py dump_locations_sheets            # preview diff only
uv run python manage.py dump_locations_sheets --commit   # write both sheets
uv run python manage.py dump_locations_sheets --commit \
    --file /tmp/locations.tsv --links-file /tmp/resort_locations.tsv
```

### `fetch_weather` — today's Open-Meteo forecast for every active location

Rebuilt by SNOW-759. One pass over one anchor: it walks
`Location.objects.active()` — a location reachable from a `ResortLocation`,
a `MicroRegion.centroid_location` or a `Favourite` — and writes one
`Weather` row per location, for today.

**An observation-only location is excluded.** A field report must not mint a
billable forecast call, and must never raise a forecast panel on a public
surface. The boundary is `LocationQuerySet.active()`, asserted in
`tests/locations/test_models.py`.

The two passes this replaces (a region pass over `MicroRegion.centre` and a
point pass over a quantised `ForecastCell` grid) resolved to the same places
by two routes, so `--skip-points` is gone with the second pass and
`--add-history` with the history table — the `Weather.forecast` column now
does that job inside the row.

**Historical days are not this command's business.** A day is written once,
on the day it is current, and `upsert_weather` then refuses to rewrite it.
Filling a day this command missed is a backfill against the archive
endpoint (SNOW-731), a different job with a different upstream.

Streams the estate through `iterate_rows`, so stdout reads as a countdown.
A per-location failure is logged and counted, never aborts the batch, and
makes the command exit non-zero.

Before its first useful run in an environment, every micro-region needs a
centroid `Location` — see
[`runbooks/region-centroid-backfill.md`](runbooks/region-centroid-backfill.md),
which also carries the Open-Meteo cost this command's cadence implies.

```bash
uv run python manage.py fetch_weather           # preview (calls the API)
uv run python manage.py fetch_weather --commit  # persist
```

### `link_region_centroid_locations` — anchor each region to a Location

Gives every `MicroRegion` with a `boundary` a `Location` at that region's
centroid, which anchors the region in the location estate (SNOW-696).

**This runs on every deploy, from `build.sh` and `build_headless.sh`, and it
must.** `loaddata` resets every column the EAWS fixtures do not carry, and
none of them carries `centroid_location` — so each deploy NULLs all 461
links and orphans the `Location` rows behind them. That was silent data loss
until SNOW-771: the command reported success, and a deploy hours later undid
it with nothing in any log to say so. Re-running it immediately after
`loaddata` is what makes the wipe harmless.

**Wholly offline — no network at all.** The coordinate is
`centre_from_bbox(boundary)`, and the boundary is in the fixture; the
elevation is `MicroRegion.centroid_elevation_m`, resolved once by
`refresh_centroid_elevations` and committed. That is what makes a per-deploy
run affordable, and it removes the per-environment backfill entirely — no
environment pays for elevation lookups.

SNOW-765 established the coordinate half: `centre` was computed from the
same polygon by the fixture builders, so the two agree value-for-value
across all 461 L4 regions, a property
`tests/regions/management/commands/test_link_region_centroid_locations.py`
asserts against the committed fixtures.

A region whose fixture carries no elevation still links, with a null
`elevation_m` — weather needs a coordinate, not a height. A region whose
boundary cannot be read is logged, counted and skipped; it never aborts the
batch, and the command exits non-zero only on a genuine failure.

**A centroid is not a place anyone goes.** The minted location carries no
name and no kind: it represents the region and sits at whatever elevation
the polygon's middle happens to fall at. Any surface showing it must say
which elevation it represents.

Linking a region *does* have a recurring cost, just not here:
`fetch_weather` walks `Location.objects.active()`, which includes every
`centroid_location`, four times a day. Sizing is in
[`runbooks/region-centroid-backfill.md`](runbooks/region-centroid-backfill.md).

```bash
uv run python manage.py link_region_centroid_locations           # preview
uv run python manage.py link_region_centroid_locations --commit  # apply
```

### `prune_orphan_locations` — sweep anonymous Locations nothing points at

Cleanup for the orphans SNOW-771 left behind. Before its reuse fix, each
deploy's re-link minted a fresh centroid `Location` instead of rebinding to
the existing one, so every deploy stranded 461 rows and their `Weather`.
Staging accumulated three generations in a day.

Only an **anonymous** location with no `ResortLocation`, `MicroRegion`,
`Favourite` or `FieldObservation` is a candidate. A named location is
curated data owned by `import_locations` and is never touched, even when
nothing references it — an unreferenced curated place is a curation
question, not garbage. Deleting a `Location` cascades to its `Weather`,
which is the point: that data describes a place nothing can reach.

```bash
uv run python manage.py prune_orphan_locations           # preview
uv run python manage.py prune_orphan_locations --commit  # delete
```

### `link_resort_locations` — give every geocoded resort weather

A resort's pin and a resort's weather used to be unconnected. The
edit-resorts map overlay writes `Resort.latitude`/`longitude` and never
touches `Location`; the resort page's weather section reads
`ResortLocation` links, which only the separate edit-locations overlay
creates. So a resort could sit on the map for months with a hand-placed pin
and show no weather at all — production had **115 geocoded resorts and 4
links**.

This gives a geocoded resort with no link an anonymous `Location` at its own
coordinate, height from `base_elevation_m`, marked `is_primary`. Curating
named village / mid / peak points stays worthwhile and stays the editor's
job; this is the floor, not a replacement.

`role` is left blank on purpose: BASE/MID/TOP are claims about what a point
*is*, and a hand-placed pin is only "where this resort is" — `is_primary`
already carries that.

Offline (no Open-Meteo call) and idempotent, so `build.sh` and
`build_headless.sh` both run it on every deploy. It reuses an existing
anonymous `Location` at the coordinate rather than minting a new one, for
the reason SNOW-771 records — a fresh row each deploy would orphan the
previous one and every `Weather` row hanging off it.

```bash
uv run python manage.py link_resort_locations           # preview
uv run python manage.py link_resort_locations --commit  # apply
```

### `refresh_centroid_elevations` — resolve centroid heights into the fixtures

The one manual half of the above, and the only part of a centroid that
cannot be derived offline. Fills `centroid_elevation_m` on every L4 entry in
the four committed EAWS fixtures, one Open-Meteo elevation call per
unresolved region.

Run by a **developer against the committed fixtures**, not per environment —
that is the whole point. Every environment then gets the elevations from the
fixture for nothing. Commit the changed files.

Re-runnable: an entry that already has a value is skipped, so an interrupted
run costs only what it had not reached. Pass `--force` after a fixture
rebuild moves a boundary — a moved centroid leaves a stale elevation behind
and nothing else would notice.

The test suite fails if any region with a boundary is left without an
elevation, so a half-finished run cannot ship.

```bash
uv run python manage.py refresh_centroid_elevations                    # preview
uv run python manage.py refresh_centroid_elevations --commit           # resolve missing
uv run python manage.py refresh_centroid_elevations --commit --force   # re-resolve all
```

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
one would mean an Open-Meteo round trip per historical report for a figure
nothing renders.

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

### `sync_from_production` — refresh staging's data from production

Copies the provider-derived tables out of the **production** database into
the local one (SNOW-729). Staging has no scheduler and no task worker, so it
never ingests a bulletin of its own; this is how it gets data. Runs unattended as the `snowdesk-staging-data-sync` Render cron
job at 07:20 UTC.

Copies `Bulletin`, `RegionBulletin`, `RegionDayRating`, `BulletinGrouping`,
and the curated resort estate — `Resort`, `ResortLocation`, and the
`Location` rows a `ResortLocation` references.

Copies **no user data** — no `auth_user`, `Account`, passkeys, push
subscriptions, favourites, observations, routes, request logs or bulletin
shares. That is what makes it safe to run unattended with no anonymisation
step, and it matters because staging sends email inline (`ImmediateBackend`).
`PipelineRun` is also excluded: it is telemetry about production's own ingest
runs, and `Bulletin.pipeline_run` is nullable.

`locations.Location` is the one **mixed** table — a resort's village/mid/peak
sits alongside a row per saved favourite and per field report — so the plan
restricts it to rows a `ResortLocation` points at. The filter is a subquery
inside production, so a user's saved position is never read at all.

Every table upserts on its own natural key and foreign keys are translated
through id maps, so primary keys need not (and do not) match across the two
databases. `Resort`, `Location` and `ResortLocation` declare no domain-unique
field, so they key on `BaseModel.uuid`, which nothing ever reassigns.
Re-running is a no-op.

```bash
# Read-only: report what would be copied, write nothing.
uv run python manage.py sync_from_production

# First load into an empty staging database — --all is required.
uv run python manage.py sync_from_production --all --commit

# What the cron job runs: the last seven days of changes.
uv run python manage.py sync_from_production --commit

# Narrow to one table while triaging.
uv run python manage.py sync_from_production --all --only bulletins.Bulletin -v 2
```

Read-only by default; `--commit` persists. `--since-days N` sets the
`updated_at` window (default 7), `--all` removes it, `--only APP.MODEL`
(repeatable) limits the run. Respects `--verbosity`.

Requires `PRODUCTION_DATABASE_URL` pointing at a **read-only** production
role; `config/settings/staging.py` is the only settings module that turns it
into a connection, so the command cannot run from production's own settings.
Exits non-zero when any row is skipped for an unresolvable foreign key — a
partial copy is a failure, not a warning. Full setup and triage:
[`runbooks/refresh-staging-from-production.md`](runbooks/refresh-staging-from-production.md).

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

- `fetch_bulletins --source <src> --start-date <YYYY-MM-DD> --commit` —
  to backfill bulletins after a multi-day outage. Add `--delay 5` for
  multi-year backfills to stay polite to the public APIs.
- `audit_resort_regions --commit` — after editing resort coordinates or
  region polygons; refixes FKs and rewrites the resort fixture.

  Flags: `--commit`, `--delay SECONDS` (default 1.0).

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

---

## Local DB bootstrap

Two paths for seeding a fresh local development database:

### `bin/bootstrap-dev-db` — one-command seed (mirrors required)

Runs migrate, loads all region/resort fixtures, and fetches bulletins from
all three providers — via the local mirrors served by the running dev
server.

**Prerequisite:** the Django dev server must already be running on :8000
before you execute this script. The SLF and ALBINA local mirrors are
served by dev-only views in the running server.

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
