"""
apps/bulletins/services/slf_fetcher.py — The SLF pipeline, app side.

Since SNOW-1249 this module holds only what needs Snowdesk's tables:

  run_slf_pipeline   drives ``apps.ingest.public.fetch_bulletins("SLF", …)``
                     — the walk back through SLF's paginated list API, which
                     lives in ingest with the HTTP — and decides what to do
                     with each bulletin it yields: count it in a dry run,
                     skip it when it is already stored, or write it through
                     ``apply_bulletin``.
  latest_slf_date    where the next run resumes from.
  slf_stash_writer   ``--stash``'s archive writer.

Also defines the ``BulletinSource`` registry used by the unified
``fetch_bulletins`` management command. The registry maps provider names
(``"SLF"``, ``"ALBINA"``, ``"METEOFRANCE"``) to their pipeline function,
latest-date function, settings keys, and archive-writer adapter so the
command can iterate over requested sources without owning any
provider-specific logic.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from apps.bulletins.models import (
    Bulletin,
    PipelineRun,
)
from apps.bulletins.services.apply import (
    NoResolvableRegionsError,
    apply_bulletin,
    count_outcome,
)
from apps.ingest.public import (
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_UPDATED,
    PROVIDER_SLF,
    FetchOutcome,
    FetchStats,
    NormalisedBulletin,
    fetch_bulletins,
    merge_slf_archive,
    read_slf_archive,
    write_slf_archive,
)

logger = logging.getLogger(__name__)

# The SLF pipeline's own outcome: a bulletin already stored and not forced.
# The walk's outcomes (skipped_newer) come from apps.ingest.public.
_OUTCOME_SKIPPED_EXISTS = "skipped_exists"


def _process_bulletin(
    record: NormalisedBulletin,
    run: PipelineRun,
    *,
    dry_run: bool,
    force: bool,
) -> str:
    """
    Decide what to do with one bulletin the walk found in the window.

    Args:
        record: The bulletin.
        run: The active PipelineRun.
        dry_run: When True, log and count without writing.
        force: When True, rewrite a bulletin that already exists.

    Returns:
        ``OUTCOME_CREATED``, ``OUTCOME_UPDATED``, ``OUTCOME_FAILED`` or
        ``_OUTCOME_SKIPPED_EXISTS``.

    """
    if dry_run:
        logger.info("[dry-run] Would store %s", record.bulletin_id)
        return OUTCOME_CREATED

    if not force and Bulletin.objects.filter(bulletin_id=record.bulletin_id).exists():
        return _OUTCOME_SKIPPED_EXISTS

    # A bulletin whose regions are all unresolvable fails that bulletin, not
    # the batch (SNOW-547) — the same posture RenderModelBuildError takes
    # inside upsert_bulletin. records_failed makes fetch_bulletins exit
    # non-zero per the management-command contract, so cron/CI surface it.
    try:
        created = apply_bulletin(record, run)
    except NoResolvableRegionsError:
        logger.exception("Skipping bulletin %s", record.bulletin_id)
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return OUTCOME_FAILED
    except KeyError:
        logger.exception(
            "Error parsing bulletin data:\n%s", json.dumps(record.caaml, indent=4)
        )
        raise

    return OUTCOME_CREATED if created else OUTCOME_UPDATED


def run_slf_pipeline(
    start: date,
    end: date,
    triggered_by: str = "unknown",
    dry_run: bool = False,
    force: bool = False,
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
    delay: float = 0.0,
) -> PipelineRun:
    """
    Orchestrate a full SLF pipeline run over a date range.

    The walk — paging back through the SLF API, skipping bulletins newer
    than ``end`` and stopping at the first older than ``start`` — is
    ``apps.ingest.public.fetch_bulletins``. This function decides what to
    do with each bulletin it yields and records the run.

    Args:
        start: First date to include (inclusive).
        end: Last date to include (inclusive).
        triggered_by: Human-readable label for who/what triggered the run.
        dry_run: If True, fetch data but do not write to the database.
        force: If True, upsert existing bulletins instead of skipping them.
        base_url: Override for the SLF API base URL. ``None`` defers to
            ``settings.SLF_API_BASE_URL``. ``fetch_bulletins --source
            local-mirror`` passes the development mirror URL here.
        on_fetched: Optional per-record callback invoked once for every
            raw bulletin returned by the fetcher, *before* in-range /
            dry-run / dedup decisions are made. The ``--stash`` flag
            wires this to a list collector so the on-disk archive
            captures everything the fetcher saw — independent of the
            date window or whether the bulletin was already in the DB.
        delay: Seconds to sleep between successive page fetches. ``0.0``
            (default) is a no-op; positive values pace the API to avoid
            hammering the SLF server during multi-year backfills. The
            sleep happens only between pages, never before the first
            request or after the last.

    Returns:
        The completed (or failed) PipelineRun instance.

    """
    run = PipelineRun.objects.create(triggered_by=triggered_by)
    run.mark_running()

    counts: dict[str, int] = {
        OUTCOME_CREATED: 0,
        OUTCOME_UPDATED: 0,
        _OUTCOME_SKIPPED_EXISTS: 0,
        OUTCOME_FAILED: 0,
    }
    stats = FetchStats()

    try:
        logger.info(
            "Pipeline run %s: range %s–%s force=%s dry_run=%s delay=%s",
            run.pk,
            start,
            end,
            force,
            dry_run,
            delay,
        )
        for item in fetch_bulletins(
            PROVIDER_SLF,
            start,
            end,
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
        "Pipeline run %s finished: %d pages, %d created, %d updated, "
        "%d skipped, %d failed",
        run.pk,
        stats.requests,
        counts[OUTCOME_CREATED],
        counts[OUTCOME_UPDATED],
        counts[_OUTCOME_SKIPPED_EXISTS],
        counts[OUTCOME_FAILED],
    )

    if dry_run:
        run.mark_finished(0, 0)
    else:
        run.mark_finished(counts[OUTCOME_CREATED], counts[OUTCOME_UPDATED])

    return run


# ---------------------------------------------------------------------------
# Source registry — used by the unified fetch_bulletins management command.
# ---------------------------------------------------------------------------
# SNOW-582: these reference Bulletin.Source directly rather than redeclaring
# the same upper-case strings — Bulletin.Source.SLF now already equals "SLF".

SOURCE_SLF = Bulletin.Source.SLF
SOURCE_ALBINA = Bulletin.Source.ALBINA
SOURCE_METEOFRANCE = Bulletin.Source.METEOFRANCE
SOURCE_CHOICES = (SOURCE_SLF, SOURCE_ALBINA, SOURCE_METEOFRANCE)


def latest_slf_date() -> date | None:
    """
    Return the most recent ``valid_from`` date of any SLF bulletin in the DB.

    Used by the management command to derive the default ``--start-date``
    (resume from where the last run left off, with a one-day same-day
    overlap so morning updates / prior-evening re-issues are re-fetched).
    Returns ``None`` when no bulletin has been stored yet, which causes the
    command to fall back to ``settings.SEASON_START_DATE``.

    Returns:
        The latest ``valid_from.date()`` across all Bulletin rows, or
        ``None`` when the table is empty.

    """
    return Bulletin.objects.latest_valid_from_date()


def slf_stash_writer(records: list[dict[str, Any]], path: Path) -> int:
    """
    Merge ``records`` into the SLF on-disk archive and return the new size.

    Wraps ``merge_slf_archive``, ``read_slf_archive`` and ``write_slf_archive``
    to match the uniform ``(records, path) -> int`` stash-writer signature
    required by ``BulletinSource``.

    The merge is atomic (write to a ``.tmp`` sibling then ``os.replace``).

    Args:
        records: Raw bulletin dicts collected by the ``--stash`` callback
            during a pipeline run.
        path: Filesystem path to the SLF archive NDJSON file.

    Returns:
        The total number of records in the archive after the merge.

    """
    existing = list(read_slf_archive(path))
    merged = merge_slf_archive(existing, records)
    write_slf_archive(path, merged)
    return len(merged)


@dataclass(frozen=True)
class BulletinSource:
    """
    Registry entry describing a single bulletin provider.

    Each field tells the unified ``fetch_bulletins`` command how to
    interact with a specific provider without encoding any provider logic
    in the command itself.

    Attributes:
        name: Short provider name used in ``--source`` choices and log
            output (e.g. ``"SLF"``, ``"ALBINA"``, ``"METEOFRANCE"``).
        pipeline_fn: Callable with the signature
            ``(start, end, triggered_by, dry_run, force, base_url,
            on_fetched, delay) -> PipelineRun`` that runs the full ingest.
        latest_date_fn: Zero-argument callable that returns the most
            recent ``valid_from`` date stored in the DB for this provider,
            or ``None`` when the DB is empty. Used to derive the default
            ``--start-date``.
        live_url_setting: Attribute name on ``django.conf.settings`` that
            holds the provider's live API base URL (e.g.
            ``"SLF_API_BASE_URL"``).
        mirror_url_setting: Attribute name on ``django.conf.settings``
            that holds the dev-mirror URL (e.g.
            ``"SLF_API_LOCAL_MIRROR_URL"``). Expected to be absent or
            falsy in production.
        archive_path_setting: Attribute name on ``django.conf.settings``
            that holds the ``Path`` to the on-disk NDJSON archive (e.g.
            ``"SLF_ARCHIVE_PATH"``). Used when ``--stash`` is passed.
        stash_writer: Callable with signature
            ``(records: list[dict], path: Path) -> int`` that merges
            ``records`` into the on-disk archive and returns the new
            total record count.

    """

    name: str
    pipeline_fn: Callable[..., PipelineRun]
    latest_date_fn: Callable[[], date | None]
    live_url_setting: str
    mirror_url_setting: str
    archive_path_setting: str
    stash_writer: Callable[[list[dict[str, Any]], Path], int]


def get_sources() -> dict[str, BulletinSource]:
    """
    Return the bulletin-provider registry.

    Built on each call so the ALBINA imports (which themselves import
    from this module) are not executed at module load time — avoiding a
    circular import. Not cached on the module: tests patch
    ``run_slf_pipeline`` / ``run_albina_pipeline`` at the module level, and
    caching the resolved references would freeze the unpatched originals
    inside the registry. The rebuild cost is negligible — the command
    runs once per cron invocation.

    Returns:
        A dict mapping ``SOURCE_SLF`` / ``SOURCE_ALBINA`` /
        ``SOURCE_METEOFRANCE`` to their ``BulletinSource`` entries.

    """
    from apps.bulletins.services.albina_fetcher import (
        albina_stash_writer,
        latest_albina_date,
        run_albina_pipeline,
    )
    from apps.bulletins.services.meteofrance_fetcher import (
        latest_meteofrance_date,
        meteofrance_stash_writer,
        run_meteofrance_pipeline,
    )

    return {
        SOURCE_SLF: BulletinSource(
            name=SOURCE_SLF,
            pipeline_fn=run_slf_pipeline,
            latest_date_fn=latest_slf_date,
            live_url_setting="SLF_API_BASE_URL",
            mirror_url_setting="SLF_API_LOCAL_MIRROR_URL",
            archive_path_setting="SLF_ARCHIVE_PATH",
            stash_writer=slf_stash_writer,
        ),
        SOURCE_ALBINA: BulletinSource(
            name=SOURCE_ALBINA,
            pipeline_fn=run_albina_pipeline,
            latest_date_fn=latest_albina_date,
            live_url_setting="ALBINA_API_BASE_URL",
            mirror_url_setting="ALBINA_API_LOCAL_MIRROR_URL",
            archive_path_setting="ALBINA_ARCHIVE_PATH",
            stash_writer=albina_stash_writer,
        ),
        SOURCE_METEOFRANCE: BulletinSource(
            name=SOURCE_METEOFRANCE,
            pipeline_fn=run_meteofrance_pipeline,
            latest_date_fn=latest_meteofrance_date,
            live_url_setting="METEOFRANCE_API_BASE_URL",
            mirror_url_setting="METEOFRANCE_API_LOCAL_MIRROR_URL",
            archive_path_setting="METEOFRANCE_ARCHIVE_PATH",
            stash_writer=meteofrance_stash_writer,
        ),
    }
