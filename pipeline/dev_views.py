"""
pipeline/dev_views.py — Development-only SLF mirror view.

Replays ``sample_data/slf_archive.ndjson`` with the same
``limit``/``offset`` paging contract as the upstream SLF CAAML API:
reverse-chronological by ``publicationTime``, paginated by offset,
fewer-than-``limit`` items signals the last page.

Wired up only when ``settings.DEBUG`` is true (see ``config/urls.py``);
production never imports this module. The companion command
``fetch_bulletins --source local-mirror --commit`` lets an empty
local DB be re-populated end-to-end through the production fetch path
against deterministic, committed input.

The view returns a flat JSON list — the simplest of the response
shapes that ``data_fetcher._normalise_response`` already handles.
"""

import logging

from django.conf import settings
from django.http import HttpRequest, JsonResponse

from pipeline.services.data_fetcher import PAGE_SIZE
from pipeline.services.slf_archive import read_archive

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
