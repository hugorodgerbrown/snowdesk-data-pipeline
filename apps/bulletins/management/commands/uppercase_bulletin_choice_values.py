"""
apps/bulletins/management/commands/uppercase_bulletin_choice_values.py — one-time.

Three fields/keys predate the project's UPPER CASE storage convention and
hold rows written with the old lower-case values (SNOW-582):

- ``PipelineRun.status`` — ``"pending"``, ``"running"``, ``"success"``,
  ``"failed"``.
- ``Bulletin.source`` and ``RegionDayRating.source`` — ``"slf"``,
  ``"albina"``, ``"meteofrance"``.
- ``Bulletin.render_model["source"]`` — the same three values, stamped into
  the JSON render model by ``build_render_model`` rather than stored in a
  plain column.

This command rewrites all four to their upper-case form. The first three are
handled by the shared ``uppercase_field_values`` helper; the JSON key is a
targeted rewrite of exactly that one key — not a
``rebuild_render_models --commit`` (a no-op once every row's
``render_model_version`` is current) nor a forced ``--all`` (which would
regenerate every render-model field and refresh ``RegionDayRating`` for
every covered (region, day) — a far wider blast radius, at ~8,000 production
rows, on a code path that has OOM'd before).

Read-only by default — the detection and counting still run so the dry-run
reports a real breakdown of what would change, per field/key. Pass
``--commit`` to persist.

Each field's queryset (rows whose value is a lower-case form of a current
choice) is itself the idempotency mechanism — a second run after a
successful commit selects nothing.

Usage::

    # Read-only probe — how many rows would be converted, and to what?
    python manage.py uppercase_bulletin_choice_values

    # Persist the uppercased values.
    python manage.py uppercase_bulletin_choice_values --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from collections import Counter
from typing import Any

from django.core.management.base import BaseCommand

from apps.bulletins.models import Bulletin, PipelineRun, RegionDayRating
from apps.core.command_iteration import iterate_rows
from apps.core.uppercase_choices import uppercase_field_values

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


class Command(BaseCommand):
    """Uppercase legacy lower-case bulletins choice values, including the JSON key."""

    help = (
        "Rewrite PipelineRun.status, Bulletin.source, RegionDayRating.source, "
        "and Bulletin.render_model['source'] from their legacy lower-case "
        "stored values to their upper-case form. Read-only by default; "
        "pass --commit to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line flags."""
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Persist the uppercased values. Omit for a dry-run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the one-time uppercase conversion across all fields and the JSON key.

        Flags:
            --commit: Persist the uppercased values to the database.

        """
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Uppercasing bulletins choice values"
                + ("" if commit else " [READ-ONLY]")
            )
        )

        total = 0
        for label, model, field in (
            ("PipelineRun.status", PipelineRun, "status"),
            ("Bulletin.source", Bulletin, "source"),
            ("RegionDayRating.source", RegionDayRating, "source"),
        ):
            converted = uppercase_field_values(
                self, model, field, commit=commit, verbosity=verbosity
            )
            total += self._report_field(label, converted)

        json_converted = _uppercase_render_model_source(
            self, commit=commit, verbosity=verbosity
        )
        total += self._report_field('Bulletin.render_model["source"]', json_converted)

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        if commit:
            self.stdout.write(self.style.SUCCESS(f"Done — uppercased {total} row(s)."))
        else:
            self.stdout.write(
                f"Read-only run — would uppercase {total} row(s). "
                "Pass --commit to persist."
            )

        logger.info(
            "uppercase_bulletin_choice_values finished: converted=%d commit=%s",
            total,
            commit,
        )

    def _report_field(self, label: str, converted: Counter[str]) -> int:
        """Print one field/key's breakdown and return its converted total."""
        field_total = sum(converted.values())

        if field_total == 0:
            self.stdout.write(f"{label}: nothing to do.")
            return 0

        self.stdout.write(f"{label}:")
        for value, count in sorted(converted.items()):
            self.stdout.write(f"  {value}: {count}")
        return field_total


def _uppercase_render_model_source(
    cmd: BaseCommand,
    *,
    commit: bool,
    verbosity: int,
) -> Counter[str]:
    """
    Rewrite ``Bulletin.render_model["source"]`` for rows whose value is lower case.

    Unlike the plain-column fields, the render model is a JSON blob — there
    is no ``choices`` metadata to introspect, so this streams rows matched
    via the ``render_model__source`` JSON-path lookup directly rather than
    going through ``uppercase_field_values``. Only the ``"source"`` key is
    rewritten; every other render-model field is left untouched.

    Args:
        cmd: The calling command, used (via ``iterate_rows``) for
            ``cmd.stdout``.
        commit: When True, persist the rewritten render models via
            ``bulk_update``.
        verbosity: Django's ``--verbosity`` level, forwarded to
            ``iterate_rows`` for the per-row countdown line.

    Returns:
        A ``Counter`` mapping each new (upper-case) source value to how many
        rows were converted (or would be, in a dry run).

    """
    lower_to_upper: dict[str, str] = {
        value.lower(): value for value in Bulletin.Source.values
    }

    converted: Counter[str] = Counter()
    qs = Bulletin.objects.filter(render_model__source__in=list(lower_to_upper))

    batch: list[Bulletin] = []
    for bulletin in iterate_rows(cmd, qs, verbosity=verbosity):
        old_value = bulletin.render_model.get("source")
        new_value = lower_to_upper[old_value]
        converted[new_value] += 1

        if commit:
            bulletin.render_model["source"] = new_value
            batch.append(bulletin)
            if len(batch) >= _BATCH_SIZE:
                Bulletin.objects.bulk_update(
                    batch, ["render_model"], batch_size=_BATCH_SIZE
                )
                batch = []

    if commit and batch:
        Bulletin.objects.bulk_update(batch, ["render_model"], batch_size=_BATCH_SIZE)

    return converted
