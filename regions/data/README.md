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

The export is deliberately partial. It carries no geocoding columns —
coordinates are placed on the map and owned by the database. `region` and
`canton` are absent too, and both are required to create a resort, so
`add` currently reports any row that would need creating instead of
guessing. Add those two columns to the export to enable it.

Each environment's database is the source of truth for `Resort` once the
import has run — later edits happen in the admin or the map editor, not
here. Re-running the import re-asserts the sheet over those edits, so read
the dry-run diff before passing `--commit`.
