"""
apps/ingest/sources/slf.py — The SLF CAAML list API.

The HTTP half of the SLF pipeline, moved out of
``apps/bulletins/services/slf_fetcher.py`` (SNOW-1249):

  resolve_base_url     which base URL a fetch uses — an explicit override,
                       then the ``SLF_API_LEGACY_URL`` pin (SNOW-900), then
                       ``SLF_API_BASE_URL``.
  fetch_bulletin_page  one page of the paginated list API, unwrapped to a
                       flat list of CAAML v6 bulletins.
  log_caaml_shape      what shape each page turned out to be.

The walk across pages lives in ``apps/ingest/runs.py``; deciding what to do
with each bulletin stays in the host app.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from apps.ingest import conf
from apps.ingest.normalise.slf import (
    CAAML_SHAPE_PER_REGION,
    DEFAULT_LANG,
    detect_caaml_shape,
    normalise_slf_response,
)

logger = logging.getLogger(__name__)

LANG = DEFAULT_LANG
PAGE_SIZE = 50
REQUEST_TIMEOUT = 30  # seconds


def resolve_base_url(base_url: str | None) -> str:
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

    legacy_url = conf.text("SLF_API_LEGACY_URL")
    if legacy_url:
        # WARNING, every fetch, for as long as it is set: this is an
        # emergency lever pointed at an endpoint SLF intend to retire, not
        # a configuration to settle into. A pin left on by accident should
        # be impossible to miss in the log.
        logger.warning(
            "SLF is pinned to settings.SLF_API_LEGACY_URL (%s) instead of "
            "SLF_API_BASE_URL (%s)",
            legacy_url,
            conf.text("SLF_API_BASE_URL"),
        )
        return legacy_url

    return conf.text("SLF_API_BASE_URL")


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
            ``resolve_base_url``, so ``None`` picks up the legacy pin
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
    resolved_base = resolve_base_url(base_url)
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
    log_caaml_shape(bulletins, resolved_base)
    return bulletins


def log_caaml_shape(bulletins: list[dict[str, Any]], base_url: str) -> None:
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
