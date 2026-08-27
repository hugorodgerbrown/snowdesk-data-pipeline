"""
apps/core/db_routers.py — Database routers.

One router, and it exists for a single purpose: keep Django's schema
machinery away from the read-only ``production`` alias that
``config/settings/staging.py`` registers for
:mod:`apps.core.services.production_sync`.

``manage.py migrate`` only targets ``default`` unless ``--database`` says
otherwise, so nothing routine would reach the production alias by accident —
but ``migrate --database=production`` is one typo away from applying
staging's unreleased migrations to the live database, and ``allow_migrate``
is the hook that makes that typo a no-op rather than an outage.

This is the second of two defences. The first is that the production
credentials handed to staging belong to a **read-only** Postgres role, which
is what actually guarantees the sync cannot write; see
``docs/runbooks/refresh-staging-from-production.md``.
"""

from __future__ import annotations

from typing import Any

from apps.core.services.production_sync import PRODUCTION_ALIAS


class ProductionReadOnlyRouter:
    """Block schema operations against the read-only production alias.

    Reads and writes are unrouted (``None`` — no opinion), so ordinary
    application queries keep going to ``default`` and the sync's explicit
    raw cursors keep going where they ask. Only ``allow_migrate`` takes a
    position.
    """

    def allow_migrate(self, db: str, app_label: str, **hints: Any) -> bool | None:
        """Refuse every migration against the production alias.

        Args:
            db: The alias ``migrate`` is running against.
            app_label: The app being migrated.
            **hints: Django's routing hints, unused here.

        Returns:
            ``False`` for the production alias, disallowing the operation;
            ``None`` (no opinion) for every other alias.

        """
        if db == PRODUCTION_ALIAS:
            return False
        return None
