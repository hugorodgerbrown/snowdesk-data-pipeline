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
# Read-only production database — source for `manage.py sync_from_production`
# ---------------------------------------------------------------------------
# Staging has no scheduler and no task worker, so its database never ingests
# a bulletin or a weather forecast of its own (render.yaml). The
# `sync_from_production` command copies the provider-derived tables across
# from production; this is the connection it reads them through.
#
# Registered HERE and nowhere else, on purpose. production.py has no such
# alias, so the command cannot run from production's own settings module —
# `check_safe_to_write` fails on the missing alias before it touches a row.
#
# PRODUCTION_DATABASE_URL must hold credentials for a **read-only** role.
# Nothing in the sync issues a write against this alias, and the router
# below blocks migrations on it, but the role is the guarantee — the code is
# only the intent. See docs/runbooks/refresh-staging-from-production.md.
#
# `PRODUCTION_DATABASE_URL` itself is read in base.py so it resolves under
# every settings module; this overlay is the only place that turns it into a
# connection. Unset by default: staging runs perfectly well without it, and
# the sync then refuses to start rather than silently doing nothing.
if PRODUCTION_DATABASE_URL:  # noqa: F405
    DATABASES["production"] = dj_database_url.parse(  # noqa: F405
        PRODUCTION_DATABASE_URL,
        conn_max_age=0,
        ssl_require=True,
    )

    # Keeps `migrate --database=production` a no-op. See the module docstring
    # in apps/core/db_routers.py for why that specific typo is worth a router.
    DATABASE_ROUTERS = ["apps.core.db_routers.ProductionReadOnlyRouter"]
