"""
apps/data_feed/views.py — The data feed endpoints, ``GET /api/data/v1/<feed>/``.

One view serves all four feeds (``bulletins``, ``weather``, ``locations``,
``resorts``); ``feeds.py`` says what each reads and ``auth.py`` who may
read it. Query parameters, all optional:

* ``since`` — ISO 8601 datetime with an offset; only rows updated at or
  after it.
* ``cursor`` — the previous page's ``next_cursor``.
* ``limit`` — page size, default 100, at most 500.

A bad parameter is a 400 with a JSON ``error``. Every response is
``no-store``: it is a per-client read of live data, not a page to cache.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from django_ratelimit.decorators import ratelimit

from apps.data_feed.auth import require_feed_token
from apps.data_feed.feeds import (
    FEEDS,
    FeedQueryError,
    page,
    parse_limit,
    parse_since,
)

logger = logging.getLogger(__name__)


@ratelimit(key="ip", rate="240/m", block=True)
@require_GET
@require_feed_token
def feed(request: HttpRequest, name: str) -> HttpResponse:
    """Return one page of the feed called ``name``.

    Args:
        request: The authenticated GET.
        name: The feed, one of ``feeds.FEEDS`` (the URLconf allows no other).

    Returns:
        The page as JSON, or a 400 naming the bad parameter.

    """
    try:
        body = page(
            FEEDS[name],
            since=parse_since(request.GET.get("since")),
            cursor=request.GET.get("cursor") or None,
            limit=parse_limit(request.GET.get("limit")),
        )
    except FeedQueryError as exc:
        response: HttpResponse = JsonResponse({"error": str(exc)}, status=400)
    else:
        logger.info(
            "data feed: client=%s feed=%s items=%d",
            request.feed_client.client_id,  # type: ignore[attr-defined]
            name,
            len(body["results"]),
        )
        response = JsonResponse(body)
    response["Cache-Control"] = "no-store"
    return response
