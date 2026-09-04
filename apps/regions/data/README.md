# apps/regions/data

Hand-curated source data for the `regions` app. Unlike `reference_data/`
(vendored third-party files — EAWS regions, Météo-France massifs) these
files are maintained by us.

## `resorts.tsv`

**The** file that describes a resort (SNOW-817): the editorial columns —
operator, website, elevations, lift/run counts, piste length, typical
season dates and free-text `notes` — plus the coordinate pair and that
coordinate's provenance. Tab-separated, one row per resort, keyed by
`uuid`.

It is **not** a fixture and is never loaded on deploy. The round trip is:

```bash
uv run python manage.py import_resorts            # preview sheet -> DB
uv run python manage.py import_resorts --commit   # apply it
uv run python manage.py dump_resorts_sheet        # preview DB -> sheet
uv run python manage.py dump_resorts_sheet --commit  # write it back
```

`dump_resorts_sheet` is what carries a coordinate placed in the in-map
editor back into git. Before SNOW-817 that direction did not exist, so 77
pins lived only in the retired `resorts.json` fixture and the sheet
disagreed with the database about most of them.

`import_resorts` reconciles in three modes, all selected by default:
`add` creates resorts the sheet lists but the database lacks, `update`
overwrites the editorial fields of ones it has, and `delete` removes any
resort the sheet does not list. Pick a subset with `--mode`, e.g.
`--mode update` to refresh fields without creating or deleting anything.

The sheet's *live* set is every row whose `status` column is blank, so
`delete` removes both the `NOT_A_SKI_RESORT` rows and any resort missing
from the export altogether. That second half has teeth: a resort created
in the admin and not yet dumped here is deleted by the next full run. Run
`dump_resorts_sheet --commit` after adding one, or reconcile with
`--mode add update`.

`status` accepts only a blank cell or `NOT_A_SKI_RESORT`; anything else is
an error, not a silent "live". It had been a prefix on the old `note`
column, which meant one cell carried both a curator's prose and a
machine-read verdict — and no note could begin with those characters
without deleting the resort. The prose half is now `notes`.

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
(SNOW-544). It also carries `latitude` and `longitude`, so a row can
arrive with its pin already placed instead of needing one added by hand
afterwards. The pair is optional: a row that omits both still creates a
resort, one with no pin, which then needs placing in the edit-resorts
panel before it appears on the map. Supplying only one of the two is an
error rather than a single-axis value.

It also carries the coordinate's provenance — `geocode_source`,
`geocode_confidence` and `needs_review` — so the sheet can record that a
pin was placed by an operator in the map editor and reviewed, rather than
having every import demote it to `IMPORT`/`needs_review`. A row with a
coordinate but no `geocode_source` still gets that cautious stamp, which
is the honest record for a coordinate somebody typed in.

All of these are **creation-time values only** — `import_resorts` reads
them when it creates a row and never writes them again. That carve-out is
what makes the sheet safe to re-run: a resort re-pinned in the map editor
owns its own position, region and provenance afterwards, and a later
import cannot drag it back to whatever the sheet happened to say. The way
a re-pin *does* travel is `dump_resorts_sheet` → commit → a fresh
database's first import.

A sheet-supplied coordinate is stamped `geocode_source="IMPORT"` with
`needs_review=True`, never `MANUAL`. The edit-resorts panel's `MANUAL` /
`geocode_confidence=1.0` / `needs_review=False` stamp records that an
operator placed that pin on a map, which is not true of a coordinate that
arrived as data — so imported rows stay flagged for a confirming pass, and
the panel re-stamps one the first time it is saved.

Each environment's database is the source of truth for `Resort` once the
import has run — later edits happen in the admin or the map editor, not
here. Re-running the import re-asserts the sheet over those edits, so read
the dry-run diff before passing `--commit`.
