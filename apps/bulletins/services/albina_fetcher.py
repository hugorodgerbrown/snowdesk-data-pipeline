"""
apps/bulletins/services/albina_fetcher.py — The ALBINA pipeline, app side.

Since SNOW-1249 the walk across the ALBINA CDN — every (date, region) slot at::

    {base}/{date}/{date}_{region}_en_CAAMLv6.json

with a 404 read as "no bulletin for this slot" and a bulletin spanning
regions yielded once — is ``apps.ingest.public.fetch_bulletins("ALBINA",
…)``. This module decides what to do with each bulletin it yields and
records the run.

``run_albina_pipeline``, ``latest_albina_date`` and ``albina_stash_writer``
are the entry points the ``fetch_bulletins`` command wires up via the
``BulletinSource`` registry.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.bulletins.models import Bulletin, PipelineRun
from apps.bulletins.services.apply import apply_bulletin, count_outcome
from apps.ingest.public import (
    OUTCOME_CREATED,
    OUTCOME_DUPLICATE,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_UNCHANGED,
    OUTCOME_UPDATED,
    PROVIDER_ALBINA,
    FetchOutcome,
    FetchStats,
    NormalisedBulletin,
    fetch_bulletins,
    write_ndjson_archive,
)

logger = logging.getLogger(__name__)


def _process_albina_bulletin(
    record: NormalisedBulletin,
    run: PipelineRun,
    *,
    dry_run: bool,
    force: bool,
) -> str:
    """
    Decide what to do with one ALBINA bulletin the walk found in the window.

    Args:
        record: The bulletin.
        run: The active PipelineRun.
        dry_run: When True, log and count without writing.
        force: When True, upsert even if the bulletin already exists.

    Returns:
        ``"created"``, ``"updated"``, ``"skipped"`` or ``"failed"``.

    """
    if dry_run:
        logger.info("[dry-run] Would store ALBINA %s", record.bulletin_id)
        return OUTCOME_CREATED

    try:
        outcome = apply_bulletin(record, run, force=force)
    except Exception as exc:
        logger.exception(
            "Failed to upsert ALBINA bulletin %s: %s", record.bulletin_id, exc
        )
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return OUTCOME_FAILED

    return OUTCOME_SKIPPED if outcome == OUTCOME_UNCHANGED else outcome


def run_albina_pipeline(
    start: date,
    end: date,
    regions: tuple[str, ...] | None = None,
    triggered_by: str = "unknown",
    dry_run: bool = False,
    force: bool = False,
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
    delay: float = 0.0,
) -> PipelineRun:
    """
    Orchestrate a full ALBINA bulletin ingest over a date range × region set.

    Args:
        start: First date to include (inclusive).
        end: Last date to include (inclusive).
        regions: Tuple of ALBINA region codes to query. Falls back to
            ``settings.ALBINA_REGIONS`` when ``None``.
        triggered_by: Human-readable label for who/what triggered the run.
        dry_run: If True, fetch data but do not write to the database.
        force: If True, upsert bulletins that already exist in the DB.
        base_url: Override the ALBINA CDN base URL. ``None`` defers to
            ``settings.ALBINA_API_BASE_URL``.
        on_fetched: Optional per-record callback invoked once for each
            unique bulletin (after dedup, before date-range/dry-run
            decisions). The ``--stash`` flag on the management command
            wires this to a list collector so the on-disk archive captures
            one entry per ``bulletinID`` even when a bulletin spans regions.
        delay: Seconds to sleep between successive CDN requests. ``0.0``
            (default) is a no-op; positive values pace requests to avoid
            hammering the CDN during multi-year backfills.

    Returns:
        The completed (or failed) ``PipelineRun`` instance.

    """
    resolved_regions: tuple[str, ...] = (
        regions if regions is not None else settings.ALBINA_REGIONS
    )

    run = PipelineRun.objects.create(triggered_by=triggered_by)
    run.mark_running()

    counts: dict[str, int] = {
        OUTCOME_CREATED: 0,
        OUTCOME_UPDATED: 0,
        OUTCOME_SKIPPED: 0,
        OUTCOME_DUPLICATE: 0,
        OUTCOME_FAILED: 0,
    }
    stats = FetchStats()

    try:
        logger.info(
            "ALBINA pipeline run %s: range %s–%s regions=%s force=%s dry_run=%s",
            run.pk,
            start,
            end,
            ",".join(resolved_regions),
            force,
            dry_run,
        )
        for item in fetch_bulletins(
            PROVIDER_ALBINA,
            start,
            end,
            regions=resolved_regions,
            base_url=base_url,
            delay=delay,
            on_item=on_fetched,
            stats=stats,
        ):
            if isinstance(item, FetchOutcome):
                count_outcome(item, run, counts)
                continue
            outcome = _process_albina_bulletin(item, run, dry_run=dry_run, force=force)
            counts[outcome] += 1

    except Exception as exc:
        run.mark_failed(exc)
        return run

    logger.info(
        "ALBINA pipeline run %s finished: %d requests, "
        "%d created, %d updated, %d skipped",
        run.pk,
        stats.requests,
        counts[OUTCOME_CREATED],
        counts[OUTCOME_UPDATED],
        counts[OUTCOME_SKIPPED],
    )

    if dry_run:
        run.mark_finished(0, 0)
    else:
        run.mark_finished(counts[OUTCOME_CREATED], counts[OUTCOME_UPDATED])

    return run


def latest_albina_date() -> date | None:
    """
    Return the most recent ``valid_from`` date of any ALBINA bulletin in the DB.

    Used by the management command to derive the default ``--start-date``
    (resume from where the last run left off). Returns ``None`` when no
    ALBINA bulletins exist yet.

    Returns:
        The latest ``valid_from.date()`` of any ALBINA bulletin, or ``None``.

    """
    result = (
        Bulletin.objects.filter(source=Bulletin.Source.ALBINA)
        .order_by("-valid_from")
        .values_list("valid_from", flat=True)
        .first()
    )
    if result is None:
        return None
    return result.date()


def albina_stash_writer(records: list[dict[str, Any]], path: Path) -> int:
    """
    Merge ``records`` into the on-disk ALBINA archive and return the new size.

    Delegates to ``write_ndjson_archive`` from ``fetcher_common``, which
    reads the existing archive at ``path`` (if it exists), overlays the
    supplied records (later ``bulletinID`` wins), sorts ascending by
    ``validTime.startTime``, and writes the result back atomically via a
    sibling ``.tmp`` file plus ``os.replace`` so an interrupted run never
    leaves a half-written archive in place.

    Args:
        records: Raw ALBINA bulletin dicts collected during a pipeline run.
        path: Filesystem path to the ALBINA archive NDJSON file.

    Returns:
        The total number of records in the archive after the merge.

    """
    return write_ndjson_archive(records, path, source_label="albina")
