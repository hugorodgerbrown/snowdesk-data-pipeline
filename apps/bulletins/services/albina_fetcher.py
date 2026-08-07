"""
apps/bulletins/services/albina_fetcher.py — Fetching and persisting ALBINA bulletins.

Walks the ALBINA CDN at::

    {base}/{date}/{date}_{region}_en_CAAMLv6.json

for a date range × region combination, deduplicating by ``bulletinID`` and
persisting each bulletin via the shared ``upsert_bulletin`` pipeline.

Each CDN file is a JSON array (or ``{"bulletins": [...]}`` envelope) of raw
CAAML v6 bulletin dicts. A 404 response for a given (date, region) pair means
"no bulletin published for this slot" — not an error. Any other 4xx/5xx
response logs a warning and skips the slot.

``fetch_albina_for_date``, ``run_albina_pipeline``, and ``albina_stash_writer``
are the public entry points. The management command ``fetch_bulletins`` calls
``run_albina_pipeline`` and ``albina_stash_writer``; unit tests can call any
of them independently via mocked ``requests.get``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from django.conf import settings

from apps.bulletins.models import Bulletin, PipelineRun
from apps.bulletins.services.fetcher_common import (
    OUTCOME_CREATED,
    OUTCOME_FAILED,
    OUTCOME_SKIPPED,
    OUTCOME_UPDATED,
    normalise_bulletin_response,
    write_ndjson_archive,
)
from apps.bulletins.services.slf_fetcher import upsert_bulletin

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds

_ONE_DAY = timedelta(days=1)

_ALBINA_PDF_API = "https://api.avalanche.report/albina/api/bulletins/pdf"


def _albina_pdf_url(raw: dict[str, Any], region: str) -> str:
    """
    Derive the ALBINA on-demand PDF URL from a raw bulletin dict.

    The URL is constructed from fields already present in the raw CAAML
    payload — no additional network call is needed.  The ``region``
    parameter comes from the per-slot fetch loop in
    ``run_albina_pipeline``.

    Args:
        raw: A single raw bulletin dict from the ALBINA CDN.  Must contain
            ``validTime.startTime``.
        region: The ALBINA region code used for this bulletin's CDN slot
            (e.g. ``"AT-07"``, ``"IT-32-BZ"``, ``"IT-32-TN"``).  Passed
            through to the PDF endpoint as the ``region`` query parameter.

    Returns:
        The full ALBINA PDF API URL, e.g.
        ``https://api.avalanche.report/albina/api/bulletins/pdf
        ?date=2025-11-30T16%3A00%3A00Z&region=AT-07&lang=en&grayscale=false``.

    """
    start_time: str = (raw.get("validTime") or {}).get("startTime", "")
    lang: str = raw.get("lang", "en")
    params = {
        "date": start_time,
        "region": region,
        "lang": lang,
        "grayscale": "false",
    }
    return f"{_ALBINA_PDF_API}?{urlencode(params)}"


def fetch_albina_for_date(
    target_date: date,
    region: str,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """
    Fetch ALBINA bulletins for one (date, region) pair from the ALBINA CDN.

    The CDN publishes per-date, per-region CAAMLv6 files at::

        {base}/{date}/{date}_{region}_en_CAAMLv6.json

    A 404 response means no bulletin was published for this date/region
    combination (off-season gap, or the region didn't publish that day).
    That is the expected shape for an archive gap, not an error.

    Args:
        target_date: The date whose bulletin to fetch.
        region: ALBINA region code, e.g. ``"AT-07"``.
        base_url: Override the ALBINA CDN base URL. Falls back to
            ``settings.ALBINA_API_BASE_URL`` when ``None``.

    Returns:
        A flat list of raw bulletin dicts. Empty when the CDN returns 404
        or when the response body has an unexpected shape.

    Raises:
        requests.HTTPError: If the CDN returns any non-404 error status.

    """
    resolved_base = base_url if base_url is not None else settings.ALBINA_API_BASE_URL
    date_str = target_date.isoformat()
    url = f"{resolved_base}/{date_str}/{date_str}_{region}_en_CAAMLv6.json"

    logger.debug(
        "Fetching ALBINA bulletins: date=%s region=%s url=%s",
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

    Delegates to ``normalise_bulletin_response`` from ``fetcher_common``,
    passing ``"ALBINA {date_str}/{region}"`` as the source label so warning
    messages retain the per-slot context.

    Args:
        data: The parsed JSON response from the CDN.
        date_str: ISO date string used in warning messages.
        region: Region code used in warning messages.

    Returns:
        A flat list of bulletin dicts.

    """
    return normalise_bulletin_response(data, f"ALBINA {date_str}/{region}")


def _parse_issued_at(raw: dict[str, Any], fallback: datetime) -> datetime:
    """
    Derive an ``issued_at`` datetime from a raw ALBINA bulletin dict.

    Tries ``publicationTime`` first, then ``validTime.startTime``, then
    falls back to ``fallback`` when neither parses.

    Args:
        raw: A raw bulletin dict from the ALBINA CDN.
        fallback: Returned when neither timestamp field is parseable.

    Returns:
        A UTC-aware datetime.

    """
    candidates: list[str] = [
        raw.get("publicationTime", "") or "",
        (raw.get("validTime") or {}).get("startTime", "") or "",
    ]
    for value in candidates:
        if not value:
            continue
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            continue
    return fallback


# ALBINA-specific outcome — a bulletin whose ``bulletinID`` was already
# processed in a prior CDN slot (it spans multiple regions).  Not a shared
# constant because this dedup tag is meaningless for the SLF and MF pipelines.
_OUTCOME_DUPLICATE = "duplicate"


def _process_albina_bulletin(
    raw: dict[str, Any],
    run: PipelineRun,
    seen_ids: set[str],
    *,
    region: str,
    range_start: datetime,
    range_end: datetime,
    dry_run: bool,
    force: bool,
    on_fetched: Callable[[dict[str, Any]], None] | None,
) -> str:
    """
    Decide how to handle a single ALBINA bulletin.

    Returns a short outcome tag: ``"created"``, ``"updated"``, ``"skipped"``,
    ``"duplicate"``, or ``"failed"``.

    Args:
        raw: A single raw bulletin dict.
        run: The active PipelineRun instance.
        seen_ids: Mutable set of already-processed bulletin IDs (dedup).
        region: The ALBINA region code whose CDN slot was fetched
            (e.g. ``"AT-07"``). Passed through to ``_albina_pdf_url``.
        range_start: Lower bound of the ingest window (UTC-aware).
        range_end: Upper bound of the ingest window (UTC-aware).
        dry_run: When True, log and count without writing.
        force: When True, upsert even if the bulletin already exists.
        on_fetched: Optional callback called for each unique bulletin (after
            dedup) so consumers like ``--stash`` see one entry per
            ``bulletinID`` even when a bulletin spans regions.

    """
    bulletin_id: str = raw.get("bulletinID", "")
    if not bulletin_id:
        logger.warning("ALBINA bulletin with no bulletinID — skipping")
        return OUTCOME_SKIPPED

    # Deduplicate — the same bulletin ID appears in multiple region files
    # when its coverage spans regions.
    if bulletin_id in seen_ids:
        return _OUTCOME_DUPLICATE
    seen_ids.add(bulletin_id)

    if on_fetched is not None:
        on_fetched(raw)

    issued_at = _parse_issued_at(raw, fallback=range_start)
    if not (range_start <= issued_at < range_end):
        return OUTCOME_SKIPPED

    if dry_run:
        logger.info("[dry-run] Would store ALBINA %s", bulletin_id)
        return OUTCOME_CREATED

    if not force and Bulletin.objects.filter(bulletin_id=bulletin_id).exists():
        return OUTCOME_SKIPPED

    try:
        created = upsert_bulletin(raw, run, pdf_url=_albina_pdf_url(raw, region))
    except Exception as exc:
        logger.exception("Failed to upsert ALBINA bulletin %s: %s", bulletin_id, exc)
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return OUTCOME_FAILED

    return OUTCOME_CREATED if created else OUTCOME_UPDATED


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

    Walks the ALBINA CDN for every (date, region) combination in the
    Cartesian product of ``[start..end]`` × ``regions``, deduplicating
    bulletins by ``bulletinID`` so that cross-region bulletins are only
    stored once.

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
        _OUTCOME_DUPLICATE: 0,
        OUTCOME_FAILED: 0,
    }
    request_count = 0
    seen_ids: set[str] = set()

    range_start = datetime(start.year, start.month, start.day, tzinfo=UTC)
    range_end = datetime(end.year, end.month, end.day, tzinfo=UTC) + _ONE_DAY

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

        current = start
        while current <= end:
            for region in resolved_regions:
                if delay > 0 and request_count > 0:
                    time.sleep(delay)

                try:
                    bulletins = fetch_albina_for_date(
                        current, region, base_url=base_url
                    )
                except requests.HTTPError as exc:
                    logger.exception(
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
                    outcome = _process_albina_bulletin(
                        raw,
                        run,
                        seen_ids,
                        region=region,
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
        "ALBINA pipeline run %s finished: %d requests, "
        "%d created, %d updated, %d skipped",
        run.pk,
        request_count,
        counts[OUTCOME_CREATED],
        counts[OUTCOME_UPDATED],
        counts[OUTCOME_SKIPPED],
    )

    if dry_run:
        run.mark_success(0, 0)
    else:
        run.mark_success(counts[OUTCOME_CREATED], counts[OUTCOME_UPDATED])

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
