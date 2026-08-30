---
name: location-migration-backfill
description: Backfill an environment onto the Location model — the --commit commands, Open-Meteo elevation cost, per-environment progress log
status: current
last-reviewed: 2026-08-30
---

# Runbook — bring an environment onto the Location model

## When this applies

Once the location-migration stack (SNOW-696, 700, 701, 702, 703, 704, 708,
709) has deployed to an environment. The migrations run themselves —
`bin/build.sh` runs `migrate` on every Render deploy — but they only add the
columns. **Every row that fills them is written by a `--commit`-gated
management command an operator runs by hand**, per
[`dry-run-default-commands`](../decisions/dry-run-default-commands.md).

> **Reduced by SNOW-762 (2026-08-30).** The weather app was stripped, so
> three of the original five steps are gone with it:
> `backfill_favourite_locations` and `link_location_forecast_cells` were
> deleted, and `fetch_weather` no longer exists. What remains is the
> location estate itself. `link_region_centroid_locations` survives with
> its forecast-cell half removed, so its cost is now one elevation call per
> region and nothing recurring.
>
> **Superseded in part by SNOW-759 (2026-08-30).** `fetch_weather` exists
> again, and `link_region_centroid_locations` is now a step with a
> recurring cost after all — the centroid `Location`s it mints are what the
> rebuilt fetch walks. Its own procedure and cost sizing live in
> [`region-centroid-backfill.md`](region-centroid-backfill.md); run that
> one for the weather estate and this one for the location estate.

## Order

The three remaining commands are independent — the ordering edge
(`import_locations` before the cell resolution) went with the deleted
command.

## Cost

One of the three calls Open-Meteo, one call per row:

| Command | Calls | Notes |
|---|---|---|
| `backfill_observation_locations` | 0 | reads existing rows |
| `import_locations` | 0 | reads two TSVs |
| `link_region_centroid_locations` | 1 per region | **461**, one-off |

`link_region_centroid_locations` is a one-off cost, not a recurring one:
it resolves each region's centroid elevation once and stores it. The free
tier allows 10,000/day per IP, so a full run fits inside a day's
allowance with room to spare. The `--delay` default (1.0s) paces it.

Check which tier an environment is on by reading
`OPEN_METEO_API_BASE_URL` in its Render env group.
`customer-api.open-meteo.com` is the paid tier; `api.open-meteo.com` is free.

## Steps

Run from the Render Shell of the environment's web service —
`snowdesk-staging` or `snowdesk-website` (`render ssh <service>`, or the
dashboard Shell tab). Every command is read-only without `--commit`; run it
bare first and read the plan before committing.

```bash
# 0. Confirm the migrations landed.
uv run --no-sync python manage.py showmigrations \
    locations regions favourites observations

# 1. One Location per existing field observation.
uv run --no-sync python manage.py backfill_observation_locations --commit

# 2. The curated estate — four villages and their resort links.
uv run --no-sync python manage.py import_locations --commit

# 3. Anchor each micro-region to a centroid Location.
uv run --no-sync python manage.py link_region_centroid_locations --commit
```

### Step 3 doubles as a curation check

`link_region_centroid_locations` logs each region's resolved elevation, and
`import_locations` rows should match the figure in that row's `note` in
`apps/locations/data/locations.tsv`. A height far off means the coordinate
is mis-pinned. As of 2026-08-24 the four curated villages resolve to
Verbier 1494 m, Thyon 2144 m, Silvaplana 1815 m, Sils-Maria 1805 m — all
matching their notes.

## Verification

Every micro-region with a `centre` should carry a `centroid_location`, and
the curated villages should exist as named `Location` rows with their
resort links. There is no user-facing surface to check until SNOW-761
builds the weather surfaces back.

## Progress log

Record each environment as it is done, so a resumed run knows where it is.

### Staging — 2026-08-24

*A record of what ran on the day, against the pre-SNOW-762 six-step
sequence. The steps referencing forecast cells and `fetch_weather` no
longer exist; the location rows they were run alongside do.*

| Step | Result |
|---|---|
| 0. migrations | ✅ all five leaves applied |
| 1. favourites | ✅ 14 minted, 0 failed |
| 2. observations | ✅ 3 minted, 0 failed |
| 3. import_locations | ✅ 4 locations, 4 links added |
| 4. link_location_forecast_cells | ✅ 4 resolved, 3 new cells; elevations match notes |
| 5. link_region_centroid_locations | ✅ 461 linked, 0 failed; 461 new cells, 0 reused |
| 6. fetch_weather | ✅ `--date` pinned to today; 4290 created, 0 failed |
| verification | ✅ both surfaces render |

Verified on staging 2026-08-24: Thyon's resort page shows the labelled
per-location panel (`THYON 2000 · 2144 M`) where uncurated Nendaz still
shows the unlabelled legacy panel, and `/ch-4115/` shows
`WEATHER — REGION CENTRE` with its seven-day panel. The region panel renders
in the no-bulletin empty state too, which is what makes this verifiable
out of season.

Thyon reads 18°/11° for the day where Nendaz reads 24°/16° — same domain,
same "shared 4 Vallées figures" note, different weather because 2144 m is
not 1400 m. That gap is what the single unlabelled panel used to hide.

The region-centroid preview's upper bound was exact: every one of the 461
centroids minted its own cell, none reused an existing resort cell. A
micro-region's centroid is the geometric centre of a large polygon, so it
rarely lands within both the 750 m and 150 m reuse thresholds of a resort.
Staging's active cell count is now ~544, and each one is an Open-Meteo call
per fetch cycle.

Staging is on the **paid** Open-Meteo tier
(`customer-api.open-meteo.com`), so the free-tier quota does not constrain
step 5 there.

### Production — not started

Production deploys from `release`, which is behind `main` — **cut a release
first** (`bin/cut-release`, and see [`deployment.md`](../deployment.md)).
Confirm production's own `OPEN_METEO_API_BASE_URL` before step 3; the
`Production` and `Staging` env groups are independent.

Hold this until the whole SNOW-757 sequence has landed on `main`: a
release cut mid-rebuild would take a weather-less site to production,
which is the one thing the staged landing exists to avoid.

| Step | Result |
|---|---|
| cut release | ⬜ not started |
| 0. migrations | ⬜ not started |
| 1. observations | ⬜ not started |
| 2. import_locations | ⬜ not started |
| 3. link_region_centroid_locations | ⬜ not started |
| verification | ⬜ not started |

## How a curated edit reaches an environment

The commands above backfill an environment onto the model. Growing the
curated estate afterwards — the ~93 summits SNOW-732 needs — is a
different loop, and it does **not** run against production directly.

0. **Start from the sheets.** `import_locations --commit` locally, before
   opening the editor, so the database already holds every row the sheets
   carry. `dump_locations_sheets` renders each sheet **whole** from the
   database — it is a replacement, not a merge — so a database missing
   rows the sheet lists will delete them at step 2. A local database
   seeded from fixtures or by `seed_test_data` does *not* hold the curated
   estate: it can carry its own unrelated `Location` rows, which is the
   case that bites, because the dump then looks like it worked.
1. **Curate locally.** `/?edit=locations` as a superuser (SNOW-755): click
   the map to place a summit, name it, classify it, and link it to every
   resort that reaches it. The editor writes to the local database only.
2. **Write it back to git.** `dump_locations_sheets --commit`, then
   `git diff apps/locations/data/` and commit. Until this runs, the edit
   exists in one database and nowhere else.
3. **Prove the loop closed.** `import_locations` must report **no
   changes** against the sheets just written. If it does not, the dump and
   the database disagree and the difference is what would be silently
   reverted on the next reconciliation.
4. **Apply it to each environment.** `import_locations --commit` by hand
   on staging, then production, after the branch has deployed. It is not
   in `build.sh`, for the same reason `import_resorts` is not: these rows
   are editable data owned by each environment's database, and a deploy
   that re-imported them would discard admin edits.
5. **Resolve the new rows.** `link_location_forecast_cells --commit` —
   one Open-Meteo call per new location, and the check on the curation
   (step 4 above).

**The dry run is the guard on step 0.** `dump_locations_sheets` with no
`--commit` reports each sheet's change as `+added/-removed` lines. A
removed count you cannot account for means the database is behind the
sheets — stop and run step 0, rather than committing the diff. Observed
on 2026-08-29 against a worktree database holding two unrelated locations
where the sheet held four curated villages: the dry run read
`locations.tsv would change (+2/-4 lines)`, which is the whole warning
you get.

The editor is superuser-gated and could in principle be driven against
production, but curating there skips steps 2 and 3 entirely: the estate
would drift from the sheets, and the next `import_locations --commit`
would delete every location the sheets do not list. Curate locally.

## Not part of this runbook

Dropping the superseded coordinate columns from `Favourite` and
`FieldObservation` (SNOW-714) and retiring `Resort.forecast_point`
(SNOW-715) are separate tickets that must not ship with their backfills —
`build.sh` auto-migrates, so a drop lands the moment its PR merges, before
any operator can run the command that fills its replacement.
