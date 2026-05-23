"""
bulletins/management/commands/reformat_mf_comments.py — Management command.

Retroactively applies ``format_comment_as_html`` to the three prose-comment
fields (``snowpackStructure.comment``, ``avalancheActivity.comment``, and each
``tendency[i].comment``) in the ``raw_data`` JSONField of FR bulletins whose
comments are still plain-text (i.e. those ingested before SNOW-207 added the
HTML formatter to the translator).

The idempotency guard is simple: any comment that already contains ``<`` or
``>`` is left untouched.  Since ``format_comment_as_html`` HTML-escapes its
input, re-applying it to an already-formatted comment would double-escape
angle brackets, so we must skip those rows.

Read-only by default — pass ``--commit`` to persist changes (per the
project-wide management command convention).

Typical use::

    # Read-only walk (counts what would change, no DB writes).
    python manage.py reformat_mf_comments

    # Persist.
    python manage.py reformat_mf_comments --commit

Single bulletin::

    python manage.py reformat_mf_comments --bulletin-id FR-11-2026-05-18 --commit

Skip day-rating refresh::

    python manage.py reformat_mf_comments --commit --skip-day-ratings
"""

import copy
import datetime
import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from bulletins.models import Bulletin
from bulletins.services.day_rating import _target_day, recompute_region_day
from bulletins.services.meteofrance_translator import format_comment_as_html
from bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
)

logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 500


def _needs_formatting(text: Any) -> bool:
    """
    Return True if ``text`` is a non-empty plain-text string lacking HTML tags.

    A comment is considered already formatted if it contains ``<`` or ``>``.
    Empty strings and non-string values are skipped.

    Args:
        text: The comment value from ``raw_data``.

    Returns:
        ``True`` if the comment should be wrapped in HTML; ``False`` otherwise.

    """
    if not isinstance(text, str):
        return False
    if not text.strip():
        return False
    return "<" not in text and ">" not in text


def _upgrade_raw_data(raw_data: dict) -> tuple[dict, int]:
    """
    Apply ``format_comment_as_html`` to all three comment fields in ``raw_data``.

    Mutates a deep copy of the ``properties`` subtree and returns the number of
    fields that were actually upgraded (0 means nothing changed).

    Args:
        raw_data: The full ``raw_data`` dict (GeoJSON Feature envelope).

    Returns:
        A ``(new_raw_data, fields_upgraded)`` tuple.  ``new_raw_data`` is a
        new dict with the upgraded comments; ``fields_upgraded`` is the count
        of comment strings that were wrapped.

    """
    new_raw_data: dict = copy.deepcopy(raw_data)
    props: dict = new_raw_data.get("properties", {})
    fields_upgraded = 0

    # snowpackStructure.comment
    snowpack: dict = props.get("snowpackStructure", {})
    snowpack_comment = snowpack.get("comment", "")
    if _needs_formatting(snowpack_comment):
        snowpack["comment"] = format_comment_as_html(snowpack_comment)
        fields_upgraded += 1

    # avalancheActivity.comment
    activity: dict = props.get("avalancheActivity", {})
    activity_comment = activity.get("comment", "")
    if _needs_formatting(activity_comment):
        activity["comment"] = format_comment_as_html(activity_comment)
        fields_upgraded += 1

    # tendency[i].comment
    for entry in props.get("tendency", []):
        tendency_comment = entry.get("comment", "")
        if _needs_formatting(tendency_comment):
            entry["comment"] = format_comment_as_html(tendency_comment)
            fields_upgraded += 1

    new_raw_data["properties"] = props
    return new_raw_data, fields_upgraded


class Command(BaseCommand):
    """Reformat plain-text FR bulletin comments to HTML using format_comment_as_html."""

    help = (
        "Apply format_comment_as_html to the prose-comment fields of FR bulletins "
        "that were ingested before the HTML formatter was added (SNOW-207). "
        "Filters to bulletin_id__startswith='FR'. Read-only unless --commit is "
        "passed. Pass --skip-day-ratings to suppress the day-rating refresh step."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist reformatted raw_data and rebuilt render models to the "
                "database.  Without this flag the command is read-only."
            ),
        )
        parser.add_argument(
            "--bulletin-id",
            metavar="ID",
            help="Restrict to a single bulletin by its bulletin_id.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=_DEFAULT_BATCH_SIZE,
            metavar="N",
            help=(
                f"Process bulletins in batches of N (default: {_DEFAULT_BATCH_SIZE})."
            ),
        )
        parser.add_argument(
            "--skip-day-ratings",
            action="store_true",
            help="Skip the RegionDayRating refresh step after reformatting.",
        )

    def _build_queryset(self, bulletin_id_arg: str | None) -> Any:
        """
        Build the queryset of FR bulletins to inspect.

        Args:
            bulletin_id_arg: Optional bulletin_id to restrict to a single row.

        Returns:
            A Bulletin queryset ordered by pk.

        Raises:
            CommandError: If ``bulletin_id_arg`` is supplied but not found.

        """
        if bulletin_id_arg:
            if not bulletin_id_arg.startswith("FR"):
                raise CommandError(
                    f"--bulletin-id must start with 'FR'; got {bulletin_id_arg!r}. "
                    "This command is scoped to French bulletins only."
                )
            qs = Bulletin.objects.filter(bulletin_id=bulletin_id_arg)
            if not qs.exists():
                raise CommandError(
                    f"No bulletin found with bulletin_id={bulletin_id_arg!r}"
                )
            return qs.order_by("pk")
        return Bulletin.objects.filter(bulletin_id__startswith="FR").order_by("pk")

    def _process_bulletin(
        self, bulletin: Bulletin, *, commit: bool
    ) -> tuple[bool, bool, int]:
        """
        Reformat comments on one bulletin and optionally write to the database.

        Args:
            bulletin: The Bulletin to inspect and potentially upgrade.
            commit: If True, persist changes; otherwise report only.

        Returns:
            A ``(changed, success, fields_upgraded)`` tuple.  ``changed`` is
            True if at least one comment field was upgraded.  ``success`` is
            False if ``build_render_model`` raised ``RenderModelBuildError``
            (in which case an error sentinel is written and the caller should
            count this as a failure).

        """
        if not bulletin.raw_data:
            return False, True, 0

        new_raw_data, fields_upgraded = _upgrade_raw_data(bulletin.raw_data)
        if fields_upgraded == 0:
            return False, True, 0

        if not commit:
            logger.info(
                "[read-only] Would reformat bulletin %s (%d field(s))",
                bulletin.bulletin_id,
                fields_upgraded,
            )
            return True, True, fields_upgraded

        props = new_raw_data.get("properties", {})
        try:
            new_render_model = build_render_model(props)
            new_version = RENDER_MODEL_VERSION
            success = True
        except RenderModelBuildError as exc:
            logger.exception(
                "Failed to build render model for bulletin %s: %s",
                bulletin.bulletin_id,
                exc,
            )
            new_render_model = {
                "version": 0,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
            }
            new_version = 0
            success = False

        Bulletin.objects.filter(pk=bulletin.pk).update(
            raw_data=new_raw_data,
            render_model=new_render_model,
            render_model_version=new_version,
        )
        if success:
            logger.info(
                "Reformatted bulletin %s (%d field(s))",
                bulletin.bulletin_id,
                fields_upgraded,
            )
        return True, success, fields_upgraded

    def _refresh_day_ratings(self, bulletins: list[Bulletin]) -> int:
        """
        Recompute RegionDayRating for every (region, day) touched by ``bulletins``.

        Deduplicates pairs before calling ``recompute_region_day`` so a region+day
        covered by several bulletins is only recomputed once.

        Args:
            bulletins: Bulletins whose day ratings should be refreshed.

        Returns:
            The number of (region, day) pairs that failed to recompute.

        """
        pairs: set[tuple[Any, datetime.date]] = set()
        for bulletin in bulletins:
            regions = list(bulletin.regions.all())
            day = _target_day(bulletin)
            for region in regions:
                pairs.add((region, day))

        self.stdout.write(
            f"Refreshing day ratings for {len(pairs)} (region, day) pair(s)."
        )
        rating_failures = 0
        for region, day in pairs:
            try:
                recompute_region_day(region, day, commit=True)
            except Exception:
                rating_failures += 1
                logger.exception(
                    "Failed to refresh day rating for region=%s day=%s",
                    region.region_id,
                    day,
                )
        self.stdout.write(self.style.SUCCESS("Day ratings refreshed."))
        return rating_failures

    def _process_in_batches(
        self, qs: Any, total: int, batch_size: int, commit: bool
    ) -> tuple[int, int, list[Bulletin]]:
        """
        Iterate the queryset in pk-ordered batches, processing each bulletin.

        Returns:
            A ``(changed_count, errored, successfully_changed_bulletins)`` tuple.
            ``successfully_changed_bulletins`` contains only bulletins that were
            both changed and built without error, so day ratings are not refreshed
            for error sentinels.

        """
        changed_count = 0
        errored = 0
        changed_bulletins: list[Bulletin] = []
        offset = 0

        while offset < total:
            batch_ids = list(
                qs.values_list("pk", flat=True)[offset : offset + batch_size]
            )
            if not batch_ids:
                break

            batch = Bulletin.objects.filter(pk__in=batch_ids).order_by("pk")
            for bulletin in batch:
                changed, success, _ = self._process_bulletin(bulletin, commit=commit)
                if changed:
                    changed_count += 1
                    if success:
                        changed_bulletins.append(bulletin)
                    else:
                        errored += 1

            offset += batch_size

        return changed_count, errored, changed_bulletins

    def handle(self, *args: Any, **options: Any) -> None:
        """
        Execute the reformat command.

        Default behaviour: walks all FR bulletins in pk order, inspects the
        three prose-comment fields, and counts how many would change. Pass
        ``--commit`` to persist changes. After reformatting, refreshes
        RegionDayRating for every covered (region, day) unless
        ``--skip-day-ratings`` is passed.

        Flags:
            --commit: Persist reformatted data to the database.
            --bulletin-id: Restrict to a single bulletin by its bulletin_id.
            --batch-size N: Override the default batch size.
            --skip-day-ratings: Skip the RegionDayRating refresh step.

        """
        commit: bool = options["commit"]
        bulletin_id_arg: str | None = options["bulletin_id"]
        batch_size: int = options["batch_size"]
        skip_day_ratings: bool = options["skip_day_ratings"]

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                "Reformatting MF bulletin comments"
                + (f" [bulletin-id={bulletin_id_arg}]" if bulletin_id_arg else "")
                + ("" if commit else " [READ-ONLY] — pass --commit to persist")
            )
        )

        qs = self._build_queryset(bulletin_id_arg)
        total = qs.count()
        self.stdout.write(f"FR bulletins to inspect: {total}")

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        changed_count, errored, changed_bulletins = self._process_in_batches(
            qs, total, batch_size, commit
        )

        if not commit:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Read-only run complete — {changed_count} bulletin(s) would be "
                    f"reformatted. Pass --commit to persist."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Reformatted {changed_count} bulletin(s), {errored} failed."
                )
            )

        logger.info(
            "reformat_mf_comments finished: changed=%d errored=%d commit=%s",
            changed_count,
            errored,
            commit,
        )

        rating_failures = 0
        if commit and not skip_day_ratings and changed_bulletins:
            rating_failures = self._refresh_day_ratings(changed_bulletins)

        if errored > 0 or rating_failures > 0:
            raise CommandError(
                f"{errored} bulletin(s) failed render-model rebuild; "
                f"{rating_failures} day-rating recompute(s) failed."
            )
