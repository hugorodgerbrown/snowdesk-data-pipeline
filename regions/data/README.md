# regions/data

Hand-curated source data for the `regions` app. Unlike `reference_data/`
(vendored third-party files — EAWS regions, Météo-France massifs) these
files are maintained by us.

## `resorts.tsv`

The curation surface for `Resort`'s editorial columns — operator, website,
elevations, lift/run counts, piste length, typical season dates, and a
free-text curator note. Exported from the working spreadsheet as
tab-separated values, one row per resort, keyed by `uuid`.

It is **not** a fixture and is never loaded on deploy. Apply it with:

```bash
uv run python manage.py import_resorts           # preview the changes
uv run python manage.py import_resorts --commit  # apply them
```

`import_resorts` reconciles in three modes, all selected by default:
`add` creates resorts the sheet lists but the database lacks, `update`
overwrites the editorial fields of ones it has, and `delete` removes any
resort the sheet does not list. Pick a subset with `--mode`, e.g.
`--mode update` to refresh fields without creating or deleting anything.

The sheet's *live* set is every row whose `note` does **not** start with
`NOT_A_SKI_RESORT`, so `delete` removes both the marked rows and any
resort missing from the export altogether. That second half has teeth: a
resort created in the admin and not yet re-exported here is deleted by the
next full run. Re-export the sheet after adding one, or reconcile with
`--mode add update`.

`kind` is a separate axis from that marker, and the two do not interact
(SNOW-544). `NOT_A_SKI_RESORT` means *delete this row*; `kind` says what a
**live** row is — `RESORT` (the default, and what a blank cell means) or
`TOURING_TERRAIN` for real avalanche terrain with no lifts: passes, side
valleys and glacier basins the sheet accumulated back when it was one row
per micro-region. Touring rows stay in the database and out of every
surface that presents its rows as resorts — the map's resort layer,
`/api/resorts-by-region/`, the bulletin page's resort list, and the MCP
`list_resorts_in_region` tool. An unrecognised `kind` is an error rather
than a fallback to `RESORT`, because a typo silently becoming a resort is
the failure the column exists to prevent.

The export carries `region` (a `MicroRegion.region_id`) and `canton`,
which are both required to create a resort — so `--mode add` works
(SNOW-544). They are creation-time values only: `import_resorts` never
overwrites them on a row that already exists, because a resort moved in
the map editor owns its own region afterwards.

It still carries **no geocoding columns**. Coordinates are placed on the
map and owned by the database, so a row added by `--mode add` arrives
without them and needs a pin placing in the edit-resorts panel before it
appears on the map.

Each environment's database is the source of truth for `Resort` once the
import has run — later edits happen in the admin or the map editor, not
here. Re-running the import re-asserts the sheet over those edits, so read
the dry-run diff before passing `--commit`.
