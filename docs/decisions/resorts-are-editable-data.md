---
name: resorts-are-editable-data
description: resorts.tsv is the only resort file — import_resorts applies it, dump_resorts_sheet writes it back, no deploy loads it; resorts.json retired
status: current
last-reviewed: 2026-09-04
---

# Resorts are editable data, and one file describes them

**Decision.** `apps/regions/data/resorts.tsv` is the only file that describes
a resort. `import_resorts --commit` applies it to a database;
`dump_resorts_sheet --commit` writes it back from one. No deploy runs either
— each environment's database owns its `Resort` rows, edited in the admin
and the in-map editor.

The four `eaws_*.json` region fixtures and `region_aliases.json` are a
different thing and are unaffected: they are upstream reference data, loaded
by an operator (see [`runbooks/reset-live-db.md`](../runbooks/reset-live-db.md)).

## Why one file (SNOW-817)

There used to be two. `apps/regions/fixtures/resorts.json` seeded local and
CI databases; `resorts.tsv` held the editorial columns a curator maintains
in a spreadsheet. Both described the same 164 rows, and neither was complete:

- **77 coordinates existed only in the fixture.** The map editor wrote to the
  database, and `dump_resorts_fixture` dumped the database to JSON. The sheet
  was maintained by hand and had no way to receive them — there was no DB →
  sheet direction at all. Verbier, Zermatt and Nendaz were among them.
- **The sheet's 38 coordinates were the `IMPORT`-stamped ones**, the subset
  that had originally come *from* the sheet. So the two files disagreed about
  the majority of pins, and the JSON was the only one that was right.
- **Provenance existed only in the fixture.** Nothing in the sheet could say
  a pin had been placed by an operator and reviewed.

Two partial descriptions of one table is a data-loss shape: whichever file a
reader picks, something is missing, and nothing tells them which. The merge
put every field the fixture carried into the sheet, added the missing
direction, and deleted the JSON.

`slug` is the one field that did not need a column: it is always
`slugify(name)`, there are no duplicate names, and `Resort.save()` mints it
on first save — so it regenerates identically. That is asserted by the
round-trip test rather than assumed.

## Why the sheet, and not the fixture

Production is where the latest data is produced. A curator edits the
spreadsheet; an operator places pins on the production map. The sheet is the
format a human edits, so it is the one that survives.

## Why no deploy loads it

Reloading `resorts.json` on every deploy used to make the fixture
authoritative in production, so any edit made there was silently reverted by
the next deploy. That is the wrong direction for data we want to keep
editing. Applying the sheet by hand also keeps a destructive change visible:
the import deletes rows the sheet retires, and an operator should see the
dry-run diff before that runs against production, not discover it in a deploy
log.

## The round trip

```
sheet  --import_resorts --commit-->  database  --dump_resorts_sheet --commit-->  sheet
```

An edit becomes durable — reaching other worktrees, CI and every other
environment's next reconciliation — only once it is back in the sheet and
committed. `tests/regions/management/commands/test_dump_resorts_sheet.py`
asserts the loop is a fixpoint on the real committed data: import the sheet,
dump it, get the same bytes.

## Consequences

- **A resort change reaches production** by an admin/map edit made directly
  there, or a sheet edit plus a manual `import_resorts --commit` against that
  environment. A deploy alone changes nothing.
- **`status` is the retirement marker**, in its own column. It is blank or
  `NOT_A_SKI_RESORT`; any other value is an error rather than a silent
  "live". It used to be a `NOT_A_SKI_RESORT` prefix on the `note` column, so
  one cell carried both a curator's prose and a machine-read verdict — and no
  note could begin with those characters without deleting the resort. The
  prose half is now `notes`, which maps to `Resort.notes`.
- **`import_resorts --mode delete` (on by default) removes any resort the
  sheet does not list**, not only the ones it retires. A resort created in the
  admin must be re-exported with `dump_resorts_sheet`, or reconciled with
  `--mode add update`, or the next full run deletes it. The dry-run names
  every deletion — read it before `--commit`.
- **Coordinates and provenance are creation-time only.** `import_resorts`
  reads `latitude`/`longitude`, `geocode_source`, `geocode_confidence` and
  `needs_review` when it creates a row and never writes them again, so a
  resort re-pinned in the map editor cannot be dragged back to whatever the
  sheet said (SNOW-544). A coordinate edit travels between environments
  through `dump_resorts_sheet` → commit → a fresh database's first import.
- **A row with a coordinate but no `geocode_source`** — a curator typing one
  in by hand — is stamped `IMPORT` and `needs_review`. The panel's `MANUAL` /
  `confidence=1.0` / `needs_review=False` combination asserts that somebody
  placed the pin on a map, which is not true of sheet data.
- **`add` needs `region` and `canton`.** A row missing either is reported as
  an error rather than guessed at, and the fix is to fill the sheet.
- **Deleting a resort is safe for user data**: `Favourite.resort` is
  `SET_NULL`, so a favourite made from a deleted resort degrades to a plain
  pin with its snapshotted name, coordinates and region intact. It does leave
  the resort's `ForecastCell` unreferenced — run `prune_forecast_points
  --commit` after a bulk deletion to clear those and their cascaded weather
  rows (SNOW-633).
