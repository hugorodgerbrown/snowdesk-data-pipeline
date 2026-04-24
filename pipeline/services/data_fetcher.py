"""
pipeline/services/data_fetcher.py — Fetching and persisting SLF bulletins.

Contains pure-ish functions that:
  1. Fetch a page of bulletins from the SLF CAAML API (fetch_bulletin_page).
  2. Persist a single bulletin into the database (upsert_bulletin).
  3. Orchestrate a full pipeline run across a date range (run_pipeline).

The SLF API returns bulletins in reverse chronological order and is
paginated by offset/limit — it does not support filtering by date. The
pipeline pages through results, skipping bulletins newer than the end date
and stopping once it passes the start date boundary.

Keeping these as functions rather than a class makes them easy to test and
compose. The management commands call run_pipeline(); unit tests can call
fetch_bulletin_page() and upsert_bulletin() independently.
"""

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests

from pipeline.models import Bulletin, PipelineRun, Region, RegionBulletin
from pipeline.services.day_rating import apply_bulletin_day_ratings
from pipeline.services.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
)

logger = logging.getLogger(__name__)

SLF_API_BASE_URL = "https://aws.slf.ch/api/bulletin-list/caaml"
LANG = "en"
PAGE_SIZE = 50
REQUEST_TIMEOUT = 30  # seconds
_ONE_DAY = timedelta(days=1)


def fetch_bulletin_page(lang: str, limit: int, offset: int) -> list[dict[str, Any]]:
    """
    Fetch a single page of bulletins from the SLF CAAML list API.

    Args:
        lang: Language code ("en", "de", "fr", "it").
        limit: Maximum number of bulletins to return.
        offset: Number of bulletins to skip (for pagination).

    Returns:
        A list of raw bulletin dicts as returned by the API.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        ValueError: If the response body cannot be parsed as JSON.

    """
    url = f"{SLF_API_BASE_URL}/{lang}/json"
    logger.debug(
        "Fetching SLF bulletins: lang=%s limit=%d offset=%d", lang, limit, offset
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


class UnknownRegionError(LookupError):
    """Raised when an ingested bulletin references an unseeded region_id.

    Regions are fixture-backed reference data, not auto-created. If a
    CAAML bulletin arrives with a ``region_id`` that isn't in the
    ``pipeline_region`` table, we want that to fail loudly so a human
    can investigate (new EAWS region published? typo in the feed?) and
    update the fixture deliberately.
    """


def _get_region(region_id: str) -> Region:
    """
    Look up the Region for an ingested bulletin entry.

    Regions are fixture-backed; unseen identifiers raise
    ``UnknownRegionError`` rather than being silently auto-created.

    Args:
        region_id: SLF region identifier, e.g. "CH-4115".

    Returns:
        The matching Region instance.

    Raises:
        UnknownRegionError: The region_id does not correspond to any
            seeded Region row.

    """
    try:
        return Region.objects.get(region_id=region_id)
    except Region.DoesNotExist as exc:
        raise UnknownRegionError(
            f"Bulletin references unknown region_id={region_id!r} — "
            "add it to pipeline/fixtures/regions.json (and rerun "
            "refresh_eaws_fixtures if the EAWS source has changed) before "
            "re-ingesting."
        ) from exc


def upsert_bulletin(raw: dict[str, Any], run: PipelineRun) -> bool:
    """
    Create or update a single Bulletin from a raw API dict.

    Wraps the raw bulletin in a GeoJSON Feature envelope (matching the
    format expected by downstream consumers) before storing. Creates or
    looks up Region records and links them via RegionBulletin.

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
        "issued_at": _parse_dt(raw["publicationTime"]),
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

    for raw_region in raw_regions:
        region = _get_region(raw_region["regionID"])
        RegionBulletin.objects.create(
            bulletin=bulletin,
            region=region,
            region_name_at_time=raw_region["name"],
        )

    action = "Created" if created else "Updated"
    logger.debug(
        "%s bulletin %s (issued %s, %d regions)",
        action,
        bulletin_id,
        defaults["issued_at"],
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
    issued_at = _parse_dt(raw["publicationTime"])

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


def run_pipeline(
    start: date,
    end: date,
    triggered_by: str = "unknown",
    dry_run: bool = False,
    force: bool = False,
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
            "Pipeline run %s: range %s–%s force=%s dry_run=%s",
            run.pk,
            start,
            end,
            force,
            dry_run,
        )

        done = False
        while not done:
            page = fetch_bulletin_page(LANG, PAGE_SIZE, offset)
            pages_fetched += 1

            if not page:
                break

            for raw in page:
                outcome = _process_bulletin(
                    raw,
                    run,
                    range_start=range_start,
                    range_end=range_end,
                    dry_run=dry_run,
                    force=force,
                )
                if outcome == _OUTCOME_OUT_OF_RANGE:
                    done = True
                    break
                counts[outcome] += 1

            # Fewer results than requested means last page.
            if len(page) < PAGE_SIZE:
                break

            offset += PAGE_SIZE

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
