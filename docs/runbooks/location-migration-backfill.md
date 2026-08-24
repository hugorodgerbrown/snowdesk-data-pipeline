---
name: location-migration-backfill
description: Backfill an environment onto the Location model — the five --commit commands, their Open-Meteo cost, and the per-environment progress log
status: current
last-reviewed: 2026-08-24
---

# Runbook — bring an environment onto the Location model

## When this applies

Once the location-migration stack (SNOW-696, 700, 701, 702, 703, 704, 708,
709) has deployed to an environment. The migrations run themselves —
`bin/build.sh` runs `migrate` on every Render deploy — but they only add the
columns. **Every row that fills them is written by a `--commit`-gated
management command an operator runs by hand**, per
[`dry-run-default-commands`](../decisions/dry-run-default-commands.md).

Until these commands run, the new surfaces render nothing: the resort page
shows no per-location forecast, and the bulletin page falls back to the
one-day `WeatherSnapshot` masthead rather than the multi-day panel. Nothing
breaks — `_region_forecast_panel()` returns `None` when a region has no
`centroid_location` — it just stays dark.

## Order

The five commands are independent except for one edge: `import_locations`
must precede `link_location_forecast_cells`, which resolves what the import
wrote. Everything else can run in any order.

`prune_forecast_points` is **not** scheduled — it only ever runs by hand — so
no cell minted here is at risk of being pruned between steps.

## Cost

Only two of the five call Open-Meteo, one call per row:

| Command | Calls | Notes |
|---|---|---|
| `backfill_favourite_locations` | 0 | copies the cell the favourite already had |
| `backfill_observation_locations` | 0 | observations get no cell — no forecast panel to fill |
| `import_locations` | 0 | reads two TSVs |
| `link_location_forecast_cells` | 1 per curated location | 4 today |
| `link_region_centroid_locations` | 1 per region | **461** |

⚠️ `link_region_centroid_locations` is the one to think about before
committing. It mints up to 461 forecast cells, and **each new cell is one
extra Open-Meteo call per `fetch_weather` cycle, four times daily** —
roughly 1,800 additional calls per day, forever. Check the environment's
Open-Meteo plan first: the free tier allows 10,000/day per IP shared across
elevation, forecast and archive. The dry run reports the created-vs-reused
split; its created figure is an **upper bound**, because a preview writes
nothing, so two nearby region centres each count as new where the commit run
would have the second reuse the first.

Check which tier an environment is on by reading the URL in the elevation
debug log, or `OPEN_METEO_API_BASE_URL` in its Render env group.
`customer-api.open-meteo.com` is the paid tier; `api.open-meteo.com` is free.

## Steps

Run from the Render Shell of the environment's web service —
`snowdesk-staging` or `snowdesk-website` (`render ssh <service>`, or the
dashboard Shell tab). Every command is read-only without `--commit`; run it
bare first and read the plan before committing.

```bash
# 0. Confirm the migrations landed.
uv run --no-sync python manage.py showmigrations \
    locations regions favourites observations weather

# 1. One Location per existing favourite.
uv run --no-sync python manage.py backfill_favourite_locations --commit

# 2. One Location per existing field observation.
uv run --no-sync python manage.py backfill_observation_locations --commit

# 3. The curated estate — four villages and their resort links.
uv run --no-sync python manage.py import_locations --commit

# 4. Resolve those locations' own elevation and shared cell.
uv run --no-sync python manage.py link_location_forecast_cells --commit

# 5. Anchor each micro-region to a centroid Location. READ THE COST ABOVE.
uv run --no-sync python manage.py link_region_centroid_locations --commit

# 6. Populate weather for the newly-active cells.
uv run --no-sync python manage.py fetch_weather --commit
```

### Step 4 doubles as a curation check

`link_location_forecast_cells` logs each location's resolved elevation. It
should match the figure in that row's `note` in
`apps/locations/data/locations.tsv`. A height far off means the coordinate
is mis-pinned, and it shows up here rather than on the resort page. As of
2026-08-24 the four curated villages resolve to Verbier 1494 m, Thyon 2144 m,
Silvaplana 1815 m, Sils-Maria 1805 m — all matching their notes.

### Step 6 differs per environment

**Production** runs `snowdesk-scheduler`, which fires `fetch_weather` at
00/06/12/18 UTC. New cells are picked up by the next cycle on their own, so
step 6 is optional there — it only front-runs the wait.

**Staging has no scheduler**, so nothing populates weather unless step 6 is
run by hand. Without it the panels stay empty even though every location and
cell exists.

⚠️ **Always pin `--date` on staging.** With no `--date`, `fetch_weather`
derives its start from the latest stored `WeatherSnapshot` and runs to today
— and because staging only ever fetches when someone runs it by hand, that
gap is however long it has been since the last run. Each day in the window
costs one paced archive call per micro-region, so a two-week gap is
~7,000 calls at 1s each: over two hours. Pinning `--date` to today fetches
one day of region archive plus the whole active-cell forecast pass, which is
all a freshly-backfilled environment needs.

The point pass only runs when the window reaches today, so a window that
ends in the past populates no forecast cells at all.

## Verification

A resort with a curated location (Verbier, Thyon, Silvaplana, Sils-Maria)
should show a per-location forecast on its resort page. A bulletin page for
a current or future date should show the multi-day forecast panel rather
than the one-day masthead — note the panel is gated on
`target_date >= today`, so a historical bulletin correctly shows nothing.

## Progress log

Record each environment as it is done, so a resumed run knows where it is.

### Staging — 2026-08-24

| Step | Result |
|---|---|
| 0. migrations | ✅ all five leaves applied |
| 1. favourites | ✅ 14 minted, 0 failed |
| 2. observations | ✅ 3 minted, 0 failed |
| 3. import_locations | ✅ 4 locations, 4 links added |
| 4. link_location_forecast_cells | ✅ 4 resolved, 3 new cells; elevations match notes |
| 5. link_region_centroid_locations | ✅ 461 linked, 0 failed; 461 new cells, 0 reused |
| 6. fetch_weather | ⬜ not started |
| verification | ⬜ not started |

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
Confirm production's own `OPEN_METEO_API_BASE_URL` before step 5; the
`Production` and `Staging` env groups are independent.

| Step | Result |
|---|---|
| cut release | ⬜ not started |
| 0. migrations | ⬜ not started |
| 1. favourites | ⬜ not started |
| 2. observations | ⬜ not started |
| 3. import_locations | ⬜ not started |
| 4. link_location_forecast_cells | ⬜ not started |
| 5. link_region_centroid_locations | ⬜ not started |
| 6. fetch_weather | ⬜ optional — the scheduler picks new cells up |
| verification | ⬜ not started |

## Not part of this runbook

Dropping the superseded coordinate columns from `Favourite` and
`FieldObservation` (SNOW-714) and retiring `Resort.forecast_point`
(SNOW-715) are separate tickets that must not ship with their backfills —
`build.sh` auto-migrates, so a drop lands the moment its PR merges, before
any operator can run the command that fills its replacement.
