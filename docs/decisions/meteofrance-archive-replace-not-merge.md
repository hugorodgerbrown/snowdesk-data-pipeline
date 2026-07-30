---
name: meteofrance-archive-replace-not-merge
description: Load-then-purge-with-guard ordering for replacing the old-grammar Météo-France archive rows; why delete-first and re-keying were rejected
status: current
last-reviewed: 2026-07-30
---

# Replace the legacy Météo-France archive rows, don't merge them

**Decision.** Bringing the rebuilt Météo-France archive (SNOW-559 grammar) into
a database still holding the pre-SNOW-559 rows is a **load, then purge, with a
per-row verification guard** — never a delete-first pass, and never an attempt
to re-key the existing rows onto the new grammar.

1. Load the rebuilt archive. Old and new-grammar rows coexist; the calendar
   and map briefly show duplicate massif-days.
2. Run `purge_legacy_meteofrance_bulletins` (dry-run first). It deletes an
   old-grammar row **only** when a new-grammar row already exists whose id is
   exactly the old id plus `-{14-digit publication stamp}`. A candidate with no
   such sibling is reported and left alone — never deleted.

**Why.**

*Re-keying is structurally impossible for this population.*
`rekey_meteofrance_bulletins` derives the new id from a row's own `raw_data`,
but every row ingested before SNOW-559 was written by an archive builder that
never captured a publication timestamp — `properties.publicationTime` and the
live-path fallback are both absent. `_new_id_for` returns `None` for all 4,549
of them, and the command exits non-zero on the first row. The instant exists
only in the source PDFs' `CreationDate`, which only the *rebuilt* archive
carries, under an id that is structurally disjoint from the old one
(`FR-14-2026-05-19` vs `FR-14-2026-05-19-20260518160000`). Nothing links the
two rows in the database; only their common covered-date prefix does, and only
after the new one has landed.

*Delete-first is not survivable.* `bulletin_id` is `unique=True`, so the
rebuilt archive's rows cannot land while the old ones with the same conceptual
identity are still there without a purge either way. The two orderings differ
in what an interruption between the load and the purge leaves behind:

- **Delete old rows, then load** — an interruption after the delete and before
  the load leaves the entire 2025/26 French season missing from the calendar
  and map until the load completes. There is no fallback data.
- **Load new rows, then purge old** — an interruption leaves old and
  new-grammar rows coexisting. `recompute_region_day` already resolves this in
  the new rows' favour: they carry a real publication-derived `valid_from`
  against the old rows' synthetic midnight, so `max(candidates, key=valid_from)`
  picks the new row. The site is not depending on stale data at any point.

Transient duplication beats a data gap, so load comes first.

*The verification guard makes the purge order-independent and safe on its
own.* Because the new grammar is exactly `{old id}-{14 digits}`, "does a
replacement exist" is a single indexed `bulletin_id__startswith` prefix lookup
— no region or date re-parsing, and structurally incapable of deleting a row
that has nothing to replace it. This guard is what makes the command safe to
run against production without first proving the load was complete.

**Consequences.**

- `purge_legacy_meteofrance_bulletins` is a permanent addition to the
  management-command catalogue, not deleted once the production run
  completes — a future re-ingest of any old-grammar backlog (e.g. a partial
  archive rebuild) can reuse it unchanged.
- `RegionBulletin` and `BulletinGrouping` rows for a purged bulletin cascade
  away automatically (`on_delete=CASCADE`); the command reports the counts but
  does not need to handle them.
- `RegionDayRating.source_bulletin` and `BulletinShare.bulletin` are
  `SET_NULL`, and neither self-heals. The command collects every touched
  `(region, day)` pair **before** deleting (the `RegionBulletin` links that
  back `bulletin.regions.all()` cascade away with the row) and recomputes each
  afterwards, and refuses to orphan a live `BulletinShare` unless
  `--allow-orphaned-shares` is passed explicitly.
- The runbook ([`rebuild-meteofrance-archive`](../runbooks/rebuild-meteofrance-archive.md))
  sequences load → assert counts → purge dry-run → purge `--commit`, replacing
  the old "run `rekey_meteofrance_bulletins --commit` first" step, which cannot
  exit 0 against this population.
