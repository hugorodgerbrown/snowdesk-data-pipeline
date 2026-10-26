---
name: a-run-with-failed-records-is-partial
description: PipelineRun with records_failed > 0 finishes PARTIAL, not SUCCESS — Status.PARTIAL, mark_finished vs mark_success, ingest_status
status: current
last-reviewed: 2026-10-26
---

# A pipeline run with failed records is partial, not successful

**Decision.** `PipelineRun.Status` has a fourth terminal state, `PARTIAL`.
Every pipeline that goes through `upsert_bulletin` — the SLF, ALBINA and
Météo-France fetchers, the Météo-France archive loader, `seed_test_week`
and `pull_production_data` — finishes with `PipelineRun.mark_finished()`,
which stores `PARTIAL` when `records_failed > 0` and `SUCCESS` otherwise.
`mark_success()` is unchanged and stays for a caller that cannot record a
per-record failure. `FAILED` still means the pipeline itself raised.
(SNOW-1228.)

**Why.** `records_failed` counts real damage — a bulletin that could not
be stored, a render model stored as a `version=0` sentinel, a day rating
invalidated, a grouping outline removed — yet the run was filed as
`SUCCESS`. `fetch_bulletins` already exited non-zero on the counter, but
anything that read the status alone (the ops `ingest_status` tool, the
admin's status filter, the backfill message) reported a clean run. A
separate state is chosen over `FAILED` because the run did finish and most
of its records are good: an operator's response to the two is different
(re-ingest the failures, versus find out why nothing ran).

**Consequences.**
- `fetch_bulletins` behaves as before: a `PARTIAL` run flushes `--stash`,
  then raises its `records_failed` `CommandError`. Since SNOW-1228 made a
  failed grouping outline count, a geometry error now exits non-zero where
  it used to exit 0.
- `ingest_status` counts `FAILED` and `PARTIAL` runs in its summary; the
  admin backfill shows a `PARTIAL` run as a warning with the failed count.
- Migration `bulletins.0021` alters only `choices` and `help_text`, so it
  emits no DDL and old and new code can run against one database.
- A new pipeline that records failures must finish with `mark_finished`;
  calling `mark_success` would reintroduce the silent success.
