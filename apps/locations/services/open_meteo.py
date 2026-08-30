"""
apps/locations/services/open_meteo.py — Open-Meteo request addressing.

Contains the two helpers that put the host and the customer-API key in one
place rather than repeating them at each request site:

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

The key is scoped to the hosts it was issued for (SNOW-579), so a
dev-mirror or test ``base_url`` falls outside the customer set and is
sent no key.

**Elevation only.** SNOW-762 stripped the weather app, and with it the
forecast and archive endpoints this module used to address. What remains
is the elevation lookup, which is location domain rather than weather: it
answers how high a ``Location`` is, not what the sky is doing above it.
The weather rebuild (SNOW-757) addresses its own endpoints.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings

# Endpoint path segment, appended to the resolved base URL.
ELEVATION = "elevation"

# The free public host. A configured host that is still this one is on the
# free tier, which takes no key. Matching on hostname rather than the whole
# base URL means a trailing slash, an ``http://`` scheme, or a different
# path version cannot defeat the comparison.
#
# This must stay in step with the ``OPEN_METEO_API_BASE_URL`` default in
# ``config/settings/base.py``; the "shipped defaults send no key" test in
# tests/locations/services/test_open_meteo.py fails if they drift apart.
FREE_HOSTNAMES = frozenset({"api.open-meteo.com"})


def request_url(endpoint: str, base_url: str | None = None) -> str:
    """
    Build the full request URL for one Open-Meteo endpoint.

    Args:
        endpoint: The endpoint path segment — ``ELEVATION``.
        base_url: When set, overrides the configured host. Defaults to
            ``None``, which uses ``settings.OPEN_METEO_API_BASE_URL``.

    Returns:
        The absolute request URL.

    """
    if base_url is None:
        base_url = settings.OPEN_METEO_API_BASE_URL
    return f"{base_url}/{endpoint}"


def _hostname(url: str) -> str:
    """Return the lowercased hostname of ``url``, or an empty string."""
    return (urlsplit(url).hostname or "").lower()


def _customer_hostnames() -> frozenset[str]:
    """
    Return the configured host when it is not the free public host.

    A host the operator has moved off its free default is, by definition,
    the paid tier they hold the key for. Deriving the set this way needs
    no extra setting and does not depend on the ``customer-`` hostname
    convention, which Open-Meteo documents but which should not be taken
    on trust.

    Returns:
        The hostnames the ``apikey`` parameter may be sent to. Empty on
        the free tier, which is the shipped default.

    """
    configured = _hostname(settings.OPEN_METEO_API_BASE_URL)
    if configured and configured not in FREE_HOSTNAMES:
        return frozenset({configured})
    return frozenset()


def with_api_key(params: dict[str, str], url: str) -> dict[str, str]:
    """
    Return ``params`` with the Open-Meteo customer-API key appended.

    The key is added only when ``url`` is aimed at a configured customer
    host. Everything else — the free public host, the dev mirror, a test
    double, or no key configured at all — gets the mapping back
    unchanged.

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
