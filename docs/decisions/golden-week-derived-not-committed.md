---
name: golden-week-derived-not-committed
description: The golden-week bulletin corpus is derived from the committed local_mirrors archives at seed time, not committed as its own fixture
status: current
last-reviewed: 2026-07-29
---

# The golden week is derived at seed time, not committed as a fixture

**Decision.** The "golden week" — seven consecutive real days of bulletins from
all three providers, used to test behaviour *across* days — is **selected from
the archives already committed under `apps/bulletins/local_mirrors/`** each time
`seed_test_week` runs. It is not captured from the provider APIs, and it is not
written to a fixture directory of its own. The week itself
(Mon 2026-02-09 → Sun 2026-02-15) is a constant in
`apps/bulletins/services/golden_week.py`.

**Why.** SNOW-528 was originally scoped as a `capture_golden_week` command that
would fetch seven days from SLF, ALBINA and Météo-France and commit the result
as `tests/fixtures/golden_week/`. That premise was wrong: the repo already
carries a full 2025/26 season for all three providers — 8,080 records, ~30 MB,
git-tracked — because `fetch_bulletins --stash` writes there and the dev-mirror
views replay it. Capturing would have re-downloaded data the repo already has,
and committing the result would have duplicated ~1.7 MB of it.

Deriving instead means the corpus costs no new bytes, needs no network access
and no running dev server, and stays reproducible: the same archives plus the
same week constant always yield the same corpus. It also keeps the licensing
position unchanged — the redistribution question, such as it is, concerns the
existing archives and is not created or widened by this corpus.

The archives are replayed through the production `upsert_bulletin` rather than
loaded as serialised model rows, so a CAAML shape change that the ingest path
cannot handle fails loudly when the corpus is seeded, instead of silently
producing rows that no longer resemble what the pipeline would write today.

**Consequences.**

- The golden week depends on `apps/bulletins/local_mirrors/*.ndjson` staying in the
  tree. Trimming those archives to save repo size would break `seed_test_week`;
  the week constant must be re-pointed at a range the trimmed archives still
  cover, and `tests/bulletins/services/test_golden_week.py` will fail until it
  is.
- Selecting by target day requires each provider's *own* day key, because they
  disagree: SLF has none and must be derived from `validTime.startTime` via
  `day_rating.target_day_for_valid_from`, ALBINA carries
  `customData.ALBINA.mainDate` (its `validTime.startTime` is 16:00 the previous
  day), and Météo-France carries `customData.MF.date`. Reading the wrong field
  shifts a whole provider by a day.
- Météo-France's `bulletinID` is synthesised as `FR-{NN}-{date}` from the
  *covered* day, so its previous-evening and morning issues for one massif
  collide on a single ID. `golden_week` collapses them deterministically,
  keeping the later-published issue. The underlying synthesis is not narrowed
  here — that would change the identity of Météo-France bulletins already
  ingested by `load_meteofrance_archive`, which is a migration, not a fixture
  change. It is tracked as SNOW-559; `_dedupe_meteofrance` should be deleted
  when that lands.
- The corpus is kept strictly separate from `tests/sentinels/`. Those are one
  graded payload per structural case, enforced by a round-trip test; this is a
  realistic corpus. Merging them would leave the sentinels no longer minimal and
  the corpus no longer representative.
