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
    Normalise the three response shapes the SLF API can return into a
    flat list of bulletin dicts.

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


def _get_or_create_region(region_id: str, name: str) -> Region:
    """
    Look up or create a Region record.

    If the region already exists but the name has changed, update it.

    Args:
        region_id: SLF region identifier, e.g. "CH-4115".
        name: Human-readable region name.

    Returns:
        The Region instance (created or existing).
    """
    region, created = Region.objects.get_or_create(
        region_id=region_id,
        defaults={"name": name},
    )

    if not created and region.name != name:
        region.name = name
        region.save(update_fields=["name", "updated_at"])

    return region


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

    defaults: dict[str, Any] = {
        "raw_data": raw_data,
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
        region = _get_or_create_region(raw_region["regionID"], raw_region["name"])
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
    return created


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

    total_created = 0
    total_updated = 0
    total_skipped = 0
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
                issued_at = _parse_dt(raw["publicationTime"])

                # Bulletin is newer than the end date — skip, keep paging.
                if issued_at >= range_end:
                    continue

                # Bulletin is older than the start date — we're past the range.
                if issued_at < range_start:
                    logger.info(
                        "Passed start boundary at %s, stopping", issued_at.isoformat()
                    )
                    done = True
                    break

                # Bulletin is within range.
                if dry_run:
                    logger.info("[dry-run] Would store %s", raw["bulletinID"])
                    total_created += 1
                    continue

                if not force:
                    exists = Bulletin.objects.filter(
                        bulletin_id=raw["bulletinID"]
                    ).exists()
                    if exists:
                        total_skipped += 1
                        continue

                created = upsert_bulletin(raw, run)
                if created:
                    total_created += 1
                else:
                    total_updated += 1

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
        total_created,
        total_updated,
        total_skipped,
    )

    if dry_run:
        run.mark_success(0, 0)
    else:
        run.mark_success(total_created, total_updated)

    return run
