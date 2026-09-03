"""backfill_resort_slugs — mint ``Resort.slug`` for every row that has none.

One-shot backfill for SNOW-796. ``Resort.slug`` is the resort's URL
identifier (``/resorts/<slug>/``) and the ``id`` that ``resorts.geojson``
emits, replacing the integer primary key those surfaces used to expose
(``docs/decisions/no-integer-pks-in-urls.md``). ``Resort.save()`` mints a
slug for every row created after the column landed; this command reaches
the rows that pre-date it.

**Not a data migration.** CLAUDE.md forbids bulk dataset updates in
migrations, so the schema migration adds a nullable column and writes
nothing; this ``--commit``-gated command fills it, and a later migration
tightens the column to ``null=False`` once every environment has run it.

**A collision is a failure, not a suffix.** All curated resorts slugify
distinctly today, so two rows wanting the same slug means something is
wrong with the data — the command records the failure, leaves both rows
alone and exits non-zero, rather than quietly minting ``verbier-2`` for a
page a search engine will index under that name. (``Resort.save()`` does
suffix, but only for a row created after the backfill, where the
operator is present to see it.)

Idempotent: the candidate queryset is the rows with no slug, so a second
run selects zero rows and never rewrites one — a slug is never regenerated.

Usage:
    # Preview — reports what would be written, writes nothing.
    uv run python manage.py backfill_resort_slugs

    # Apply it.
    uv run python manage.py backfill_resort_slugs --commit
"""

from __future__ import annotations

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils.text import slugify

from apps.core.command_iteration import iterate_rows
from apps.regions.models import Resort

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Mint a slug for every Resort that has none.

    Read-only by default; pass --commit to persist. Exits non-zero if any
    row collided, so a partial run is detectable.
    """

    help = (
        "Mint Resort.slug from the name for every row that has none "
        "(SNOW-796). Read-only unless --commit."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist the minted slugs. Without this flag the command "
                "reports and writes nothing."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Mint a slug for every candidate resort."""
        commit: bool = options["commit"]
        verbosity: int = options["verbosity"]

        candidates = Resort.objects.filter(slug__isnull=True)
        total = candidates.count()

        flag_label = "" if commit else " [READ-ONLY]"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Minting a slug for {total} resort(s){flag_label}"
            )
        )
        logger.info(
            "backfill_resort_slugs started: candidates=%d commit=%s", total, commit
        )

        # Every slug already held, plus every one planned in this run —
        # so a collision between two candidates is caught in the dry run,
        # not by the unique constraint on the second write.
        taken: set[str] = {
            slug
            for slug in Resort.objects.exclude(slug__isnull=True).values_list(
                "slug", flat=True
            )
            if slug
        }
        counts = {"minted": 0, "failed": 0}
        for resort in iterate_rows(
            self,
            candidates,
            verbosity=verbosity,
            describe=lambda row: f"{row.pk} {row.name}",
        ):
            self._backfill_one(resort, taken, counts, commit=commit)

        self._report_outcome(counts, commit=commit, verbosity=verbosity)

        if counts["failed"] > 0:
            raise CommandError(
                f"backfill_resort_slugs completed with {counts['failed']} "
                "failure(s). Check logs for details."
            )

    def _backfill_one(
        self,
        resort: Resort,
        taken: set[str],
        counts: dict[str, int],
        *,
        commit: bool,
    ) -> None:
        """Mint one resort's slug and, when committing, persist it.

        Args:
            resort: The resort to back-fill.
            taken: Every slug held or planned so far; mutated in place.
            counts: The running tally, mutated in place.
            commit: Whether to persist.

        """
        slug = slugify(resort.name)
        if not slug or slug in taken:
            logger.error(
                "backfill_resort_slugs: resort id=%s name=%r wants slug %r, "
                "which is empty or already taken; left unchanged",
                resort.pk,
                resort.name,
                slug,
            )
            counts["failed"] += 1
            return
        taken.add(slug)
        counts["minted"] += 1
        if not commit:
            return
        resort.slug = slug
        resort.save(update_fields=["slug", "updated_at"])

    def _report_outcome(
        self, counts: dict[str, int], *, commit: bool, verbosity: int
    ) -> None:
        """Emit the post-run summary to stdout and the structured log."""
        if verbosity >= 1:
            if commit:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Done. {counts['minted']} slug(s) minted, "
                        f"{counts['failed']} failed."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Read-only run complete — {counts['minted']} slug(s) "
                        "would be minted. No data written. Pass --commit to "
                        "persist."
                    )
                )
        logger.info(
            "backfill_resort_slugs finished: minted=%d failed=%d commit=%s",
            counts["minted"],
            counts["failed"],
            commit,
        )
