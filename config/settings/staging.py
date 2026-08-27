"""
config/settings/staging.py — Staging-environment overrides.

Staging inherits the full production hardening (see production.py) but runs
as a single web dyno with no background-tasks worker (render.yaml Staging
environment). Production dispatches subscription email from a separate
``python manage.py db_worker`` process consuming the DatabaseBackend queue;
staging has no such process, so a DatabaseBackend enqueue would persist the
task row and never send — silently, with no error in the logs.

This module overrides the task backend back to ImmediateBackend so email is
sent inline on the request, the same way development.py does. Staging's
volume is low enough that a synchronous SMTP send on the request is
acceptable; the alternative (a dedicated staging db_worker service) costs a
second dyno for no benefit at this scale.
"""

from .production import *  # noqa: F401, F403

# ---------------------------------------------------------------------------
# django-tasks background task queue — inline send, no worker required
# ---------------------------------------------------------------------------
# Staging has no db_worker service (render.yaml), so the production
# DatabaseBackend would leave queued email unconsumed. ImmediateBackend runs
# each task inline in the request process, so subscription email is sent
# synchronously — no worker to run, nothing to accumulate in the queue.

TASKS = {
    "default": {
        "BACKEND": "django_tasks.backends.immediate.ImmediateBackend",
    }
}


# ---------------------------------------------------------------------------
# Production data refresh — no Django wiring required
# ---------------------------------------------------------------------------
# Staging's bulletins, weather and resorts are refreshed from production by
# `bin/sync-staging-data`, which drives pg_dump/psql directly and reads
# PRODUCTION_DATABASE_URL from the environment (declared in base.py, so it is
# validated at deploy time and appears — redacted — in `dump_settings`).
#
# SNOW-736 removed the second DATABASES alias and the router that used to
# guard it. Both existed to let the ORM read production; nothing in Django
# connects there now, so the safest configuration is no configuration —
# there is no connection for a stray `migrate --database=…` to find.
