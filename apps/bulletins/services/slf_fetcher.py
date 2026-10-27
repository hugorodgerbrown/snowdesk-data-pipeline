"""
apps/bulletins/services/slf_fetcher.py — Fetching and persisting SLF bulletins.

Contains pure-ish functions that:
  1. Fetch a page of bulletins from the SLF CAAML list API (fetch_bulletin_page).
  2. Persist a single bulletin into the database (upsert_bulletin).
  3. Orchestrate a full pipeline run across a date range (run_slf_pipeline).
  4. Log which CAAML export each page actually IS (_log_caaml_shape). The
     classifier itself, detect_caaml_shape, and the other pure helpers
     (response unwrapping, timestamp parsing, the PDF URL) live in
     ``apps.ingest.normalise.slf`` since SNOW-1248.

Also defines the ``BulletinSource`` registry used by the unified
``fetch_bulletins`` management command. The registry maps provider names
(``"SLF"``, ``"ALBINA"``, ``"METEOFRANCE"``) to their pipeline function,
latest-date function, settings keys, and archive-writer adapter so the
command can iterate over requested sources without owning any
provider-specific logic.

Note: ``upsert_bulletin``, ``UnknownRegionError``, and ``_get_region`` are
shared helpers also imported by ``albina_fetcher`` and ``meteofrance_fetcher``.
Extracting them into a dedicated ``persistence.py`` module is a separate
cleanup ticket (not in scope here).

The SLF API returns bulletins in reverse chronological order and is
paginated by offset/limit — it does not support filtering by date. The
pipeline pages through results, skipping bulletins newer than the end date
and stopping once it passes the start date boundary.

Keeping these as functions rather than a class makes them easy to test and
compose. The management commands call run_slf_pipeline(); unit tests can call
fetch_bulletin_page() and upsert_bulletin() independently.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from django.conf import settings

from apps.bulletins.models import (
    Bulletin,
    PipelineRun,
)
from apps.bulletins.services.apply import NoResolvableRegionsError, upsert_bulletin
from apps.ingest.public import (
    CAAML_SHAPE_PER_REGION,
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_UPDATED,
    SLF_DEFAULT_LANG,
    detect_caaml_shape,
    merge_slf_archive,
    normalise_slf_response,
    read_slf_archive,
    resolve_issued_at,
    slf_pdf_url,
    write_slf_archive,
)

logger = logging.getLogger(__name__)

LANG = SLF_DEFAULT_LANG
PAGE_SIZE = 50
REQUEST_TIMEOUT = 30  # seconds
_ONE_DAY = timedelta(days=1)


def _resolve_base_url(base_url: str | None) -> str:
    """Pick the base URL one fetch should use.

    Three paths, in descending precedence:

    1. An explicit ``base_url`` argument, which is how
       ``fetch_bulletins --local-mirror`` points a run at the dev mirror.
       A caller that named a URL gets the URL it named.
    2. ``settings.SLF_API_LEGACY_URL``, when non-empty (SNOW-900) — the
       environment-variable pin back to the pre-2026/27 export.
    3. ``settings.SLF_API_BASE_URL``, the live API.

    The pin lives HERE rather than in the ``fetch_bulletins`` command so
    that it holds for every caller. It first shipped in the command, which
    left ``BulletinAdmin.backfill_view`` — which calls
    ``run_slf_pipeline`` with no ``base_url`` — fetching the live URL and
    ingesting the new schema straight past a configured rollback. A pin
    that one entry point ignores is worse than no pin, because it reads
    as applied.

    Args:
        base_url: The caller's explicit override, or ``None``.

    Returns:
        The base URL to build this request from.

    """
    if base_url is not None:
        return base_url

    legacy_url: str = getattr(settings, "SLF_API_LEGACY_URL", "") or ""
    if legacy_url:
        # WARNING, every fetch, for as long as it is set: this is an
        # emergency lever pointed at an endpoint SLF intend to retire, not
        # a configuration to settle into. A pin left on by accident should
        # be impossible to miss in the log.
        logger.warning(
            "SLF is pinned to settings.SLF_API_LEGACY_URL (%s) instead of "
            "SLF_API_BASE_URL (%s)",
            legacy_url,
            settings.SLF_API_BASE_URL,
        )
        return legacy_url

    resolved: str = settings.SLF_API_BASE_URL
    return resolved


def fetch_bulletin_page(
    lang: str,
    limit: int,
    offset: int,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch a single page of bulletins from the SLF CAAML list API.

    Args:
        lang: Language code ("en", "de", "fr", "it").
        limit: Maximum number of bulletins to return.
        offset: Number of bulletins to skip (for pagination).
        base_url: Override for the API base URL. Resolved by
            ``_resolve_base_url``, so ``None`` picks up the legacy pin
            when one is set and the live API otherwise.

    Returns:
        A list of raw bulletin dicts as returned by the API.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        ValueError: If the response body cannot be parsed as JSON.

    Every page is classified by ``detect_caaml_shape`` and logged on the
    way out — at WARNING when the 2026/27 per-region export is what
    arrived (SNOW-900). Classification never blocks the fetch: the page
    is returned whatever shape it is.

    """
    resolved_base = _resolve_base_url(base_url)
    url = f"{resolved_base}/{lang}/json"
    logger.debug(
        "Fetching SLF bulletins: lang=%s limit=%d offset=%d base=%s",
        lang,
        limit,
        offset,
        resolved_base,
    )

    response = requests.get(
        url,
        params={"limit": limit, "offset": offset},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    data: Any = response.json()
    bulletins = normalise_slf_response(data)
    _log_caaml_shape(bulletins, resolved_base)
    return bulletins


def _log_caaml_shape(bulletins: list[dict[str, Any]], base_url: str) -> None:
    """Log what shape a fetched page turned out to be.

    WARNING for the new per-region export, INFO otherwise: the whole point
    is that the changeover announces itself the first time it happens
    rather than being inferred later from a row count. The base URL is in
    the message because the shape is explicitly NOT a property of it —
    seeing the two together is what tells an operator whether pinning
    ``SLF_API_LEGACY_URL`` did anything.

    Args:
        bulletins: The normalised bulletin list from one page fetch.
        base_url: The base URL the page was fetched from.

    """
    detected = detect_caaml_shape(bulletins)
    level = (
        logging.WARNING if detected.shape == CAAML_SHAPE_PER_REGION else logging.INFO
    )
    logger.log(
        level,
        "SLF page shape=%s bulletins=%d regions=%d max_regions=%d markers=%s base=%s",
        detected.shape,
        detected.bulletin_count,
        detected.region_count,
        detected.max_regions_per_bulletin,
        ",".join(detected.markers) or "none",
        base_url,
    )


# Per-bulletin processing outcomes returned by ``_process_bulletin``.
# The generic OUTCOME_* constants are imported from fetcher_common; the
# SLF pipeline also uses internal pagination-control variants that have
# no equivalent in other providers.
_OUTCOME_SKIPPED_EXISTS = "skipped_exists"
_OUTCOME_SKIPPED_NEWER = "skipped_newer"
_OUTCOME_OUT_OF_RANGE = "out_of_range"


def _process_bulletin(
    raw: dict[str, Any],
    run: PipelineRun,
    *,
    range_start: datetime,
    range_end: datetime,
    dry_run: bool,
    force: bool,
) -> str:
    """
    Decide how to handle a single bulletin within the paging loop.

    Returns one of the ``_OUTCOME_*`` constants so the caller can update
    counters or terminate pagination without owning the decision logic.
    """
    issued_at = resolve_issued_at(raw)

    if issued_at >= range_end:
        return _OUTCOME_SKIPPED_NEWER
    if issued_at < range_start:
        logger.info("Passed start boundary at %s, stopping", issued_at.isoformat())
        return _OUTCOME_OUT_OF_RANGE

    if dry_run:
        logger.info("[dry-run] Would store %s", raw["bulletinID"])
        return OUTCOME_CREATED

    if not force and Bulletin.objects.filter(bulletin_id=raw["bulletinID"]).exists():
        return _OUTCOME_SKIPPED_EXISTS

    # A bulletin whose regions are all unresolvable fails that bulletin, not
    # the batch (SNOW-547) — the same posture RenderModelBuildError takes
    # inside upsert_bulletin. records_failed makes fetch_bulletins exit
    # non-zero per the management-command contract, so cron/CI surface it.
    try:
        created = upsert_bulletin(raw, run, pdf_url=slf_pdf_url(raw))
    except NoResolvableRegionsError:
        logger.exception("Skipping bulletin %s", raw["bulletinID"])
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return OUTCOME_FAILED

    return OUTCOME_CREATED if created else OUTCOME_UPDATED


def _process_page(
    page: list[dict[str, Any]],
    run: PipelineRun,
    counts: dict[str, int],
    *,
    range_start: datetime,
    range_end: datetime,
    dry_run: bool,
    force: bool,
    on_fetched: "Callable[[dict[str, Any]], None] | None",
) -> bool:
    """
    Walk a page of bulletins, mutating counts; return True to stop paging.

    Pulled out of ``run_slf_pipeline`` so the orchestration loop stays
    under the cyclomatic-complexity limit. The return value collapses
    the two pagination-termination signals into one: an out-of-range
    bulletin (``_OUTCOME_OUT_OF_RANGE``) tells the caller to stop.
    """
    for raw in page:
        if on_fetched is not None:
            on_fetched(raw)
        try:
            outcome = _process_bulletin(
                raw,
                run,
                range_start=range_start,
                range_end=range_end,
                dry_run=dry_run,
                force=force,
            )
        except KeyError:
            logger.exception(
                "Error parsing bulletin data:\n%s", json.dumps(raw, indent=4)
            )
            raise
        if outcome == _OUTCOME_OUT_OF_RANGE:
            return True
        counts[outcome] += 1
    return False


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

    Pages through the SLF CAAML API in reverse chronological order.
    Bulletins newer than ``end`` are skipped; once a bulletin older than
    ``start`` is encountered, pagination stops.

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

    # Convert date boundaries to aware datetimes for comparison.
    range_start = datetime(start.year, start.month, start.day, tzinfo=UTC)
    range_end = datetime(end.year, end.month, end.day, tzinfo=UTC) + _ONE_DAY

    counts: dict[str, int] = {
        OUTCOME_CREATED: 0,
        OUTCOME_UPDATED: 0,
        _OUTCOME_SKIPPED_EXISTS: 0,
        _OUTCOME_SKIPPED_NEWER: 0,
        OUTCOME_FAILED: 0,
    }
    offset = 0
    pages_fetched = 0

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

        while True:
            page = fetch_bulletin_page(LANG, PAGE_SIZE, offset, base_url=base_url)
            pages_fetched += 1

            if not page:
                break

            stop = _process_page(
                page,
                run,
                counts,
                range_start=range_start,
                range_end=range_end,
                dry_run=dry_run,
                force=force,
                on_fetched=on_fetched,
            )

            # Stop on either an out-of-range bulletin (``stop`` is True)
            # or the upstream's "fewer than ``limit``" last-page signal.
            if stop or len(page) < PAGE_SIZE:
                break

            offset += PAGE_SIZE

            if delay > 0:
                time.sleep(delay)

    except Exception as exc:
        run.mark_failed(exc)
        return run

    logger.info(
        "Pipeline run %s finished: %d pages, %d created, %d updated, "
        "%d skipped, %d failed",
        run.pk,
        pages_fetched,
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
