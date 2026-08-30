"""
apps/bulletins/dev_views.py — Development-only mirror views for bulletin sources.

Contains two mirrors:

``slf_mirror``
    Replays ``apps/bulletins/local_mirrors/slf_archive.ndjson`` with the same
    ``limit``/``offset`` paging contract as the upstream SLF CAAML API:
    reverse-chronological by ``publicationTime``, paginated by offset,
    fewer-than-``limit`` items signals the last page.

``albina_mirror``
    Replays ``apps/bulletins/local_mirrors/albina_archive.ndjson`` with the
    same per-date, per-region URL shape as the ALBINA CDN:
    ``/<date>/<date>_<region>_en_CAAMLv6.json``. Returns bulletins whose
    ``customData.ALBINA.mainDate`` matches ``date_str`` and which cover at
    least one region whose prefix matches ``region``. Returns an empty JSON
    array when no matching bulletins are found (same semantics as a CDN
    404 gap).

Both views are wired up only when ``settings.DEBUG`` is true (see
``config/urls.py``); production never imports this module. Companion
commands ``fetch_bulletins --source local-mirror`` and
``fetch_albina_bulletins --source local-mirror`` use these views to
replay committed sample data end-to-end through the production fetch
paths.
"""

import datetime
import json
import logging
from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, JsonResponse

from apps.bulletins.services.slf_archive import read_archive
from apps.bulletins.services.slf_fetcher import PAGE_SIZE

logger = logging.getLogger(__name__)


def slf_mirror(request: HttpRequest, lang: str) -> JsonResponse:
    """
    Serve a slice of the on-disk SLF archive in upstream-compatible shape.

    Args:
        request: The incoming Django request; ``?limit`` and ``?offset``
            query params are honoured with the same semantics as the
            upstream SLF API.
        lang: Accepted for URL-shape parity with upstream but ignored
            (the archive only stores English bulletins).

    Returns:
        A ``JsonResponse`` containing the requested page as a flat
        JSON list, descending by ``publicationTime``.

    """
    try:
        limit = int(request.GET.get("limit", PAGE_SIZE))
        offset = int(request.GET.get("offset", 0))
    except ValueError:
        return JsonResponse({"error": "limit and offset must be integers"}, status=400)

    records = list(read_archive(settings.SLF_ARCHIVE_PATH))
    records.sort(key=lambda r: r["publicationTime"], reverse=True)
    page = records[offset : offset + limit]

    logger.debug(
        "slf_mirror serving lang=%s limit=%d offset=%d -> %d record(s) "
        "(archive total=%d)",
        lang,
        limit,
        offset,
        len(page),
        len(records),
    )
    return JsonResponse(page, safe=False)


def _read_albina_archive(path: Path) -> list[dict]:
    """
    Read the ALBINA NDJSON archive and return all bulletin dicts.

    Args:
        path: Filesystem path to the ``albina_archive.ndjson`` file.

    Returns:
        A list of raw bulletin dicts, one per non-empty line.

    """
    results: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                results.append(json.loads(stripped))
    return results


def albina_mirror(request: HttpRequest, date_str: str, region: str) -> JsonResponse:
    """
    Replay ``albina_archive.ndjson`` in the same shape as the ALBINA CDN.

    URL pattern: ``/dev/albina-mirror/<date>/<date>_<region>_en_CAAMLv6.json``

    Returns the subset of bulletins from the archive whose
    ``customData.ALBINA.mainDate`` equals ``date_str`` and which cover at
    least one region whose ``regionID`` starts with the requested ``region``
    prefix (e.g. ``"AT-07"`` matches ``"AT-07-01"``, ``"AT-07-02"`` etc.).

    Returns an empty JSON array when no matching bulletins are found —
    same semantics as a CDN 404 gap, but without a real 404 status so the
    fetcher's 404-tolerance path is not triggered (the fetcher treats an
    empty array as "no data for this slot", which is the intended dev
    behaviour).

    Returns a 400 JSON response when ``date_str`` is not a valid ISO date.

    Args:
        request: The incoming Django request.
        date_str: ISO date string extracted from the URL (e.g. ``"2026-01-15"``).
        region: ALBINA region code extracted from the URL (e.g. ``"AT-07"``).

    Returns:
        A ``JsonResponse`` with a flat JSON array of matching bulletin dicts.

    """
    try:
        datetime.date.fromisoformat(date_str)
    except ValueError:
        return JsonResponse(
            {"error": f"Invalid date: {date_str!r}"},
            status=400,
        )

    archive = _read_albina_archive(settings.ALBINA_ARCHIVE_PATH)

    matching = [
        b
        for b in archive
        if (b.get("customData") or {}).get("ALBINA", {}).get("mainDate") == date_str
        and any(r.get("regionID", "").startswith(region) for r in b.get("regions", []))
    ]

    logger.debug(
        "albina_mirror: date=%s region=%s -> %d/%d bulletin(s) matched",
        date_str,
        region,
        len(matching),
        len(archive),
    )
    return JsonResponse(matching, safe=False)
