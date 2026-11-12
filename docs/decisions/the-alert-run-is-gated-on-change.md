---
name: the-alert-run-is-gated-on-change
description: Why send_bulletin_alerts is gated on change (last_evaluated_at, accounts_to_evaluate, held, gated, --full), not fetch-triggered
status: current
last-reviewed: 2026-11-12
---

# The alert run is gated on change

**Decision (SNOW-1491).** The hourly `send_bulletin_alerts` run decides for
itself whether anything could have changed since the last run that
evaluated. `last_evaluated_at` reads the register: the `started_at` of the
latest committed, successful `CommandRun` that was neither held for the
night (`held`) nor skipped (`gated`). `accounts_to_evaluate` then names the
alerting accounts with a reason to be looked at again since then: a changed
bulletin in a region one of their places covers, an undelivered alert to
retry, a place saved or changed, or alerts turned on. With none, the run is
skipped and says so; otherwise only those accounts are evaluated, by the
same `evaluate_account` as before. `--full` evaluates everyone.

**Why the job gates itself, not the fetch.** The other way round —
`fetch_bulletins` triggering the send when it stored something new — would
send sooner, but ingest would have to call into `apps.bulletins` alerting,
and `apps/ingest` imports no other `apps.*` package
([why](ingestion-is-a-rehostable-unit.md)). It would also leave the other
reasons to send (a retry, a newly saved place, alerts turned on) needing a
schedule anyway. Hugo chose the gate in the job on 2026-11-12. The
schedule, the night hold and the one-task-per-person delivery are
unchanged.

**Why `Bulletin.updated_at` is the durable marker.** A region's
`RegionDayRating` is the obvious thing to watch, and is watched, but it is
not enough on its own: when a recompute fails,
`apply_bulletin_day_ratings` deletes the rating rather than updating it, so
nothing is left with a fresh `updated_at`. The bulletin row the ingest
wrote is always there, and its regions come through the `RegionBulletin`
links. A day the previous run did not look at (the morning after, when
"tomorrow" has moved on) counts as changed for every region rated on it.

**Why `alerts_enabled_at`, not `Account.updated_at`.** Only turning alerts
on moves `alerts_enabled_at`; any profile save moves `updated_at`, and would
reopen the gate for nothing. Switching modes needs no reopening: every
logged row carries the fingerprint it was decided on.

**Consequences.**

- Over-selecting is always safe: an account evaluated with nothing new is
  told nothing. Missing one is not, so every marker errs wide. The gate
  looks back `GATE_OVERLAP` (15 minutes) before the watermark, for a row
  committed by a transaction that began before the previous run started.
- A dry run uses the gate but is never the watermark, and nor is a failed
  or partial run: the next run looks back as far as the last one that
  told everyone. A run recorded before the `held` tally is judged by its
  start time: inside the night hold it evaluated nothing, unless it was
  told `--ignore-night-hold`.
- A change that writes none of the markers is not seen until `--full`: a
  render-model rebuild without `refresh_ratings` updates `Bulletin` with
  `QuerySet.update()`, which does not move `updated_at`.
- A skipped run costs a fixed handful of queries, whatever the number of
  accounts.
