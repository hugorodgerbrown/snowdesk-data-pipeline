"""
bulletins/services/data_fetcher.py — Fetching and persisting SLF bulletins.

Contains pure-ish functions that:
  1. Fetch a page of bulletins from the SLF CAAML list API (fetch_bulletin_page).
  2. Persist a single bulletin into the database (upsert_bulletin).
  3. Orchestrate a full pipeline run across a date range (run_pipeline).

Also defines the ``BulletinSource`` registry used by the unified
``fetch_bulletins`` management command. The registry maps provider names
(``"slf"``, ``"euregio"``) to their pipeline function, latest-date
function, settings keys, and archive-writer adapter so the command can
iterate over requested sources without owning any provider-specific logic.

The SLF API returns bulletins in reverse chronological order and is
paginated by offset/limit — it does not support filtering by date. The
pipeline pages through results, skipping bulletins newer than the end date
and stopping once it passes the start date boundary.

Keeping these as functions rather than a class makes them easy to test and
compose. The management commands call run_pipeline(); unit tests can call
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

from bulletins.models import Bulletin, PipelineRun, RegionBulletin
from bulletins.services.day_rating import apply_bulletin_day_ratings
from bulletins.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
)
from regions.models import MicroRegion

logger = logging.getLogger(__name__)

LANG = "en"
PAGE_SIZE = 50
REQUEST_TIMEOUT = 30  # seconds
_ONE_DAY = timedelta(days=1)


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
        base_url: Override for the API base URL. Falls back to
            ``settings.SLF_API_BASE_URL`` when ``None`` so the
            ``fetch_bulletins`` command can flip between the live API
            and a local mirror without environment-variable gymnastics.

    Returns:
        A list of raw bulletin dicts as returned by the API.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        ValueError: If the response body cannot be parsed as JSON.

    """
    resolved_base = base_url if base_url is not None else settings.SLF_API_BASE_URL
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
    return _normalise_response(data)


def _normalise_response(data: Any) -> list[dict[str, Any]]:
    """
    Normalise the SLF API response into a flat list of bulletin dicts.

    The API may return:
      - A flat list of bulletins.
      - A single collection object with a "bulletins" key.
      - A list of collection objects, each with a "bulletins" key.

    Args:
        data: The parsed JSON response from the SLF API.

    Returns:
        A flat list of bulletin dicts.

    """
    if isinstance(data, list):
        if len(data) > 0 and isinstance(data[0], dict) and "bulletins" in data[0]:
            # List of collection objects
            return [b for collection in data for b in collection.get("bulletins", [])]
        return data

    if isinstance(data, dict) and "bulletins" in data:
        return data["bulletins"]  # type: ignore[no-any-return]

    return []


def _parse_dt(value: str) -> datetime:
    """
    Parse an ISO-8601 datetime string into a UTC-aware datetime.

    Aware inputs (Z-suffixed or with an explicit offset) are converted to
    UTC. Naive inputs are assumed to be UTC, since the CAAML schema
    requires timestamps to be expressed in UTC or with timezone info.

    Args:
        value: An ISO-8601 formatted datetime string.

    Returns:
        A UTC-aware datetime object.

    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _resolve_issued_at(raw: dict[str, Any]) -> datetime:
    """
    Resolve a bulletin's publication timestamp from the raw payload.

    Pre-2024 SLF bulletins omit the top-level ``publicationTime`` field
    that modern bulletins carry. When it is absent, fall back to
    ``validTime.startTime`` — for SLF the two values are typically
    identical (or differ by at most a couple of hours), so this is a
    safe proxy for both the pagination boundary check and the
    ``Bulletin.issued_at`` column.

    Args:
        raw: A single bulletin dict from the SLF CAAML API.

    Returns:
        A UTC-aware datetime suitable for use as ``issued_at``.

    """
    pub_time = raw.get("publicationTime")
    if pub_time:
        return _parse_dt(pub_time)
    return _parse_dt(raw["validTime"]["startTime"])


class UnknownRegionError(LookupError):
    """Raised when an ingested bulletin references an unseeded region_id.

    Regions are fixture-backed reference data, not auto-created. If a
    CAAML bulletin arrives with a ``region_id`` that isn't in the
    ``pipeline_region`` table, we want that to fail loudly so a human
    can investigate (new EAWS region published? typo in the feed?) and
    update the fixture deliberately.
    """


def _get_region(region_id: str) -> MicroRegion:
    """
    Look up the MicroRegion for an ingested bulletin entry.

    Regions are fixture-backed; unseen identifiers raise
    ``UnknownRegionError`` rather than being silently auto-created.

    Args:
        region_id: SLF region identifier, e.g. "CH-4115".

    Returns:
        The matching MicroRegion instance.

    Raises:
        UnknownRegionError: The region_id does not correspond to any
            seeded MicroRegion row.

    """
    try:
        return MicroRegion.objects.get(region_id=region_id)
    except MicroRegion.DoesNotExist as exc:
        raise UnknownRegionError(
            f"Bulletin references unknown region_id={region_id!r} — "
            "add it to regions/fixtures/eaws_CH.json (and rerun "
            "refresh_eaws_fixtures if the EAWS source has changed) before "
            "re-ingesting."
        ) from exc


def upsert_bulletin(raw: dict[str, Any], run: PipelineRun) -> bool:
    """
    Create or update a single Bulletin from a raw API dict.

    Wraps the raw bulletin in a GeoJSON Feature envelope (matching the
    format expected by downstream consumers) before storing. Creates or
    looks up MicroRegion records and links them via RegionBulletin.

    Args:
        raw: A single bulletin dict from the SLF CAAML API.
        run: The PipelineRun to associate with this bulletin.

    Returns:
        True if a new row was created, False if an existing row was updated.

    """
    bulletin_id: str = raw["bulletinID"]
    raw_data: dict[str, Any] = {
        "type": "Feature",
        "geometry": None,
        "properties": raw,
    }

    next_update_raw: str | None = raw.get("nextUpdate")
    raw_regions: list[dict[str, str]] = raw.get("regions", [])

    # Build render model from the raw properties.
    try:
        computed_render_model = build_render_model(raw)
        computed_render_model_version = RENDER_MODEL_VERSION
    except RenderModelBuildError as exc:
        logger.exception(
            "Failed to build render model for bulletin %s: %s",
            bulletin_id,
            exc,
        )
        computed_render_model = {
            "version": 0,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }
        computed_render_model_version = 0
        run.records_failed += 1
        run.save(update_fields=["records_failed"])

    defaults: dict[str, Any] = {
        "raw_data": raw_data,
        "render_model": computed_render_model,
        "render_model_version": computed_render_model_version,
        "issued_at": _resolve_issued_at(raw),
        "valid_from": _parse_dt(raw["validTime"]["startTime"]),
        "valid_to": _parse_dt(raw["validTime"]["endTime"]),
        "next_update": _parse_dt(next_update_raw) if next_update_raw else None,
        "lang": raw.get("lang", LANG),
        "unscheduled": raw.get("unscheduled", False),
        "pipeline_run": run,
    }

    bulletin, created = Bulletin.objects.update_or_create(
        bulletin_id=bulletin_id,
        defaults=defaults,
    )

    # Link regions — clear existing links on update to stay in sync.
    if not created:
        RegionBulletin.objects.filter(bulletin=bulletin).delete()

    linked_count = 0
    skipped_regions: list[str] = []
    for raw_region in raw_regions:
        region_id = raw_region["regionID"]
        try:
            region = _get_region(region_id)
        except UnknownRegionError:
            skipped_regions.append(region_id)
            continue
        RegionBulletin.objects.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time=raw_region["name"],
        )
        linked_count += 1

    if skipped_regions:
        logger.warning(
            "Bulletin %s: %d/%d region(s) skipped — not in fixtures: %s",
            bulletin_id,
            len(skipped_regions),
            len(raw_regions),
            ", ".join(sorted(skipped_regions)),
        )

    action = "Created" if created else "Updated"
    logger.debug(
        "%s bulletin %s (issued %s, %d/%d regions linked)",
        action,
        bulletin_id,
        defaults["issued_at"],
        linked_count,
        len(raw_regions),
    )

    # Refresh day ratings — wrapped in a broad except so that a day-rating
    # failure never aborts bulletin ingest.  Day ratings are a denormalisation;
    # the authoritative data lives in Bulletin/RegionBulletin.
    try:
        apply_bulletin_day_ratings(bulletin)
    except Exception:
        logger.exception(
            "apply_bulletin_day_ratings failed for bulletin %s — ingest continues",
            bulletin_id,
        )

    return created


# Per-bulletin processing outcomes returned by ``_process_bulletin``.
_OUTCOME_CREATED = "created"
_OUTCOME_UPDATED = "updated"
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
    issued_at = _resolve_issued_at(raw)

    if issued_at >= range_end:
        return _OUTCOME_SKIPPED_NEWER
    if issued_at < range_start:
        logger.info("Passed start boundary at %s, stopping", issued_at.isoformat())
        return _OUTCOME_OUT_OF_RANGE

    if dry_run:
        logger.info("[dry-run] Would store %s", raw["bulletinID"])
        return _OUTCOME_CREATED

    if not force and Bulletin.objects.filter(bulletin_id=raw["bulletinID"]).exists():
        return _OUTCOME_SKIPPED_EXISTS

    created = upsert_bulletin(raw, run)
    return _OUTCOME_CREATED if created else _OUTCOME_UPDATED


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

    Pulled out of ``run_pipeline`` so the orchestration loop stays
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
            logger.error("Error parsing bulletin data:\n%s", json.dumps(raw, indent=4))
            raise
        if outcome == _OUTCOME_OUT_OF_RANGE:
            return True
        counts[outcome] += 1
    return False


def run_pipeline(
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
    Orchestrate a full pipeline run over a date range.

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
        _OUTCOME_CREATED: 0,
        _OUTCOME_UPDATED: 0,
        _OUTCOME_SKIPPED_EXISTS: 0,
        _OUTCOME_SKIPPED_NEWER: 0,
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
        "Pipeline run %s finished: %d pages, %d created, %d updated, %d skipped",
        run.pk,
        pages_fetched,
        counts[_OUTCOME_CREATED],
        counts[_OUTCOME_UPDATED],
        counts[_OUTCOME_SKIPPED_EXISTS],
    )

    if dry_run:
        run.mark_success(0, 0)
    else:
        run.mark_success(counts[_OUTCOME_CREATED], counts[_OUTCOME_UPDATED])

    return run


# ---------------------------------------------------------------------------
# Source registry — used by the unified fetch_bulletins management command.
# ---------------------------------------------------------------------------

SOURCE_SLF = "slf"
SOURCE_EUREGIO = "euregio"
SOURCE_CHOICES = (SOURCE_SLF, SOURCE_EUREGIO)


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

    Wraps ``bulletins.services.slf_archive.{merge, read_archive, write_archive}``
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
    from bulletins.services.slf_archive import merge, read_archive, write_archive

    existing = list(read_archive(path))
    merged = merge(existing, records)
    write_archive(path, merged)
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
            output (e.g. ``"slf"``, ``"euregio"``).
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


def _build_sources() -> dict[str, BulletinSource]:
    """
    Build the provider registry.

    Deferred to a function so the EUREGIO imports (which themselves import
    from this module) are not executed at module load time, avoiding a
    circular-import risk.

    Returns:
        A dict mapping provider name to ``BulletinSource``.

    """
    from bulletins.services.euregio_fetcher import (
        latest_euregio_date,
        run_euregio_pipeline,
        write_archive as euregio_write_archive,
    )

    def euregio_stash_writer(records: list[dict[str, Any]], path: Path) -> int:
        """Write EUREGIO records to the on-disk archive; return new size."""
        return euregio_write_archive(records, path)

    return {
        SOURCE_SLF: BulletinSource(
            name=SOURCE_SLF,
            pipeline_fn=run_pipeline,
            latest_date_fn=latest_slf_date,
            live_url_setting="SLF_API_BASE_URL",
            mirror_url_setting="SLF_API_LOCAL_MIRROR_URL",
            archive_path_setting="SLF_ARCHIVE_PATH",
            stash_writer=slf_stash_writer,
        ),
        SOURCE_EUREGIO: BulletinSource(
            name=SOURCE_EUREGIO,
            pipeline_fn=run_euregio_pipeline,
            latest_date_fn=latest_euregio_date,
            live_url_setting="EUREGIO_API_BASE_URL",
            mirror_url_setting="EUREGIO_API_LOCAL_MIRROR_URL",
            archive_path_setting="EUREGIO_ARCHIVE_PATH",
            stash_writer=euregio_stash_writer,
        ),
    }


def get_sources() -> dict[str, BulletinSource]:
    """
    Return the bulletin-provider registry.

    The registry is built lazily on first call to avoid circular imports
    between ``data_fetcher`` and ``euregio_fetcher``.

    Returns:
        A dict mapping ``SOURCE_SLF`` / ``SOURCE_EUREGIO`` to their
        ``BulletinSource`` entries.

    """
    return _build_sources()
