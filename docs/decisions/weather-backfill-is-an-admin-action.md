---
name: weather-backfill-is-an-admin-action
description: Weather backfill runs inline in a capped LocationAdmin action; a backfilled row is its day at lead zero (forecast_on)
status: current
last-reviewed: 2026-11-01
---

# The weather backfill is an inline admin action, and writes no forecast

Two decisions from SNOW-731, recorded together because both look like
omissions and neither is.

Both still hold since SNOW-1275 moved the backfill into the weather service
(`apps/ingest/backfill.py`): the action now fills the selected locations'
stations in `SourceWeather` and applies each row into `Weather`, still
inline and still capped, and a backfilled row's `forecast` is still null in
both tables.

## Decision 1 — inline in the request, capped, not enqueued

**Decision.** "Backfill missing weather" on `LocationAdmin` does its work
inside the admin request, throttled by `INTER_LOCATION_DELAY` and capped at
`ADMIN_MAX_LOCATIONS` per run. It is not handed to `django_tasks`. The
operator stages larger batches from the changelist, or runs
`backfill_weather` from a terminal.

**Why.** Enqueuing looks like the obvious answer and is the wrong one here.

- Production runs **one** `db_worker`, and `db_worker` has no concurrency
  flag. Enqueuing one task per location would run them serially anyway, so
  the queue buys no parallelism.
- That worker serves the **default** queue, which is also where subscription
  email goes (invariant 3 — subscription email is always async,
  `DatabaseBackend` in production). A long backfill task would head-of-line
  block outbound mail for its whole duration. A slow admin page is a better
  failure than an email queue stalled behind a data job.
- The risk the queue is reached for — unthrottled iteration across hundreds
  of locations — is not solved by a queue. Open-Meteo's terms reserve the
  right to block a misusing IP "without prior notice", and the IP is
  production's. Pacing solves that; queuing does not.

**Consequences.** The cap is load-bearing and must stay derived from the
request budget: `render.yaml` starts gunicorn with no `--timeout`, so the
default 30s applies unless the Render env group overrides
`GUNICORN_CMD_ARGS`. The action reports what the cap skipped, so a truncated
run never reads as a finished one. If unattended bulk backfilling is wanted
later, the escalation is a dedicated queue and its own worker
(`db_worker --queue-name backfill`, `--exclude-queues backfill` on the
existing one) — a separate ticket, and a dyno. It is not a reason to skip
setting a sensible cap now. (SNOW-1245 took a cheaper route: the hourly
`backfill_weather` sweep runs on the scheduler worker, which is not the
email queue — see
[`weather-backfill-sweeps-newest-fortnight-first.md`](weather-backfill-sweeps-newest-fortnight-first.md).)

## Decision 2 — a backfilled row is its day at lead time zero

*Amended by SNOW-1337 (Hugo, 1 Nov 2026).* Until then this said a
backfilled row's `forecast[]` stays null: `forecast` meant *the days after
`observed_on`, as known on `observed_on`*, and the historical API's
stitched timeline is not that, so the backfill left the column empty
rather than plausible. SNOW-1337 removed the column. A row is now the
account of its own day only, and the outlook from any day is the rows
after it.

**Decision.** `backfill_location` writes each past day as one row with its
scalars and its hourly series and `forecast_on = observed_on` (lead time
zero), in `SourceWeather` and so in the `Weather` row applied from it. It
derives no other day from the historical response.

**Why.** A fetched row's lead time closes to zero on its own day and stays
there once the day has passed, so a backfilled row of the same day says
the same kind of thing: the day as it was known on the day. The stitched
timeline is exactly that, one value per day, each as issued near that day.

**Consequences.** A backfilled day followed by other recorded days reads as
a week, like any other day. A backfilled day with no rows after it yields
one day, which is the location forecast page's **second shape** (SNOW-789):
masthead, that day's line, its strips, provenance — and **no day picker at
all**, with the day line carrying no `.forecast-day-line` hiding class,
because with no picker there is no radio and the `:has()` reveal could
never match it. Why the picker is a selector rather than navigation:
[`weather-day-picker-is-a-selector-not-navigation.md`](weather-day-picker-is-a-selector-not-navigation.md).
