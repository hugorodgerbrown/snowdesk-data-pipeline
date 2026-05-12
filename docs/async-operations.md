# Async operations

Background-thread work — anything dispatched off the request cycle — is a
classic source of untraceable errors. This doc catalogues every fire-and-forget
callsite in the Snowdesk codebase, the failure mode for each, and the on/off
toggle if one exists.

## Catalogue

| Callsite | Trigger | Work | Persistence | Failure mode | Log channel | Toggle |
|----------|---------|------|-------------|--------------|-------------|--------|
| `subscriptions.services.email._dispatch_async` | Any of the three `send_*_email` functions on the subscriptions flow | SMTP send via Django's configured backend | None (mail provider is the persistence) | `Exception` caught, logged at ERROR via `logger.exception` | `subscriptions.services.email` | `SUBSCRIPTIONS_EMAIL_ASYNC` (default True; tests pin False) |
| `bulletins.services.weather_fetcher.fetch_weather_async` | `bulletin_detail` page render where no `WeatherSnapshot` exists for `(region, target_date)` and `target_date < today` | Idempotent DB pre-check, then `fetch_archive_for_region` / `fetch_weather_for_region` (Open-Meteo) | `WeatherSnapshot` row via `update_or_create` | `Exception` caught, logged at WARNING via `logger.warning(exc_info=True)`; `connections.close_all()` in `finally` so the thread releases its DB connection | `bulletins.services.weather_fetcher` | `WEATHER_FETCH_ASYNC` (default True; tests pin False) |

## Django background-thread caveats

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
  `fetch_weather_async` worker does this; `_dispatch_async` does not
  need to because the email path does not touch the ORM.
- **No request-cycle transaction.** Background threads run *outside* the
  request's atomic transaction. If you need atomicity across multiple DB
  writes inside the worker, wrap them in your own
  `transaction.atomic()` block — don't assume the request's transaction
  is in scope.
- **No request context.** `request.user`, `request.session`, locale,
  and `django.utils.timezone.override` are not in scope inside the
  worker. Pass anything the worker needs as plain arguments.

## When to add a new async operation

1. Add the callsite to the catalogue above with all six columns filled in.
2. Mirror the on/off toggle pattern: a `settings.<NAME>_ASYNC` (default
   `True`) and a pin in `tests/conftest.py`.
3. Wrap the work in `try/except Exception → logger.<level>(..., exc_info=True)`.
4. Close DB connections in `finally` if the work touches the ORM.
5. Make the work idempotent inside the worker — the snapshot/row might already
   exist by the time the worker runs.
