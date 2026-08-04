---
name: resorts-are-editable-data
description: Resort rows are editable per-environment data applied by import_resorts, not a fixture reloaded on deploy; resorts.json seeds local/CI only
status: current
last-reviewed: 2026-08-04
---

# Resorts are editable data, not deploy-time reference data

**Decision.** `apps/regions/fixtures/resorts.json` is **not** in the `loaddata`
list in `build.sh` / `build_headless.sh`. Each environment's database owns
its `Resort` rows; they are edited in the admin and the map editor. Bulk
editorial changes are curated in `apps/regions/data/resorts.tsv` and applied by
hand with `manage.py import_resorts --commit`. The fixture's only remaining
job is seeding a fresh local or CI database (`bin/init-worktree`,
`tests/seeding.py`, `seed_test_data`), refreshed from a local DB by
`dump_resorts_fixture --commit`.

The four `eaws_*.json` region fixtures and `region_aliases.json` are
unaffected — they stay in the deploy-time `loaddata` list.

**Why.** The two fixture sets have opposite lifecycles. EAWS regions are
reference data: their identifiers and geometry come from an upstream source,
nobody edits them in production, and reloading them on every deploy is how
an upstream correction ships. Resorts are the opposite — operator names,
season dates and coordinates are curated by us, and coordinates in
particular are placed interactively on the map. Reloading `resorts.json` on
every deploy made the fixture authoritative in production, so any edit made
there was silently reverted by the next deploy. That is the wrong direction
for data we want to keep editing.

Applying the sheet by hand also keeps a destructive change visible: the
import deletes rows the sheet retires, and an operator should see the
dry-run diff before that runs against production, not discover it in a
deploy log.

**Consequences.**

- A resort change reaches production in one of two ways: an admin/map edit
  made directly there, or a sheet edit plus a manual
  `import_resorts --commit` run against that environment. A deploy alone
  changes nothing.
- The fixture and production will drift, by design. The fixture is a
  snapshot for seeding, not a mirror; don't add a check that asserts they
  match.
- `import_resorts --mode delete` (on by default) removes any resort the
  sheet does not list, not only the ones it marks `NOT_A_SKI_RESORT`. A
  resort created in the admin must therefore be re-exported to the sheet,
  or reconciled with `--mode add update`, or the next full run deletes it.
  The dry-run names every deletion — read it before `--commit`.
- `add` needs `region` and `canton`. The sheet gained both columns in
  SNOW-544, so `add` works: the first production run (2026-08-04) reported
  `0 to add` because every live sheet row already matched a `uuid`, not
  because the mode was unusable. A row still missing either column is
  reported as an error rather than guessed at, and the fix is to fill the
  sheet — not to create the resort in the admin first.
- The sheet carries **no coordinates**, by design. `import_resorts` never
  writes `latitude`/`longitude`, so an added resort arrives ungeocoded and
  gets no `ForecastPoint` until someone places it in the map editor. That is
  also why a coordinate placed on production cannot travel back to the
  fixture through the sheet — `dump_resorts_fixture` reads the *local* DB.
- Deleting a resort is safe for user data: `Favourite.resort` is
  `SET_NULL`, so a favourite made from a deleted resort degrades to a plain
  pin with its snapshotted name, coordinates and region intact. It does
  leave the resort's `ForecastPoint` unreferenced — run
  `prune_forecast_points --commit` after a bulk deletion to clear those and
  their cascaded weather rows (SNOW-633).
