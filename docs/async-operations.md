---
name: async-operations
description: Catalogue of async callsites — django-tasks email backends, fetch_weather_async daemon thread, db_worker on Render
status: current
last-reviewed: 2026-06-10
---

# Async operations

Background work — anything dispatched off the request cycle — is a classic
source of untraceable errors. This doc catalogues every async callsite in the
Snowdesk codebase, the failure mode for each, and the on/off toggle if one exists.

## Catalogue

| Callsite | Trigger | Work | Persistence | Failure mode | Log channel | Toggle |
|----------|---------|------|-------------|--------------|-------------|--------|
| `subscriptions.services.email` — `_worker_send_account_access_email`, `_worker_send_subscription_confirmation_email` | Either of the two `send_*` public functions on the subscription flow | SMTP send via Django's configured backend | `django_tasks_db.DatabaseBackend` in production (DB row); `ImmediateBackend` in dev/test (in-process, no persistence) | `django-tasks` captures unhandled exceptions and marks the task failed; failed tasks are visible in the admin under the `DbTaskResult` model; no silent loss | `subscriptions.services.email` | Backend setting: `TASKS["default"]["BACKEND"]` — `ImmediateBackend` for dev/test; `DatabaseBackend` for production |
| `bulletins.services.weather_fetcher.fetch_weather_async` | `bulletin_detail` page render where no `WeatherSnapshot` exists for `(region, target_date)` and `target_date < today` | Idempotent DB pre-check, then `fetch_archive_for_region` / `fetch_weather_for_region` (Open-Meteo) | `WeatherSnapshot` row via `update_or_create` | `Exception` caught, logged at WARNING via `logger.warning(exc_info=True)`; `connections.close_all()` in `finally` so the thread releases its DB connection | `bulletins.services.weather_fetcher` | `WEATHER_FETCH_ASYNC` (default True; tests pin False) |

## django-tasks backend split

Snowdesk uses the third-party `django-tasks` package (PyPI: `django-tasks`,
`django-tasks-db`) to enqueue subscription emails off the request cycle.

### Development and tests

`TASKS["default"]["BACKEND"]` is set to
`"django_tasks.backends.immediate.ImmediateBackend"` in `development.py` (and
inherited by the test settings). Tasks run **inline** in the same process the
moment `.enqueue()` is called, so:

- Email lands in Mailhog immediately during local dev.
- `mail.outbox` is populated synchronously during tests — no fixture hacks needed.
- No `db_worker` process is required.

### Production

`TASKS["default"]["BACKEND"]` is set to `"django_tasks_db.DatabaseBackend"` with
`"QUEUES": ["default"]` in `production.py`. Tasks are serialised into the
`django_tasks_database_dbtaskresult` table and consumed by a separate Render
Background Worker service running:

```
python manage.py db_worker
```

The worker is documented in [`render.yaml`](../render.yaml) as
`snowdesk-worker-tasks` alongside the web service and the scheduler worker.
Note that Blueprint auto-sync is **not** enabled — the worker must also be
created manually in the Render dashboard before the deployed code can consume
enqueued tasks.

Scheduling (running `fetch_bulletins` and `fetch_weather` on a cron cadence)
lives in a separate `snowdesk-scheduler` worker — do not conflate the two.
`snowdesk-worker-tasks` runs `db_worker` (consuming enqueued tasks from the
DB); `snowdesk-scheduler` runs `run_scheduler` (APScheduler blocking loop).
See [`schedule.py`](../schedule.py) and the "Operational requirements" section
of [`docs/management-commands.md`](management-commands.md).

**Without an active `db_worker`, tasks accumulate in the DB but are not
consumed.** The `ImmediateBackend` default in `base.py` acts as a safe fallback
if a deployment forgets to set the production backend — mail is sent
synchronously rather than silently dropped — but production always sets
`DatabaseBackend` explicitly.

### Task retention

`django_tasks_db` accumulates completed task results indefinitely unless
`prune_db_task_results` is run. This is a candidate for a future scheduled
management command. It is not in scope for SNOW-229.

## Daemon-thread operations (`fetch_weather_async`)

The weather-fetch path still uses a daemon thread (not `django-tasks`). The
following caveats apply to that path only:

- **Daemon threads do not block worker shutdown.** Gunicorn killing or
  recycling a worker (`--max-requests`, SIGTERM) leaves any in-flight
  daemon work *unfinished*. All async work here is therefore idempotent —
  if the thread is killed before persisting, the next request that needs
  the data schedules a fresh fetch.
- **DB connections are thread-local.** A background thread that touches
  the ORM opens its own connection, separate from the request thread's.
  Long-running worker processes will leak connections under sustained
  background traffic unless the worker calls
  `django.db.connections.close_all()` in a `finally` clause. The
  `fetch_weather_async` worker does this.
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
