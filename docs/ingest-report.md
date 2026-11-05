---
name: ingest-report
description: Ingest report /_ingest/, ops MCP ingest_report, check_ingest alarm — scheduled feeds pulled in, ok/warn/red/off states
status: current
last-reviewed: 2026-11-05
---

# The ingest report

A live answer to "what have the scheduled jobs brought in, and is any of them
stuck?" (SNOW-1423). One builder, `apps.public.ingest_report.build_report`,
feeds three readers:

| Reader | Where | For |
|---|---|---|
| Staff page | `/_ingest/` (`?window=1h`, `24h` default, `7d`); the panel re-fetches `partials/_ingest/` every minute | Looking |
| Ops MCP tool | `ingest_report` on `POST /api/ops/mcp/` (staff only), `window` argument | Asking Claude |
| `check_ingest` | Scheduled hourly at :50; raises `CommandError` naming every red feed | Sentry alerting |

## Feeds

One row per entry in `FEEDS`, in three groups. Runs are the scheduler's own
(`CommandRun`, `invoked_via=SCHEDULER`); a shell or MCP run of the same
command does not count.

| Feed | Command | Figures | Red when |
|---|---|---|---|
| SLF, ALBINA, Météo-France | `fetch_bulletins` | new / changed `SourceBulletin` rows of that provider | publishing, and the last issue is over 36 h old |
| Forecast | `fetch_weather` | active places with a `Weather` row fetched in the window, of all active | any active place lacks today's row, or nothing landed for 7 h |
| Backfill | `backfill_weather` | tasks queued, and failed `backfill_unit_task` tasks | no run for 2 h, or a failed task |
| Estate mirror | `mirror_reference_data` | host `{kind}_created` / `_updated` / `_deleted` counts | no run for 2 h |
| SAC tours | `import_ski_tours` | the same counts; `service_*` ignored | no run for 8 days |
| Tour drawings | `render_ski_tours` | tasks queued, and failed `render_tour_task` tasks | no run for 26 h |
| Rest zones | `import_rest_zones` | new / changed `SourceRestZone` rows | no run for 8 days |

**Why rows and not run counts, where it matters.** `fetch_weather`,
`backfill_weather` and `render_ski_tours` only enqueue (SNOW-1398/1399): their
counts say what was asked for, not what arrived. Bulletins are counted from
ingest's `SourceBulletin`, whose `updated_at` moves only when the content
does; a host `Bulletin` is rewritten on every re-store. SAC tours are counted
from the import's own counts, because the daily drawing job also rewrites
`SourceSkiTour`.

## States

| State | Dot | Meaning |
|---|---|---|
| `ok` | green | Nothing to look at |
| `warn` | amber | A run failed in the window, but the latest succeeded |
| `red` | red | The feed's own rule above holds, or its latest run failed. `check_ingest` alerts |
| `off` | grey | A bulletin provider with no issue in 7 days: out of season. Never red, never alerts |

A failed run already reaches Sentry by itself (`apps/core/sentry.py`: every
`logger.error`). `check_ingest` exists for the other failure: a feed that
goes quiet without erroring.

## The page

Each row shows the last run (linked to its admin page), the figures, a run
strip (`templates/includes/_run_strip.html`, in `/_components/`: one cell per
slice of the window, the worst run winning, each linked by `CommandRun.pk`),
the newest data and the next run (from the job's `CronTrigger` in
`schedule.build_scheduler()`, built once and never started). Below:
**Changes**, the newest 20 runs that failed or moved a feed's change counts
(operational counts such as `fetched`, `weighted_calls` or `already_queued`
never qualify), and the **Task queue** (`apps.core.task_queue`).

Housekeeping jobs (purges, what3words, slope reconcile, alerts) are left out
(Hugo, 2026-11-05). An MCP Apps view of the report is SNOW-1424.
