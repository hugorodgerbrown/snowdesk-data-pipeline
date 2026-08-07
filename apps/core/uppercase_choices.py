"""
apps/core/uppercase_choices.py — Shared one-time TextChoices-uppercasing helper.

Five `TextChoices` families predate the project's UPPER CASE storage
convention and still hold lower-case values in the database (SNOW-582). The
per-family fix is identical in every case — find rows whose stored value is
the lower-case form of one of the field's *current* choices, and rewrite it
to the matching upper-case member — so this module provides one streaming,
idempotent implementation that every one-off command calls per field, rather
than five near-identical ~200-line commands.

Lives alongside ``command_iteration.py`` (not inside it) because it is a
narrower, data-migration-specific tool built on top of that module's
streaming primitive, not another general iteration helper.
"""

from __future__ import annotations

import logging
from collections import Counter

from django.core.management.base import BaseCommand
from django.db import models

from apps.core.command_iteration import iterate_rows

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


def uppercase_field_values(
    cmd: BaseCommand,
    model: type[models.Model],
    field: str,
    *,
    commit: bool,
    verbosity: int,
) -> Counter[str]:
    """
    Rewrite every legacy lower-case value of ``field`` to its upper-case member.

    Reads ``field``'s ``choices`` — set by the model's ``TextChoices`` class —
    to build the lower→upper mapping, then streams
    ``model.objects.filter(**{f"{field}__in": lowercase_values})`` newest-id
    first via ``iterate_rows``, rewriting matches in batches of
    ``_BATCH_SIZE`` via ``bulk_update``.

    Idempotent by construction: once every matching row is rewritten, the
    lower-case values no longer exist in the table, so the queryset a second
    run selects is empty. Read-only by default — detection and counting
    still run so the caller can report a breakdown of what would change, but
    nothing is written unless ``commit`` is True.

    Args:
        cmd: The calling command, used (via ``iterate_rows``) for
            ``cmd.stdout`` so countdown output honours the command's own
            stream and is easy to capture in tests.
        model: The model whose ``field`` carries the choices to uppercase.
        field: Name of the ``CharField`` to rewrite.
        commit: When True, persist the uppercased values via ``bulk_update``.
        verbosity: Django's ``--verbosity`` level, forwarded to
            ``iterate_rows`` for the per-row countdown line.

    Returns:
        A ``Counter`` mapping each new (upper-case) value to how many rows
        were converted (or would be, in a dry run).

    """
    django_field = model._meta.get_field(field)
    if not isinstance(django_field, models.Field):
        raise TypeError(f"{model.__name__}.{field} is a relation, not a CharField.")
    valid_values: list[str] = [value for value, _label in (django_field.choices or [])]
    lower_to_upper: dict[str, str] = {
        value.lower(): value for value in valid_values if value.lower() != value
    }

    converted: Counter[str] = Counter()
    if not lower_to_upper:
        return converted

    manager = model._default_manager
    qs = manager.filter(**{f"{field}__in": list(lower_to_upper)})

    batch: list[models.Model] = []
    for obj in iterate_rows(cmd, qs, verbosity=verbosity):
        old_value = getattr(obj, field)
        new_value = lower_to_upper[old_value]
        converted[new_value] += 1

        if commit:
            setattr(obj, field, new_value)
            batch.append(obj)
            if len(batch) >= _BATCH_SIZE:
                manager.bulk_update(batch, [field], batch_size=_BATCH_SIZE)
                batch = []

    if commit and batch:
        manager.bulk_update(batch, [field], batch_size=_BATCH_SIZE)

    logger.info(
        "uppercase_field_values: model=%s field=%s converted=%d commit=%s",
        model.__name__,
        field,
        sum(converted.values()),
        commit,
    )

    return converted
