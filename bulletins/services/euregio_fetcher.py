"""
bulletins/services/euregio_fetcher.py — Fetching and persisting EUREGIO bulletins.

Walks the ALBINA CDN at::

    {base}/{date}/{date}_{region}_en_CAAMLv6.json

for a date range × region combination, deduplicating by ``bulletinID`` and
persisting each bulletin via the shared ``upsert_bulletin`` pipeline.

Each CDN file is a JSON array (or ``{"bulletins": [...]}`` envelope) of raw
CAAML v6 bulletin dicts. A 404 response for a given (date, region) pair means
"no bulletin published for this slot" — not an error. Any other 4xx/5xx
response logs a warning and skips the slot.

``fetch_euregio_for_date`` and ``run_euregio_pipeline`` are the two public
entry points. The management command ``fetch_euregio_bulletins`` calls
``run_euregio_pipeline``; unit tests can call either independently via mocked
``requests.get``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import requests
from django.conf import settings

from bulletins.models import Bulletin, PipelineRun
from bulletins.services.data_fetcher import upsert_bulletin

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds

_ONE_DAY = timedelta(days=1)


def fetch_euregio_for_date(
    target_date: date,
    region: str,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch EUREGIO bulletins for one (date, region) pair from the ALBINA CDN.

    The CDN publishes per-date, per-region CAAMLv6 files at::

        {base}/{date}/{date}_{region}_en_CAAMLv6.json

    A 404 response means no bulletin was published for this date/region
    combination (off-season gap, or the region didn't publish that day).
    That is the expected shape for an archive gap, not an error.

    Args:
        target_date: The date whose bulletin to fetch.
        region: ALBINA region code, e.g. ``"AT-07"``.
        base_url: Override the ALBINA CDN base URL. Falls back to
            ``settings.EUREGIO_API_BASE_URL`` when ``None``.

    Returns:
        A flat list of raw bulletin dicts. Empty when the CDN returns 404
        or when the response body has an unexpected shape.

    Raises:
        requests.HTTPError: If the CDN returns any non-404 error status.

    """
    resolved_base = base_url if base_url is not None else settings.EUREGIO_API_BASE_URL
    date_str = target_date.isoformat()
    url = f"{resolved_base}/{date_str}/{date_str}_{region}_en_CAAMLv6.json"

    logger.debug(
        "Fetching EUREGIO bulletins: date=%s region=%s url=%s",
        date_str,
        region,
        url,
    )

    response = requests.get(url, timeout=REQUEST_TIMEOUT)

    if response.status_code == 404:
        logger.debug("No bulletin for %s / %s (404)", date_str, region)
        return []

    response.raise_for_status()

    data: Any = response.json()
    return _normalise_response(data, date_str, region)


def _normalise_response(data: Any, date_str: str, region: str) -> list[dict[str, Any]]:
    """
    Normalise an ALBINA CDN response into a flat list of bulletin dicts.

    The CDN may return:

    - A JSON array of bulletin dicts (most common).
    - A ``{"bulletins": [...]}`` envelope dict.

    Args:
        data: The parsed JSON response from the CDN.
        date_str: ISO date string used in warning messages.
        region: Region code used in warning messages.

    Returns:
        A flat list of bulletin dicts.

    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "bulletins" in data:
        result: list[dict[str, Any]] = data["bulletins"]
        return result
    logger.warning(
        "Unexpected ALBINA CDN body shape for %s / %s — type=%s; returning []",
        date_str,
        region,
        type(data).__name__,
    )
    return []


def _parse_issued_at(raw: dict[str, Any], fallback: datetime) -> datetime:
    """
    Derive an ``issued_at`` datetime from a raw EUREGIO bulletin dict.

    Tries ``publicationTime`` first, then ``validTime.startTime``, then
    falls back to ``fallback`` when neither parses.

    Args:
        raw: A raw bulletin dict from the ALBINA CDN.
        fallback: Returned when neither timestamp field is parseable.

    Returns:
        A UTC-aware datetime.

    """
    for key in ("publicationTime",):
        value = raw.get(key, "")
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(
                    UTC
                )
            except ValueError:
                pass
    start_raw: str = (raw.get("validTime") or {}).get("startTime", "")
    if start_raw:
        try:
            return datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(
                UTC
            )
        except ValueError:
            pass
    return fallback


def _process_euregio_bulletin(
    raw: dict[str, Any],
    run: PipelineRun,
    seen_ids: set[str],
    *,
    range_start: datetime,
    range_end: datetime,
    dry_run: bool,
    force: bool,
    on_fetched: Callable[[dict[str, Any]], None] | None,
) -> str:
    """
    Decide how to handle a single EUREGIO bulletin.

    Returns a short outcome tag: ``"created"``, ``"updated"``, ``"skipped"``,
    ``"duplicate"``, or ``"failed"``.

    Args:
        raw: A single raw bulletin dict.
        run: The active PipelineRun instance.
        seen_ids: Mutable set of already-processed bulletin IDs (dedup).
        range_start: Lower bound of the ingest window (UTC-aware).
        range_end: Upper bound of the ingest window (UTC-aware).
        dry_run: When True, log and count without writing.
        force: When True, upsert even if the bulletin already exists.
        on_fetched: Optional callback called for every raw bulletin, before
            all other decisions.

    """
    if on_fetched is not None:
        on_fetched(raw)

    bulletin_id: str = raw.get("bulletinID", "")
    if not bulletin_id:
        logger.warning("EUREGIO bulletin with no bulletinID — skipping")
        return "skipped"

    # Deduplicate — the same bulletin ID appears in multiple region files
    # when its coverage spans regions.
    if bulletin_id in seen_ids:
        return "duplicate"
    seen_ids.add(bulletin_id)

    issued_at = _parse_issued_at(raw, fallback=range_start)
    if not (range_start <= issued_at < range_end):
        return "skipped"

    if dry_run:
        logger.info("[dry-run] Would store EUREGIO %s", bulletin_id)
        return "created"

    if not force and Bulletin.objects.filter(bulletin_id=bulletin_id).exists():
        return "skipped"

    try:
        created = upsert_bulletin(raw, run)
    except Exception as exc:
        logger.exception("Failed to upsert EUREGIO bulletin %s: %s", bulletin_id, exc)
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return "failed"

    return "created" if created else "updated"


def run_euregio_pipeline(
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
    Orchestrate a full EUREGIO bulletin ingest over a date range × region set.

    Walks the ALBINA CDN for every (date, region) combination in the
    Cartesian product of ``[start..end]`` × ``regions``, deduplicating
    bulletins by ``bulletinID`` so that cross-region bulletins are only
    stored once.

    Args:
        start: First date to include (inclusive).
        end: Last date to include (inclusive).
        regions: Tuple of EUREGIO region codes to query. Falls back to
            ``settings.EUREGIO_REGIONS`` when ``None``.
        triggered_by: Human-readable label for who/what triggered the run.
        dry_run: If True, fetch data but do not write to the database.
        force: If True, upsert bulletins that already exist in the DB.
        base_url: Override the ALBINA CDN base URL. ``None`` defers to
            ``settings.EUREGIO_API_BASE_URL``.
        on_fetched: Optional per-record callback invoked once for every raw
            bulletin returned by the CDN, before dedup/dry-run decisions.
            The ``--stash`` flag on the management command wires this to a
            list collector so the on-disk archive captures everything the
            fetcher saw.
        delay: Seconds to sleep between successive CDN requests. ``0.0``
            (default) is a no-op; positive values pace requests to avoid
            hammering the CDN during multi-year backfills.

    Returns:
        The completed (or failed) ``PipelineRun`` instance.

    """
    resolved_regions: tuple[str, ...] = (
        regions if regions is not None else settings.EUREGIO_REGIONS
    )

    run = PipelineRun.objects.create(triggered_by=triggered_by)
    run.mark_running()

    counts: dict[str, int] = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "duplicate": 0,
        "failed": 0,
    }
    request_count = 0
    seen_ids: set[str] = set()

    range_start = datetime(start.year, start.month, start.day, tzinfo=UTC)
    range_end = datetime(end.year, end.month, end.day, tzinfo=UTC) + _ONE_DAY

    try:
        logger.info(
            "EUREGIO pipeline run %s: range %s–%s regions=%s force=%s dry_run=%s",
            run.pk,
            start,
            end,
            ",".join(resolved_regions),
            force,
            dry_run,
        )

        current = start
        while current <= end:
            for region in resolved_regions:
                if delay > 0 and request_count > 0:
                    time.sleep(delay)

                try:
                    bulletins = fetch_euregio_for_date(
                        current, region, base_url=base_url
                    )
                except requests.HTTPError as exc:
                    logger.warning(
                        "HTTP error fetching %s / %s: %s — skipping slot",
                        current.isoformat(),
                        region,
                        exc,
                    )
                    run.records_failed += 1
                    run.save(update_fields=["records_failed"])
                    request_count += 1
                    continue

                request_count += 1

                for raw in bulletins:
                    outcome = _process_euregio_bulletin(
                        raw,
                        run,
                        seen_ids,
                        range_start=range_start,
                        range_end=range_end,
                        dry_run=dry_run,
                        force=force,
                        on_fetched=on_fetched,
                    )
                    counts[outcome] += 1

            current += _ONE_DAY

    except Exception as exc:
        run.mark_failed(exc)
        return run

    logger.info(
        "EUREGIO pipeline run %s finished: %d requests, "
        "%d created, %d updated, %d skipped",
        run.pk,
        request_count,
        counts["created"],
        counts["updated"],
        counts["skipped"],
    )

    if dry_run:
        run.mark_success(0, 0)
    else:
        run.mark_success(counts["created"], counts["updated"])

    return run


def latest_euregio_date() -> date | None:
    """
    Return the most recent ``valid_from`` date of any EUREGIO bulletin in the DB.

    Used by the management command to derive the default ``--start-date``
    (resume from where the last run left off). Returns ``None`` when no
    EUREGIO bulletins exist yet.

    Returns:
        The latest ``valid_from.date()`` of any EUREGIO bulletin, or ``None``.

    """
    result = (
        Bulletin.objects.filter(render_model__source="euregio")
        .order_by("-valid_from")
        .values_list("valid_from", flat=True)
        .first()
    )
    if result is None:
        return None
    return result.date()
