"""
apps/core/contenttype_migrations.py — shared ContentType-row rewrite helper.

``django_content_type`` is keyed on ``(app_label, model)``, so any model or
app-label rename strands its row unless a migration rewrites that key.
Left alone, ``post_migrate`` mints a fresh row under the new identity and
every ``auth_permission`` and admin ``LogEntry`` — which hold a
``content_type_id``, not the label/model text — stays pointed at the
orphaned old one.

``rewrite_content_type_rows`` is the shape both
``weather/migrations/0002_move_content_types.py`` (an app-label move, for
SNOW-654) and ``weather/migrations/0005_rename_content_types.py`` (a model
rename, for SNOW-703) need: for each row, look it up under its old
identity, drop a history-less duplicate already sitting under the new
identity, then rewrite. It says nothing about ``auth_permission.codename``
— that only needs rewriting on a model-name change (codename embeds the
model name, not the app label), so that step lives next to the caller that
actually needs it, not here.

Import at migration module scope is safe: this module imports nothing from
``django.apps``/``django.db`` beyond typing, and defers its own
``ContentType`` import to inside the function body for the same reason the
two migrations already deferred theirs — the concrete model class is not
guaranteed importable before the app registry has finished loading.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from django.apps.registry import Apps


def rewrite_content_type_rows(
    apps: Apps,
    moves: Iterable[tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]],
) -> None:
    """
    Rewrite ContentType rows, dropping a history-less duplicate first.

    Idempotent in both directions, and converges on exactly one row per
    ``(old_lookup, new_lookup)`` pair whichever order things happened in.

    When both the old and new identity hold a row for the same underlying
    table, the **old** one survives: it is the pre-change row, so it is
    the one every ``auth_permission`` and admin ``LogEntry`` points at via
    ``content_type_id``. The new-identity row in that case was minted by
    ``post_migrate`` and carries no history, so it is the one that can be
    dropped without losing anything. Deleting the wrong one cascades away
    exactly the rows this function exists to preserve.

    Args:
        apps: The historical app registry supplied by ``RunPython``.
        moves: ``(old_lookup, new_lookup, new_values)`` triples.
            ``old_lookup`` finds the pre-change row; ``new_lookup`` finds a
            same-table duplicate ``post_migrate`` may already have minted
            under the new identity; ``new_values`` are the fields to write
            onto the surviving (old) row.

    """
    ContentType = apps.get_model("contenttypes", "ContentType")

    for old_lookup, new_lookup, new_values in moves:
        source = ContentType.objects.filter(**old_lookup).first()
        if source is None:
            # Nothing to rewrite — a fresh database, or already migrated.
            continue

        target = ContentType.objects.filter(**new_lookup).first()
        if target is not None:
            # Drop the history-less duplicate first: the lookup fields are
            # unique together, so the rewrite below would collide with it.
            target.delete()

        for field, value in new_values.items():
            setattr(source, field, value)
        source.save(update_fields=list(new_values))

    # The real ContentType manager caches lookups per (app_label, model);
    # the historical model above has no such cache, so clear it on the
    # concrete class or the rest of this migrate run reads stale rows.
    from django.contrib.contenttypes.models import (  # noqa: PLC0415 — deferred so the module stays importable without app loading
        ContentType as ConcreteContentType,
    )

    ConcreteContentType.objects.clear_cache()
