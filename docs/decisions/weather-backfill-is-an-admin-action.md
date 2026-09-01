---
name: weather-backfill-is-an-admin-action
description: The Weather backfill runs inline in a capped LocationAdmin action, not on the task queue, and a backfilled row's forecast[] stays null
status: current
last-reviewed: 2026-09-01
---

# The weather backfill is an inline admin action, and writes no forecast

Two decisions from SNOW-731, recorded together because both look like
omissions and neither is.

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
setting a sensible cap now.

## Decision 2 — a backfilled row's `forecast[]` stays null

**Decision.** `backfill_location` writes `forecast=None` on every row. It
does not derive forward days from the historical response.

**Why.** `Weather.forecast` means *the days after `observed_on`, as known on
`observed_on`* — the outlook as issued that morning. The historical forecast
API serves something different: a continuous stitched timeline, one value
per day, each as issued near that day. Writing the second into a column that
means the first would be a quiet lie, and the surfaces that read
`forecast[]` would present it as one. The whole point of `Weather` being an
account of a day rather than a cache of it is that nothing afterwards can
tell a rewrite from a record — so the column stays empty rather than
plausible.

**Consequences.** A historical location page renders its weather row, a
one-column day strip and the hourly meteogram, and **no outlook chart** —
`build_forecast_chart` returns `None` below two days, and the template
already guards on `{% if chart %}`. That absence is correct. Do not "fix" it
by inventing forward days from the stitched timeline.

The day strip's columns are selectable exactly when `hourly` is present, so
backfilling `hourly` — which this does, all 24 hours per day — is what makes
a historical meteogram work at all.
