# Async operations

Background work — anything dispatched off the request cycle — is a classic
source of untraceable errors.  This doc catalogues every fire-and-forget
callsite in the Snowdesk codebase, the failure mode for each, and the on/off
toggle if one exists.

## Catalogue

| Callsite | Trigger | Work | Persistence | Failure mode | Log channel | Toggle |
|----------|---------|------|-------------|--------------|-------------|--------|
| `subscriptions.services.email` — three `@task` workers (`_worker_send_account_access`, `_worker_send_subscription_confirmation`, `_worker_simulate`) enqueued via `django.tasks` | Any of the three public `send_*` functions on the subscriptions flow | SMTP send via Django's configured backend (ImmediateBackend runs inline; DummyBackend drops; a future DatabaseBackend would persist for the `db_worker` to consume) | Determined by `TASKS["default"]` backend; ImmediateBackend has no persistence | Exception propagated to and logged by the backend; task is marked FAILED | `django.tasks` (backend-level) + `subscriptions.services.email` (worker-level) | `TASKS["default"]["BACKEND"]` setting |
| `bulletins.services.weather_fetcher.fetch_weather_async` | `bulletin_detail` page render where no `WeatherSnapshot` exists for `(region, target_date)` and `target_date < today` | Idempotent DB pre-check, then `fetch_archive_for_region` / `fetch_weather_for_region` (Open-Meteo) | `WeatherSnapshot` row via `update_or_create` | `Exception` caught, logged at WARNING via `logger.warning(exc_info=True)`; `connections.close_all()` in `finally` so the thread releases its DB connection | `bulletins.services.weather_fetcher` | `WEATHER_FETCH_ASYNC` (default True; tests pin False) |

## django.tasks

Subscription emails are dispatched through `django.tasks` (SNOW-229).  The
relevant settings key is `TASKS["default"]["BACKEND"]`:

- **`django.tasks.backends.immediate.ImmediateBackend`** (current default in
  all environments) — runs each enqueued task synchronously inline, in the
  same request-handling thread.  No off-process worker is required.  A failed
  task raises an exception that propagates normally; there is no retry.
  Simple and safe; the task is never lost because it runs before the response
  is sent.

- **`django.tasks.backends.dummy.DummyBackend`** — enqueues tasks without
  executing them; used in the SNOW-26 timing test to replicate the response
  profile of a non-executing (off-process) backend without setting up a real
  worker.

- **`django.tasks.backends.database.DatabaseBackend`** — *not yet available
  in Django 6.0.5*.  When it ships in a future Django release, upgrade
  `TASKS` in `config/settings/production.py` and add `"django.tasks"` to
  `INSTALLED_APPS` (the migration creates a task-results table), then run a
  Render Background Worker service executing `python manage.py db_worker` to
  consume the queue.  Until then, `ImmediateBackend` is the production
  backend — emails are sent inline and cannot be silently dropped on a worker
  restart.

## Django background-thread caveats (fetch_weather_async only)

The weather-fetch helper still uses the raw `threading.Thread` idiom.  These
caveats apply to it but **not** to the `django.tasks`-backed email dispatch:

- **Daemon threads do not block worker shutdown.** Gunicorn killing or
  recycling a worker (`--max-requests`, SIGTERM) leaves any in-flight
  daemon work *unfinished*.  All async work here is therefore idempotent —
  if the thread is killed before persisting, the next request that needs
  the data schedules a fresh fetch.
- **DB connections are thread-local.** A background thread that touches
  the ORM opens its own connection, separate from the request thread's.
  Long-running worker processes will leak connections under sustained
  background traffic unless the worker calls
  `django.db.connections.close_all()` in a `finally` clause.  The
  `fetch_weather_async` worker does this.
- **No request-cycle transaction.** Background threads run *outside* the
  request's atomic transaction.  If you need atomicity across multiple DB
  writes inside the worker, wrap them in your own
  `transaction.atomic()` block — don't assume the request's transaction
  is in scope.
- **No request context.** `request.user`, `request.session`, locale,
  and `django.utils.timezone.override` are not in scope inside the
  worker.  Pass anything the worker needs as plain arguments.

## When to add a new async operation

### Using django.tasks (preferred for new work)

1. Decorate the worker function with `@task`; accept only JSON-serialisable
   primitives (str, int, float, bool, None, list, dict) so any future
   persistent backend can serialise and replay the task.
2. Extract any request-derived values (base URL, user ID, etc.) in the
   public wrapper before calling `.enqueue()`; the worker must carry no
   request dependency.
3. Add the callsite to the catalogue above with all columns filled in.
4. Add a test that exercises the worker via `ImmediateBackend` (the default
   in dev/test settings); no autouse fixture needed.

### Using raw threading (legacy — avoid for new work)

1. Add the callsite to the catalogue above with all six columns filled in.
2. Mirror the on/off toggle pattern: a `settings.<NAME>_ASYNC` (default
   `True`) and a pin in `tests/conftest.py`.
3. Wrap the work in `try/except Exception → logger.<level>(..., exc_info=True)`.
4. Close DB connections in `finally` if the work touches the ORM.
5. Make the work idempotent inside the worker — the snapshot/row might already
   exist by the time the worker runs.
