"""
apps/core/views.py — Infrastructure health-check endpoints (SNOW-565).

Two deliberately separate probes, because the two questions they answer have
different consequences when the answer is "no":

* ``/livez`` — is this WSGI process alive? Touches nothing: no database, no
  session, no cache. This is the path wired to Render's ``healthCheckPath``,
  so a failure here means "replace this instance". Coupling it to Postgres
  would turn a transient database blip into a blocked deploy or a restart
  loop while the site was still serving perfectly well from its caches.
* ``/healthz`` — can the process serve a real request end to end? Runs
  ``/livez`` plus one read against the ``auth_user`` table. This is for
  external monitoring, which can alert a human without taking the service
  down.

The database probe reads a real application table rather than issuing
``SELECT 1``. ``SELECT 1`` only proves the socket answers; a read of
``auth_user`` additionally proves that migrations have run and that the
connected role can read the application schema. An empty table is still a
healthy database — the assertion is that the query *completes*, not that it
returns a row.

Both endpoints are public and return a fixed one-word ``text/plain`` body.
There is no JSON detail and no subsystem breakdown: a health check that
enumerates its own internals publishes infrastructure state to anyone who
asks, and nothing consuming these needs more than the status code.
"""

from __future__ import annotations

import logging

from django.contrib.auth.models import User
from django.db import DatabaseError
from django.http import HttpRequest, HttpResponse
from django.views.decorators.cache import never_cache

logger = logging.getLogger(__name__)


@never_cache
def livez(request: HttpRequest) -> HttpResponse:
    """Return 200 when the WSGI process is alive.

    Deliberately does no work. In particular it must never read
    ``request.user``: that access triggers a session lookup, which would
    silently make the liveness probe depend on the database and defeat the
    whole point of the split from ``healthz``. ``/livez`` is exempt from
    ``PosthogContextMiddleware`` for the same reason — see
    ``_POSTHOG_EXEMPT_PATHS`` in ``config/settings/base.py``.

    Args:
        request: The incoming HTTP request (unused — the response is fixed).

    Returns:
        A 200 ``text/plain`` response with the body ``ok``.

    """
    return HttpResponse("ok", content_type="text/plain; charset=utf-8")


@never_cache
def healthz(request: HttpRequest) -> HttpResponse:
    """Return 200 when the process is alive and the database can serve reads.

    Issues ``User.objects.exists()``, which compiles to
    ``SELECT 1 … LIMIT 1`` against ``auth_user`` — cheap, but a genuine read
    of an application table rather than a bare connection ping. The boolean
    result is discarded: an empty user table is a healthy database.

    Database failures are caught and reported as a 503 with a fixed body.
    ``DatabaseError`` is the catch: every connection-level failure Django
    raises (``OperationalError``, ``InterfaceError``) subclasses it. The
    exception is logged, never returned — an error string from the driver
    can carry the host, port, and role of the database.

    Args:
        request: The incoming HTTP request (unused — the response depends
            only on whether the database answers).

    Returns:
        A 200 ``text/plain`` response with the body ``ok``, or a 503 with
        the body ``error`` when the database is unreachable.

    """
    try:
        User.objects.exists()
    except DatabaseError:
        logger.exception("Health check failed: database is unreachable")
        return HttpResponse(
            "error", content_type="text/plain; charset=utf-8", status=503
        )
    return HttpResponse("ok", content_type="text/plain; charset=utf-8")
