"""
apps/ingest/sources/albina.py — The ALBINA (EUREGIO) avalanche.report CDN.

The HTTP half of the ALBINA pipeline, moved out of
``apps/bulletins/services/albina_fetcher.py`` (SNOW-1249). The CDN publishes
one CAAML v6 file per date and region at::

    {base}/{date}/{date}_{region}_en_CAAMLv6.json

  fetch_albina_for_date        one (date, region) slot; a 404 is an empty
                               slot, not an error.
  normalise_albina_response    the file's envelope → a flat list.
  parse_issued_at              a bulletin's publication time, tolerant of
                               either field being absent.

The walk across dates and regions lives in ``apps/ingest/runs.py``.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import requests

from apps.ingest import conf
from apps.ingest.normalise.fetcher_common import normalise_bulletin_response

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds


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
    resolved_base = (
        base_url if base_url is not None else conf.text("ALBINA_API_BASE_URL")
    )
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
    return normalise_albina_response(data, date_str, region)


def normalise_albina_response(
    data: Any, date_str: str, region: str
) -> list[dict[str, Any]]:
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


def parse_issued_at(raw: dict[str, Any], fallback: datetime) -> datetime:
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
