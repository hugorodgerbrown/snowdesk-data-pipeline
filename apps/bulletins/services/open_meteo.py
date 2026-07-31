"""
apps/bulletins/services/open_meteo.py — Open-Meteo request addressing.

Every Open-Meteo call in the pipeline — elevation, forecast, archive —
goes through the two helpers here so that the host and the customer-API
key are configured in one place rather than repeated at each request site:

  request_url(endpoint, base_url=None)
      Build the full request URL for one endpoint. Falls back to the
      configured host when ``base_url`` is not overridden.

  with_api_key(params, base_url=None)
      Append the ``apikey`` query parameter when a key is configured and
      the request is going to the configured host.

The free public API and the paid customer API differ in two ways: the
customer tier is served from its own hostnames and requires an ``apikey``
query parameter (SNOW-577). Both are settings, so moving between tiers is
an environment change rather than a deploy — see the ``OPEN_METEO_*``
block in ``config/settings/base.py``.

The key is deliberately *not* sent when a ``base_url`` override is in
play. An override means the request is aimed at the dev mirror or a test
double rather than at Open-Meteo, and a credential belongs only to the
host it was issued for.
"""

from __future__ import annotations

from django.conf import settings

# Endpoint path segments, appended to the resolved base URL.
ELEVATION = "elevation"
FORECAST = "forecast"
ARCHIVE = "archive"


def request_url(endpoint: str, base_url: str | None = None) -> str:
    """
    Build the full request URL for one Open-Meteo endpoint.

    The archive endpoint is served from its own host on both the free and
    the paid tier, so it resolves against ``OPEN_METEO_ARCHIVE_BASE_URL``
    while elevation and forecast resolve against ``OPEN_METEO_API_BASE_URL``.
    A ``base_url`` override applies to every endpoint alike — the dev
    mirror serves all of them under one base.

    Args:
        endpoint: One of ``ELEVATION``, ``FORECAST``, or ``ARCHIVE``.
        base_url: When set, overrides the configured host. Defaults to
            ``None``, which uses the settings-derived base.

    Returns:
        The absolute request URL.

    """
    if base_url is None:
        base_url = (
            settings.OPEN_METEO_ARCHIVE_BASE_URL
            if endpoint == ARCHIVE
            else settings.OPEN_METEO_API_BASE_URL
        )
    return f"{base_url}/{endpoint}"


def with_api_key(
    params: dict[str, str],
    base_url: str | None = None,
) -> dict[str, str]:
    """
    Return ``params`` with the Open-Meteo customer-API key appended.

    Returns the mapping unchanged when no key is configured (the free
    tier, which rejects nothing but has no use for it) or when a
    ``base_url`` override means the request is not going to the
    configured host.

    Args:
        params: The request parameters built by the caller.
        base_url: The caller's ``base_url`` override, if any.

    Returns:
        A new mapping including ``apikey``, or ``params`` unchanged.

    """
    if base_url is not None or not settings.OPEN_METEO_API_KEY:
        return params
    return {**params, "apikey": settings.OPEN_METEO_API_KEY}
