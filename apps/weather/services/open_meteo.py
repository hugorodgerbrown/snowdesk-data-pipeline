"""
apps/weather/services/open_meteo.py — Open-Meteo request addressing.

Every Open-Meteo call in the pipeline — elevation, forecast, archive —
goes through the two helpers here so that the host and the customer-API
key are configured in one place rather than repeated at each request site:

  request_url(endpoint, base_url=None)
      Build the full request URL for one endpoint. Falls back to the
      configured host when ``base_url`` is not overridden.

  with_api_key(params, url)
      Append the ``apikey`` query parameter when ``url`` is aimed at a
      host the operator has configured as a customer host.

The free public API and the paid customer API differ in two ways: the
customer tier is served from its own hostnames and requires an ``apikey``
query parameter (SNOW-577). Both are settings, so moving between tiers is
an environment change rather than a deploy — see the ``OPEN_METEO_*``
block in ``config/settings/base.py``.

The key is scoped to the hosts it was issued for (SNOW-579). Open-Meteo
gates the historical API behind a subscription while leaving forecast and
elevation on the free tier, so the two host settings are routinely on
different tiers — sending the key to whichever host a request happens to
be aimed at would hand a credential to the free public API. A dev-mirror
or test ``base_url`` falls outside the customer set for the same reason.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings

# Endpoint path segments, appended to the resolved base URL.
ELEVATION = "elevation"
FORECAST = "forecast"
ARCHIVE = "archive"

# The free public hosts. A configured host that is still one of these is on
# the free tier, which takes no key. Matching on hostname rather than the
# whole base URL means a trailing slash, an ``http://`` scheme, or a
# different path version cannot defeat the comparison.
#
# These must stay in step with the ``OPEN_METEO_*_BASE_URL`` defaults in
# ``config/settings/base.py``; the "shipped defaults send no key" test in
# tests/bulletins/services/test_open_meteo.py fails if they drift apart.
FREE_HOSTNAMES = frozenset(
    {
        "api.open-meteo.com",
        "archive-api.open-meteo.com",
    }
)


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


def _hostname(url: str) -> str:
    """Return the lowercased hostname of ``url``, or an empty string."""
    return (urlsplit(url).hostname or "").lower()


def _customer_hostnames() -> frozenset[str]:
    """
    Return the configured hosts that are not free public hosts.

    A host the operator has moved off its free default is, by definition,
    the paid tier they hold the key for. Deriving the set this way needs
    no extra setting and does not depend on the ``customer-`` hostname
    convention, which Open-Meteo documents but which should not be taken
    on trust.

    Returns:
        The hostnames the ``apikey`` parameter may be sent to. Empty on
        the free tier, which is the shipped default.

    """
    configured = (
        _hostname(settings.OPEN_METEO_API_BASE_URL),
        _hostname(settings.OPEN_METEO_ARCHIVE_BASE_URL),
    )
    return frozenset(host for host in configured if host and host not in FREE_HOSTNAMES)


def with_api_key(params: dict[str, str], url: str) -> dict[str, str]:
    """
    Return ``params`` with the Open-Meteo customer-API key appended.

    The key is added only when ``url`` is aimed at a configured customer
    host. Everything else — the free public hosts, the dev mirror, a test
    double, or no key configured at all — gets the mapping back
    unchanged.

    That distinction is what makes a mixed-tier setup work: with only
    ``OPEN_METEO_ARCHIVE_BASE_URL`` moved to the paid tier, archive
    requests carry the key and forecast and elevation requests to the
    free host do not (SNOW-579).

    Args:
        params: The request parameters built by the caller.
        url: The resolved request URL, as returned by ``request_url``.

    Returns:
        A new mapping including ``apikey``, or ``params`` unchanged.

    """
    if not settings.OPEN_METEO_API_KEY:
        return params
    if _hostname(url) not in _customer_hostnames():
        return params
    return {**params, "apikey": settings.OPEN_METEO_API_KEY}
