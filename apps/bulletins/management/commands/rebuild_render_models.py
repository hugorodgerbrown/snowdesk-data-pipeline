"""
apps/bulletins/management/commands/rebuild_render_models.py — Management command.

Enqueues one background task per Bulletin whose ``render_model_version`` is
older than the current ``RENDER_MODEL_VERSION`` (or per bulletin, with
``--all``, or one bulletin, with ``--bulletin-id``). Each task
(``apps.bulletins.services.render_rebuild.rebuild_bulletin_task``) rebuilds
the bulletin's ``render_model`` from the CAAML the ingest service stored
for it (``SourceBulletin``, SNOW-1337), writes it with the
``danger_levels`` column read off its ``derived`` block (SNOW-1341), and
recomputes the RegionDayRating of every (region, day) it governs, in one
transaction (pass ``--skip-day-ratings`` to suppress the recompute).

**SNOW-1346: the command enqueues, the task runner works** (CLAUDE.md
command rule 7). Walking every bulletin in one process held a shell for as
long as the archive took; now ``--commit`` returns in seconds on a queued
backend (``DatabaseBackend`` + ``db_worker`` in production), and the tasks
run at priority -50, behind alert emails and pushes. Under
``ImmediateBackend`` (dev, test, staging) each task runs inline as it is
enqueued, and a failed one counts towards the exit code. On a queued
backend a failure shows on the task result and in the worker log; re-run
the preview to read how many rows are still stale.

**Idempotent.** A task re-reads its bulletin and skips it when it is gone
or already current, so a second run, or a bulletin enqueued twice, writes
nothing. ``--all`` and ``--bulletin-id`` force the rebuild of a current row.

Streams slim rows newest-id-first via ``apps.core.command_iteration
.iterate_rows`` (SNOW-602): the walk loads only what the countdown label
reads, never CAAML.

Read-only by default — pass ``--commit`` to enqueue (per the project-wide
management command convention).

Typical use after a builder logic change::

    # Preview: how many bulletins would be enqueued.
    python manage.py rebuild_render_models

    # Enqueue.
    python manage.py rebuild_render_models --commit

Backfill everything::

    python manage.py rebuild_render_models --all --commit

Single bulletin::

    python manage.py rebuild_render_models --bulletin-id <id> --commit

Skip day-rating refresh::

    python manage.py rebuild_render_models --commit --skip-day-ratings
"""

import logging
from argparse import ArgumentParser
from typing import Any

from django.core.management.base import CommandError
from django_tasks import TaskResultStatus

from apps.bulletins.models import Bulletin
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.bulletins.services.render_rebuild import rebuild_bulletin_task
from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand

logger = logging.getLogger(__name__)

_PREFETCH_CHUNK_SIZE = 500

# The columns Bulletin.row_label reads: the walk loads nothing else.
_LABEL_FIELDS: tuple[str, ...] = (
    "pk",
    "bulletin_id",
    "source",
    "target_date",
    "valid_from",
)


class Command(RegisteredCommand):
    """Enqueue a render-model rebuild task per stale or specified Bulletin."""

    # Offered to the ops MCP server's run_command (apps.core.remote_commands).
    mcp_runnable = True

    help = (
        "Enqueue one background task per Bulletin row that is stale "
        "(render_model_version < RENDER_MODEL_VERSION); each task rebuilds "
        "the render model and refreshes the RegionDayRating rows of every "
        "(region, day) it governs. Read-only unless --commit is passed. "
        "Pass --skip-day-ratings to suppress the day-rating refresh step."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Enqueue the rebuild tasks. Without this flag the command is read-only."
            ),
        )
        parser.add_argument(
            "--all",
            action="store_true",
            dest="rebuild_all",
            help="Rebuild every bulletin, not just stale ones.",
        )
        parser.add_argument(
            "--bulletin-id",
            metavar="ID",
            help="Rebuild a single bulletin by its bulletin_id, even if current.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=_PREFETCH_CHUNK_SIZE,
            metavar="N",
            help=(
                "Chunk size for the streamed queryset iterator "
                f"(default: {_PREFETCH_CHUNK_SIZE})."
            ),
        )
        parser.add_argument(
            "--skip-day-ratings",
            action="store_true",
            help="Skip the RegionDayRating refresh step in each task.",
        )

    def _build_queryset(self, bulletin_id_arg: str | None, rebuild_all: bool) -> Any:
        """
        Build the queryset of bulletins to enqueue, slim.

        Args:
            bulletin_id_arg: Optional bulletin_id to restrict to a single row.
            rebuild_all: If True, return all bulletins.

        Returns:
            A Bulletin queryset loading only the countdown label's columns.

        Raises:
            CommandError: If bulletin_id_arg is specified but not found.

        """
        rows = Bulletin.objects.only(*_LABEL_FIELDS)
        if bulletin_id_arg:
            qs = rows.filter(bulletin_id=bulletin_id_arg)
            if not qs.exists():
                raise CommandError(
                    f"No bulletin found with bulletin_id={bulletin_id_arg!r}"
                )
            return qs
        if rebuild_all:
            return rows
        return rows.needs_render_model_rebuild(RENDER_MODEL_VERSION)

    def handle(self, *args: Any, **options: Any) -> None:
        """Enqueue a rebuild task per selected bulletin.

        Flags:
            --commit: Enqueue the tasks.
            --all: Every bulletin, forced, regardless of stored version.
            --bulletin-id: One bulletin by its bulletin_id, forced.
            --batch-size N: Chunk size for the streamed queryset iterator.
            --skip-day-ratings: Tasks skip the RegionDayRating refresh.

        Raises:
            CommandError: An unknown --bulletin-id, or a task run inline
                failed.

        """
        commit: bool = options["commit"]
        rebuild_all: bool = options["rebuild_all"]
        bulletin_id_arg: str | None = options["bulletin_id"]
        chunk_size: int = options["batch_size"]
        skip_day_ratings: bool = options["skip_day_ratings"]
        verbosity: int = options["verbosity"]
        force = rebuild_all or bool(bulletin_id_arg)

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Enqueuing render model rebuilds (version={RENDER_MODEL_VERSION})"
                + (" [ALL]" if rebuild_all else "")
                + (f" [bulletin-id={bulletin_id_arg}]" if bulletin_id_arg else "")
                + ("" if commit else " [READ-ONLY]")
            )
        )

        try:
            qs = self._build_queryset(bulletin_id_arg, rebuild_all)
        except CommandError:
            self.tally("enqueued", 0)
            self.tally("failed", 0)
            raise

        total = qs.count()
        self.stdout.write(f"Bulletins to process: {total}")

        enqueued = 0
        failed = 0
        for bulletin in iterate_rows(
            self,
            qs,
            verbosity=verbosity,
            chunk_size=chunk_size,
            describe=Bulletin.row_label,
        ):
            enqueued += 1
            if not commit:
                continue
            result = rebuild_bulletin_task.enqueue(
                bulletin.pk, force, not skip_day_ratings
            )
            # Only an inline backend has run the task by now; a queued one
            # reports its own failure on the task result.
            if result.status == TaskResultStatus.FAILED:
                failed += 1
                logger.error(
                    "rebuild_render_models: task for bulletin %s failed",
                    bulletin.bulletin_id,
                )

        if not commit:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Read-only run complete — would enqueue {enqueued} "
                    f"bulletin(s). Pass --commit to persist."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Enqueued {enqueued} bulletin(s); {failed} failed.")
            )
        self.tally("enqueued", enqueued)
        self.tally("failed", failed)
        logger.info(
            "rebuild_render_models finished: enqueued=%d failed=%d commit=%s",
            enqueued,
            failed,
            commit,
        )
        if failed:
            raise CommandError(
                f"{failed} bulletin rebuild task(s) failed: a build error "
                "(stored with a version=0 sentinel) or a day-rating recompute "
                "(rolled back, left stale). Check logs for details."
            )
