---
name: weather-backfill-sweeps-newest-fortnight-first
description: backfill_weather as an hourly budgeted sweep — fortnight blocks newest first, sweep_budget from Open-Meteo weighted quota
status: current
last-reviewed: 2026-11-03
---

# The weather backfill sweeps newest fortnight first, inside a derived budget

SNOW-1245. Amends the "unattended bulk backfilling" consequence of
[`weather-backfill-is-an-admin-action.md`](weather-backfill-is-an-admin-action.md).

**Since SNOW-1275 (28 Oct 2026) the sweep is the weather service's.** It
moved to `apps/ingest/backfill.py` unchanged in shape: it walks the
stations (`SourceLocation`), diffs and writes `SourceWeather`, and hands
each row to the host, which applies it into `Weather`, keeping a past day
it already holds. Read "location" below as "station". The budget's live
term counts the service's live stations, which are the curated estate.

**Since SNOW-1399 (3 Nov 2026) a sweep is planned, then enqueued.** The
command no longer walks the requests itself. `plan_sweep` yields the
station-blocks the budget buys, in the same order and under the same
budget rule, and the command enqueues one `backfill_unit_task` per unit
through `enqueue_each` ([`bulk-commands-enqueue-each-row.md`](bulk-commands-enqueue-each-row.md)).
Each task fills its unit with `sweep_block`, which re-reads the gap, so a
unit filled since it was planned costs nothing. Two rules below change:

- **The budget is the enqueue limit.** It bounds what one run plans, and a
  unit still waiting from an earlier run is planned again, found already
  queued and not enqueued twice — so it counts against this run's budget,
  and a stalled worker never lets the queue grow past one hour's worth.
- **A 429 trips the breaker instead of stopping a loop.** The task that
  met it opens the `open-meteo` breaker for five minutes and fails; every
  task behind it, the live fetch's included, returns without asking, and
  their units stay gaps for the next plan. `--delay` staggers the tasks and
  is each one's gap on the shared `open-meteo` slot (`wait_for_slot`).

## Decision

`backfill_weather` is scheduled hourly (`schedule.py`, :20). Each run is one
sweep:

- The window (`WEATHER_BACKFILL_FLOOR` to yesterday) is cut into 14-day
  blocks aligned to the floor and walked **newest first**.
- A block is finished across every selected location before the next one
  starts, with **one request per location per block**, spanning its first
  missing day to its last.
- The run stops before the request that would overspend its budget of
  **weighted** Open-Meteo calls, and stops at once on a 429.
- The default budget is `sweep_budget`: 90% of the free tier's 10,000 calls
  a day, minus `fetch_weather`'s four runs over the active estate, over 24.

## Why

- **Open-Meteo charges by weight, not by request.** A request is one call up
  to ten variables and fourteen days, and scales linearly past either. Ours
  carry 24 variables: a fortnight is 2.4 calls, a season ~63. The first full
  production run asked each of 590 locations for its whole season and was
  throttled after nineteen minutes — about 37,000 calls against 10,000 a
  day. No pacing of that shape fits the quota; a budget does.
- **Fourteen days is the unit.** A one-day request costs the same 2.4 calls
  as a fortnight, so a block is one request, and splitting it around the
  days already held would pay for the same fortnight twice.
- **Time-major, not location-major.** Finishing one location's season at a
  time would leave most of the estate blank for a fortnight. Finishing a
  block everywhere first means every page gains the same recent history at
  once, and a run's work is one contiguous stretch of the calendar.
- **Newest first** because the recent past is what a reader most often
  opens, and because a location minted today needs last week before it
  needs last November.
- **Derived, not fixed.** The live fetch and the sweep share one per-IP
  quota. A fixed number would silently start throttling the live fetch —
  the job that keeps today current — as the estate grew. The derived budget
  shrinks instead, and reaches zero rather than going negative.
- **On the task queue since SNOW-1399.** The original ADR ruled out
  `db_worker` because it would block subscription email, and this ADR kept
  the sweep on the scheduler for that reason. The bulk tasks now run at
  `BULK_TASK_PRIORITY` (−50), below email and pushes, so that objection no
  longer holds; the scheduler only plans and enqueues.

## Consequences

- At 590 locations the whole season takes roughly eleven days to fill
  unattended. An urgent day is a manual run with `--floor`/`--until`,
  `--resort` or `--location`, and an explicit `--budget`.
- A location whose day never resolves upstream is requested again every
  run. It costs 2.4 calls an hour and blocks nothing else, because the
  plan moves past it.
- A read-only run plans and counts; it asks Open-Meteo nothing (it used
  to issue the requests and discard them).
- `LIVE_FETCHES_PER_DAY` must follow `fetch_weather`'s trigger; a test
  holds the two together. On a paid key, pass `--budget` — the free-tier
  arithmetic no longer applies.
