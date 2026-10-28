"""
apps/bulletins/services/meteofrance_fetcher.py — The Météo-France pipeline, app side.

Since SNOW-1249 the walk — one DPBRA XML document per configured massif from
the Météo-France APIM, translated to CAAML v6, with a 404 or a delegated
massif (e.g. Andorre / massif 71) a clean skip, and a ``file://`` base URL
read from a local mirror so a keyless session can run it — is
``apps.ingest.public.fetch_bulletins("METEOFRANCE", …)``. This module
decides what to do with each bulletin it yields and records the run.

``run_meteofrance_pipeline``, ``latest_meteofrance_date``, and
``meteofrance_stash_writer`` are the public entry points that the management
command wires up via the ``BulletinSource`` registry.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from django.conf import settings

from apps.bulletins.models import Bulletin, PipelineRun
from apps.bulletins.services.apply import (
    UnknownRegionError,
    apply_bulletin,
    count_outcome,
)
from apps.ingest.public import (
    OUTCOME_CREATED,
    OUTCOME_DELEGATED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_UPDATED,
    PROVIDER_METEOFRANCE,
    FetchOutcome,
    FetchStats,
    NormalisedBulletin,
    fetch_bulletins,
    write_ndjson_archive,
)

logger = logging.getLogger(__name__)


def run_meteofrance_pipeline(
    start: date,
    end: date,
    triggered_by: str = "unknown",
    dry_run: bool = False,
    force: bool = False,
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
    delay: float = 0.0,
    massif_ids: tuple[int, ...] | None = None,
) -> PipelineRun:
    """
    Orchestrate a full MeteoFrance bulletin ingest for one calendar day.

    Note: DPBRA is a one-issue-per-day product with no date-range API. The
    ``start`` and ``end`` arguments are accepted to match the
    ``BulletinSource.pipeline_fn`` signature, but only today's live bulletin
    is fetched for each massif regardless of the range. For historical
    backfill, a separate approach (e.g. the multi-coop CSV archive) is
    needed.

    Args:
        start: First date to include (inclusive). Passed through for
            signature compatibility; the live API only serves today.
        end: Last date to include (inclusive). Same caveat as ``start``.
        triggered_by: Human-readable label for who/what triggered the run.
        dry_run: If ``True``, fetch and translate but do not write to the DB.
        force: If ``True``, upsert bulletins that already exist in the DB.
        base_url: Override for the API base URL. ``None`` defers to
            ``settings.METEOFRANCE_API_BASE_URL``. The mirror setting is
            only used when ``fetch_bulletins --local-mirror`` forwards it
            explicitly; it is never picked up by default.
        on_fetched: Optional per-bulletin callback invoked for each
            successfully translated dict, before dry-run / upsert decisions.
            The ``--stash`` flag wires this to a list collector.
        delay: Seconds to sleep between successive API requests.
        massif_ids: Override the massif ID set. Falls back to
            ``settings.METEOFRANCE_MASSIF_IDS``.

    Returns:
        The completed (or failed) ``PipelineRun`` instance.

    """
    resolved_massif_ids: tuple[int, ...] = (
        massif_ids
        if massif_ids is not None
        else getattr(settings, "METEOFRANCE_MASSIF_IDS", ())
    )

    run = PipelineRun.objects.create(triggered_by=triggered_by)
    run.mark_running()

    counts: dict[str, int] = {
        OUTCOME_CREATED: 0,
        OUTCOME_UPDATED: 0,
        OUTCOME_SKIPPED: 0,
        OUTCOME_DELEGATED: 0,
        OUTCOME_FAILED: 0,
    }
    stats = FetchStats()

    try:
        logger.info(
            "MeteoFrance pipeline run %s: %d massifs force=%s dry_run=%s",
            run.pk,
            len(resolved_massif_ids),
            force,
            dry_run,
        )
        for item in fetch_bulletins(
            PROVIDER_METEOFRANCE,
            start,
            end,
            massif_ids=resolved_massif_ids,
            base_url=base_url,
            delay=delay,
            on_item=on_fetched,
            stats=stats,
        ):
            if isinstance(item, FetchOutcome):
                count_outcome(item, run, counts)
                continue
            counts[_process_bulletin(item, run, dry_run=dry_run, force=force)] += 1

    except Exception as exc:
        run.mark_failed(exc)
        return run

    logger.info(
        "MeteoFrance pipeline run %s finished: %d requests, "
        "%d created, %d updated, %d skipped, %d delegated, %d failed",
        run.pk,
        stats.requests,
        counts[OUTCOME_CREATED],
        counts[OUTCOME_UPDATED],
        counts[OUTCOME_SKIPPED],
        counts[OUTCOME_DELEGATED],
        counts[OUTCOME_FAILED],
    )

    if dry_run:
        run.mark_finished(0, 0)
    else:
        run.mark_finished(counts[OUTCOME_CREATED], counts[OUTCOME_UPDATED])

    return run


def _process_bulletin(
    record: NormalisedBulletin,
    run: PipelineRun,
    *,
    dry_run: bool,
    force: bool,
) -> str:
    """
    Decide what to do with one translated Météo-France bulletin.

    The archive PDF is looked up only when the bulletin is written — it
    costs a request to the BRA archive.

    Args:
        record: The bulletin.
        run: The active ``PipelineRun`` (updated on failure).
        dry_run: When ``True``, log and count without writing.
        force: When ``True``, upsert even if the bulletin already exists.

    Returns:
        ``"created"``, ``"updated"``, ``"skipped"`` or ``"failed"``.

    """
    bulletin_id = record.bulletin_id
    if dry_run:
        logger.info("[dry-run] Would store MeteoFrance %s", bulletin_id)
        return OUTCOME_CREATED

    if not force and Bulletin.objects.filter(bulletin_id=bulletin_id).exists():
        logger.debug("Skipping existing MeteoFrance bulletin %s", bulletin_id)
        return OUTCOME_SKIPPED

    try:
        created = apply_bulletin(record, run)
    except UnknownRegionError as exc:
        logger.exception(
            "Unknown region for MeteoFrance bulletin %s: %s", bulletin_id, exc
        )
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return OUTCOME_FAILED
    except Exception as exc:
        logger.exception(
            "Failed to upsert MeteoFrance bulletin %s: %s", bulletin_id, exc
        )
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return OUTCOME_FAILED

    return OUTCOME_CREATED if created else OUTCOME_UPDATED


# ---------------------------------------------------------------------------
# Latest-date helper (used by the management command default start-date)
# ---------------------------------------------------------------------------


def latest_meteofrance_date() -> date | None:
    """
    Return the most recent ``valid_from`` date of any MeteoFrance bulletin in the DB.

    Filters on the ``source`` column (``Bulletin.Source.METEOFRANCE``) — mirrors
    the approach used in ``latest_albina_date()``. Provenance is an ingest
    fact set by ``upsert_bulletin`` regardless of whether the render model
    built successfully, so this no longer depends on
    ``render_model["source"]`` at all (SNOW-582).

    Used by the management command to derive the default ``--start-date``
    (resume from where the last run left off). Returns ``None`` when no
    MeteoFrance bulletins exist yet.

    Returns:
        The latest ``valid_from.date()`` across all MeteoFrance ``Bulletin``
        rows, or ``None`` when the table has no MeteoFrance rows.

    """
    result = (
        Bulletin.objects.filter(source=Bulletin.Source.METEOFRANCE)
        .order_by("-valid_from")
        .values_list("valid_from", flat=True)
        .first()
    )
    if result is None:
        return None
    return result.date()


# ---------------------------------------------------------------------------
# Stash writer (used by the --stash flag)
# ---------------------------------------------------------------------------


def meteofrance_stash_writer(records: list[dict[str, Any]], path: Path) -> int:
    """
    Merge ``records`` into the on-disk MeteoFrance archive and return the new size.

    Delegates to ``write_ndjson_archive`` from ``fetcher_common``, which
    reads the existing archive at ``path`` (if it exists), overlays the
    supplied records (later ``bulletinID`` wins), sorts ascending by
    ``validTime.startTime``, and writes the result back atomically via a
    sibling ``.tmp`` file plus ``os.replace``.

    Args:
        records: Raw MeteoFrance CAAML dicts collected during a pipeline run.
        path: Filesystem path to the MeteoFrance archive NDJSON file.

    Returns:
        The total number of records in the archive after the merge.

    """
    return write_ndjson_archive(records, path, source_label="meteofrance")
