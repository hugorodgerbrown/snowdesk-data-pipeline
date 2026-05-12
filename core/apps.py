"""
core/apps.py — AppConfig for the core application.

Holds shared abstractions used across other apps (notably ``BaseModel``).
Ships no concrete tables — registered as an installed app so Django
discovers it cleanly and so future shared queryset / manager utilities
have an obvious home.

Also defines ``BootstrapTolerantCSPTrackerConfig`` — a thin subclass of
the upstream ``csp.apps.CSPTrackerConfig`` that swallows database errors
during its eager ``reset()`` hook. ``django-csp-plus`` calls
``cache.delete("csp::rules")`` in its ``AppConfig.ready()`` on every
Django startup. When the ``DatabaseCache`` backend is configured
(production) but the ``django_cache`` table does not yet exist (first
``migrate`` against a fresh database), that delete raises
``ProgrammingError`` / ``OperationalError`` and aborts the migration
before the ``createcachetable`` data migration can run. The override
turns that one boot-time call into a logged no-op; subsequent boots
(after the cache table exists) run the upstream reset normally.
"""

import logging

from csp.apps import CSPTrackerConfig
from django.apps import AppConfig
from django.db import DatabaseError

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    """Django application configuration for the core app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"


class BootstrapTolerantCSPTrackerConfig(CSPTrackerConfig):
    """``csp.apps.CSPTrackerConfig`` that tolerates a missing cache table.

    Drop-in replacement for ``"csp"`` in ``INSTALLED_APPS``. The only
    behavioural change is in ``reset()``: any database error raised by
    the inner ``cache.delete()`` call is caught and logged at WARNING
    level instead of propagating. This unblocks the very first
    ``migrate`` against an empty database when the project uses the
    ``DatabaseCache`` backend.
    """

    def reset(self) -> None:
        """Clear the CSP cache, tolerating a missing ``django_cache`` table."""
        try:
            super().reset()
        except DatabaseError as exc:
            logger.warning(
                "Skipping CSP cache reset on startup — cache backend errored "
                "(likely the django_cache table doesn't exist yet on a fresh "
                "deploy). The createcachetable migration will create it; "
                "subsequent boots will reset cleanly. Underlying error: %s",
                exc,
            )
