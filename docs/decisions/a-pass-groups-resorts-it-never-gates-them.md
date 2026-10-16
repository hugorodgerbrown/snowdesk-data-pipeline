---
name: a-pass-groups-resorts-it-never-gates-them
description: Pass is a many-to-many on Resort (Resort.passes), not a boolean; passes column holds slugs; passes.tsv seeds them, no data migration
status: current
last-reviewed: 2026-10-10
---

# A pass groups resorts; it never gates them

**Decision.** A season pass is a `Pass` row (`apps/regions/models.py`)
linked to resorts through `Resort.passes`, a plain many-to-many. The resort
sheet's `passes` column lists a resort's passes by slug (`magic-pass`), and
the passes themselves are described by `apps/regions/data/passes.tsv`,
which `import_resorts` reads first and from which it only *creates* a pass
the database lacks. There is no per-pass flag on `Resort`.

**Why.** SNOW-1083 first added `Resort.magic_pass`, a boolean. The product
spec it served then changed: every Snowdesk resort is a place to plan a day
or meet, and a pass is only a convenient way to group resorts ("a group
with a Magic Pass could just as easily meet in Zermatt as Saas-Fee"). A
boolean per pass would need a column and a migration for every new pass,
and invites code that treats "on the pass" as "in scope". A many-to-many
holds any number of passes and reads as what it is: a filter.

The pass rows arrive through a sheet rather than a data migration because
every path that imports `resorts.tsv` needs them — a fresh worktree, a
staging reset, and the many tests whose database is flushed between
transactional tests, which wipes migration-seeded rows. A migration-seeded
Magic Pass broke exactly those tests.

**Consequences.**

- Adding a pass is a row in `passes.tsv` (or the admin) plus slugs in the
  resort sheet; no schema change.
- `import_resorts` never renames or deletes a pass, so an admin rename
  survives the next import. A slug is the sheet's key: renaming one breaks
  every row that names it.
- `dump_resorts_sheet` writes the resort links but not `passes.tsv`. A pass
  created only in the admin must be added to `passes.tsv` by hand, or a
  fresh database's import fails with "unknown pass".
- Nothing may filter *which resorts exist* by pass. Passes filter a view.
