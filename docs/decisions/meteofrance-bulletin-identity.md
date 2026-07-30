---
name: meteofrance-bulletin-identity
description: Météo-France bulletin_id carries the publication timestamp; the archive and live paths share the grammar but are separate identity spaces
status: current
last-reviewed: 2026-07-30
---

# Météo-France bulletin identity

**Decision.** A Météo-France `bulletin_id` is
`FR-{NN}-{covered date}-{YYYYMMDDHHMMSS}` — massif, the day the bulletin
forecasts, and the **publication instant in UTC**. Both ingest paths build it
through `bulletins/services/meteofrance_identity.py`. A record with no usable
publication timestamp is a failure, never an id without the stamp.

The two paths take that timestamp from different places and are therefore
**separate identity spaces**: an id minted by one will not match the other for
the same bulletin.

- Archive: each PDF's `CreationDate` metadata.
- Live: the DPBRA `@DATEDIFFUSION` attribute.

**Why.** Météo-France publishes more than one BRA per massif per covered day —
typically a previous-evening issue and a morning refresh, occasionally an
amendment. The original `FR-{NN}-{covered date}` could not tell them apart, and
the two paths then failed differently and silently: the archive loader
overwrote one issue with the other via `update_or_create` (winner decided by
line order in the NDJSON), while the live fetcher's `exists()` check skipped the
second outright, so a massif was never updated after the first fetch of the day.
Neither counted a failure. Across the committed archive that affected 105
massif-days, 56 of which differ in structured forecast data — danger ratings,
elevation bounds, tendency, wind.

Keying on the publication instant is exact, not approximate. Measured across the
4,671 archived records it yields **4,639 ids, matching the 4,639 distinct PDFs
by checksum**, with no timestamp covering two distinct PDFs. The 32 records that
still coalesce are byte-identical duplicate downloads and should coalesce.

Two alternatives were measured and rejected:

- **Publication *date* rather than instant** (the form the ticket proposed)
  still leaves 80 records colliding across 64 ids, because same-day re-issues
  share a date.
- **A sequence number from the filename** (`BRA.ARAVIS.2026-01-30.1.pdf`) is
  actively wrong: the `.N` suffix came from aria2c's filename-conflict rename
  in *download* order, not publication order. For ARAVIS 2026-01-30 the `.1`
  file was rendered at 14:45:23 and the unsuffixed one at 15:01:15, so a
  sequence-derived id orders the issues backwards.

Cross-path id equality was in the ticket's acceptance criteria and was dropped
as unachievable: `CreationDate` (when MF rendered the PDF) and `@DATEDIFFUSION`
(MF's internal diffusion time) are recorded at different moments and differ by
0–6 minutes for the same bulletin — BELLEDONNE 2026-05-17 is `13:37:50` against
`13:37:00`, ARAVIS `14:07:47` against `14:02:00`. Nothing in the data lets one
be derived from the other.

**Consequences.**

- Amendments need no special handling. A same-day amendment has its own
  publication timestamp, so it lands on its own row; `unscheduled` still records
  that it was one. The `AMENDEMENT=true` log message that promised a future
  suffix implementation is gone.
- `recompute_region_day` and `_select_default_issue` need no Météo-France
  branch. Archive extraction now takes `validTime.startTime` from the bulletin's
  own "Rédigé le … à 16h" line instead of a synthetic midnight, so the existing
  `max(valid_from)` rule picks the morning refresh unaided.
- The archive and live paths must not be pointed at the same massif-day and
  expected to converge on one row. In practice they do not overlap — the archive
  is a closed backfill (2025-11-03 → 2026-05-21) and live ingest runs forward —
  and where they would, two rows is the better failure: an archive re-load
  overwriting a live row would replace richer data with poorer.
- Changing the identity meant re-keying every row already ingested, done once
  via `manage.py rekey_meteofrance_bulletins` (see
  [`dry-run-default-commands`](dry-run-default-commands.md)). Re-keying does not
  recreate the issues previously overwritten; that needs an archive re-load.
- A genuine collision — two records on one id with differing payloads — now
  warns and counts toward `records_failed`, so it can never again be silent.
