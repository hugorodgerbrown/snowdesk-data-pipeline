---
name: async-operations
description: Async callsites — django-tasks email, alerts, push, enqueue_each row tasks, render rebuilds, run_command; db_worker
status: current
last-reviewed: 2026-11-06
---

# Async operations

Background work — anything dispatched off the request cycle — is a classic
source of untraceable errors. This doc catalogues every async callsite in the
Snowdesk codebase, the failure mode for each, and the on/off toggle if one exists.

## Catalogue

Every background callsite is a `django_tasks` task. In production
`snowdesk-background-tasks` runs them with `db_worker`
([diagram](img/architecture-runtime.svg)); in dev, test and staging
`ImmediateBackend` runs each one inline the moment `.enqueue()` is called.
A failed task is marked failed with its traceback, visible in the admin under
`DBTaskResult`; nothing is lost silently. `task_queue_health`
(`apps/core/task_queue.py`, also an ops MCP tool) reports the backlog and the
past day's failures.

| Group | Tasks (module) | Enqueued by | Priority |
|-------|----------------|-------------|----------|
| Account email | `_worker_send_account_access_email`, `_worker_send_verification_email`, `_worker_send_password_reset_email`, `_worker_send_email_change_confirmation`, `_worker_send_email_change_notice` (`apps.accounts.services.email`) | the matching public `send_*` function in the account flow | default |
| Bulletin alerts | `_worker_deliver_alert` (`apps.bulletins.services.alerts`), `_worker_send_alert_email` (`apps.accounts.services.alert_email`) | `send_bulletin_alerts`, scheduled at :10 | default |
| Web Push | `_worker_dispatch_push` (`apps.accounts.push_service`); `dispatch_push` deletes a row on 404/410 and survives 5xx | `enqueue_push` | default |
| On-save work | `_worker_prime_location` (`apps.locations.services.prime`, after commit); `_worker_sample_route_slopes` (`apps.routes.services.slope_segments`); `_worker_sample_trip_slopes` (`apps.trips.services.slope`) | saving a location, route or trip | default |
| Bulk commands, one task per row | the row tasks in `apps.locations.services` (`elevation`, `what3words`, `orphans`), `apps.weather.services.station_tasks`, `apps.routes.services.render_tasks` and `slope_segments`, `apps.trips.services` (`render_tasks`, `slope`) | a command through `apps.core.command_tasks.enqueue_each`, paced by `run_after` and `wait_for_slot` ([why](decisions/bulk-commands-enqueue-each-row.md)) | `BULK_TASK_PRIORITY` (−50) |
| Render rebuilds | `rebuild_bulletin_task`, `rebuild_source_bulletin_task`, `refresh_source_day_rating_task`, `recompute_day_rating_task` (`apps.bulletins.services.render_rebuild`); `render_tour_task` (`apps.routes.services.ski_tour_render`) | the rebuild commands and `render_ski_tours` | `RENDER_TASK_PRIORITY`, equal to the bulk priority |
| Remote commands | `run_command_task` (`apps.core.remote_commands`) | the ops MCP `run_command` tool; refused where tasks run inline ([why](decisions/a-remote-command-runs-on-the-task-worker.md)) | default |

A task that calls an outside service registers with `calls_outside` and
backs off on a 429 or outage (`back_off`), so one rate limit stops the rest
of its queue instead of burning it.

## django-tasks backend split

Snowdesk uses the third-party `django-tasks` package (PyPI: `django-tasks`,
`django-tasks-db`) for all work done off the request cycle.

### Development and tests

`TASKS["default"]["BACKEND"]` is set to
`"django_tasks.backends.immediate.ImmediateBackend"` in `development.py` (and
inherited by the test settings). Tasks run **inline** in the same process the
moment `.enqueue()` is called, so:

- Email lands in Mailpit immediately during local dev.
- `mail.outbox` is populated synchronously during tests — no fixture hacks needed.
- No `db_worker` process is required.

`staging.py` sets the same `ImmediateBackend`: staging runs one web dyno
and no `db_worker`, so a `DatabaseBackend` enqueue there would persist a
task row that nothing ever sends.

### Production

`TASKS["default"]["BACKEND"]` is set to `"django_tasks_db.DatabaseBackend"` with
`"QUEUES": ["default"]` in `production.py`. Tasks are serialised into the
`django_tasks_database_dbtaskresult` table and consumed by a separate Render
Background Worker service running:

```
python manage.py db_worker
```

The worker is declared in [`render.yaml`](../render.yaml) as
`snowdesk-background-tasks` alongside the web service and the scheduler
worker. Blueprint auto-sync is enabled, so adding or renaming the worker
in `render.yaml` propagates to Render on the next sync — but env-var
values marked `sync: false` still have to be set in the dashboard by hand.

Scheduling (running `fetch_bulletins` on a cron cadence)
lives in a separate `snowdesk-scheduler` worker — do not conflate the two.
`snowdesk-background-tasks` runs `db_worker` (consuming enqueued tasks from
the DB); `snowdesk-scheduler` runs `run_scheduler` (APScheduler blocking
loop).
See [`schedule.py`](../schedule.py) and the "Operational requirements" section
of [`docs/management-commands.md`](management-commands.md).

**Without an active `db_worker`, tasks accumulate in the DB but are not
consumed.** The `ImmediateBackend` default in `base.py` acts as a safe fallback
if a deployment forgets to set the production backend — mail is sent
synchronously rather than silently dropped — but production always sets
`DatabaseBackend` explicitly.

### Task retention

`django_tasks_db` accumulates completed task results indefinitely unless
`prune_db_task_results` is run. Nothing schedules it yet, so the table
grows with every task, including each bulk command's row tasks.

## Daemon-thread operations

No callsite uses a daemon thread today — SNOW-762 removed the last one
(`fetch_weather_async`) with the weather app. The caveats are kept because
they are what any future daemon-thread path has to answer:

- **Daemon threads do not block worker shutdown.** Gunicorn killing or
  recycling a worker (`--max-requests`, SIGTERM) leaves any in-flight
  daemon work *unfinished*. Such work must therefore be idempotent — if
  the thread is killed before persisting, the next request that needs the
  data schedules a fresh attempt.
- **DB connections are thread-local.** A background thread that touches
  the ORM opens its own connection, separate from the request thread's.
  Long-running worker processes will leak connections under sustained
  background traffic unless the worker calls
  `django.db.connections.close_all()` in a `finally` clause.
- **No request-cycle transaction.** Background threads run *outside* the
  request's atomic transaction. If you need atomicity across multiple DB
  writes inside the worker, wrap them in your own
  `transaction.atomic()` block — don't assume the request's transaction
  is in scope.
- **No request context.** `request.user`, `request.session`, locale,
  and `django.utils.timezone.override` are not in scope inside the
  worker. Pass anything the worker needs as plain arguments.

## When to add a new async operation

**Prefer `django-tasks` over daemon threads for new work.** django-tasks gives
durability (tasks survive process restart), visibility (admin UI over
`DbTaskResult`), and retry without requiring custom error handling.

1. Decorate the worker function with `@task()` from `django_tasks`.
2. Accept only JSON-serialisable primitives in the worker signature — no
   `HttpRequest`, no model instances, no callables.
3. Add a thin public wrapper that extracts any request data and calls
   `.enqueue()`. The wrapper matches the old sync signature so callers
   need no changes.
4. Add the callsite to the catalogue above with all columns filled in.
5. If adding a daemon-thread operation instead (rare — prefer django-tasks),
   mirror the on/off toggle pattern: a `settings.<NAME>_ASYNC` (default
   `True`) and a pin in `tests/conftest.py`; wrap in `try/except Exception →
   logger.<level>(..., exc_info=True)`; close DB connections in `finally`.
