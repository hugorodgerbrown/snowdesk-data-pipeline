---
name: bulletin-target-date-vs-default-issue-selection
description: Why Bulletin.target_date's 12:00 rule and _select_default_issue's 10:00 pivot stay separate mechanisms, not one stored column
status: current
last-reviewed: 2026-07-30
---

# `Bulletin.target_date` and `_select_default_issue` answer different questions

**Decision.** `Bulletin.target_date` (SNOW-560) and the 10:00 UTC pivot inside
`apps/public/views.py::_select_default_issue` remain two separate mechanisms.
`target_date` is a stored column, populated once at ingest time by
`target_day_for_valid_from()`. `_select_default_issue`'s pivot is **not**
promoted to a stored column or otherwise unified with it.

`target_day_for_valid_from` **classifies one bulletin**: "which calendar day
does this forecast?" — a pure function of `valid_from` (hour < 12 UTC →
same day; hour >= 12 UTC → next day). It takes a single bulletin and returns
a single answer that never changes once the bulletin is issued.

`_select_default_issue` **selects from a day's issue set**: "given every
bulletin that targets this day, which one does the page show right now?" It
takes a list of candidate issues and a `target_date`, and for **today**
specifically it pivots on live wall-clock time (`timezone.now()`) rather than
a fixed hour — the bulletin whose validity window contains *this moment* is
the one the public is currently being shown. For any other day it falls back
to a fixed 10:00 UTC pivot (after the 08:00 morning update, before the 17:00
evening rollover), which happens to implement the same morning-wins /
prior-evening-fallback convention as `target_day_for_valid_from`, but for a
different reason: it is choosing which of that day's *published* issues is
"the" bulletin for display, not deciding what day a bulletin belongs to.

Because the "today" branch depends on the current instant rather than any
fixed property of the data, `_select_default_issue`'s answer can change
between two requests for the same day without any new bulletin being
ingested — it is a display-time decision, not an ingest-time fact. There is
nothing to store: a column would either be wrong the moment the clock moves
past a rollover, or would need to be recomputed on every read, at which
point it isn't a stored column any more.

**Why provider-supplied day fields were rejected as the source for
`target_date`.** The originally scoped approach was to read each provider's
own day field where available (e.g. Météo-France's `customData.MF.date`)
rather than deriving uniformly from `valid_from`. This was dropped:

- ALBINA's `customData.ALBINA.mainDate` and Météo-France's
  `customData.MF.date` never disagreed with `target_day_for_valid_from` in
  the committed archives — measured across both archives: ALBINA 1,193/1,193
  records agree, Météo-France 4,671/4,671 records agree. 5,864 records, zero
  disagreement.
- Météo-France's field is not implementable for live ingest regardless:
  `meteofrance_translator._build_custom_data_mf` does not emit a `date` key
  on live-fetched bulletins (verified against all three MF sentinels in
  `tests/sentinels/meteofrance/`) — it exists only in the PDF-scraped
  `meteofrance_archive.ndjson`, written by `fixup_envelope`. An ingest-time
  rule reading it would return `None` on every live bulletin.

**Consequences.**

- `target_date` is derived identically for SLF, ALBINA, and Météo-France —
  one rule (`target_day_for_valid_from`), one code path
  (`slf_fetcher.upsert_bulletin`), for all three providers and all five
  callers that route through it (`slf_fetcher`, `albina_fetcher`,
  `meteofrance_fetcher`, `meteofrance_archive_loader`, `golden_week`).
- If SNOW-559 changes Météo-France's `valid_from` to the real issue time
  (rather than the current placeholder), MF previous-evening issues would
  shift `target_date` by a day relative to the archive's
  `customData.MF.date`. `tests/bulletins/services/test_meteofrance_archive_loader.py::TestTargetDateMatchesArchiveDate`
  pins the current agreement so that shift surfaces as a red test rather
  than a silent drift.
- `_select_default_issue` continues to take a `target_date` as an input
  (computed the same way it already was) and is unaffected by this change —
  it reads bulletins already known to target a given day; it does not
  re-derive which day they target.
