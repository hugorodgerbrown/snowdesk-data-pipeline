"""
apps/ingest/sources/open_meteo.py — Open-Meteo addressing, elevation and forecasts.

Contains the HTTP side of everything Snowdesk asks Open-Meteo:

  request_url(endpoint, base_url=None)
      Build the full request URL for one endpoint. Falls back to the
      configured host when ``base_url`` is not overridden.

  with_api_key(params, url)
      Append the ``apikey`` query parameter when ``url`` is aimed at a
      host the operator has configured as a customer host.

  fetch_elevation(latitude, longitude, base_url=None)
      The height of one coordinate, from the elevation endpoint.

  fetch_forecast(latitude, longitude, elevation, start, end, *, base_url=None)
      One forecast request over ``[start, end]``, returned as the parsed
      body. ``history_base_url()`` names the historical host the weather
      backfill passes as ``base_url``; the request shape is the same.

The free public API and the paid customer API differ in two ways: the
customer tier is served from its own hostnames and requires an ``apikey``
query parameter (SNOW-577). Both are settings, so moving between tiers is
an environment change rather than a deploy — see the ``OPEN_METEO_*``
block in ``config/settings/base.py``.

The key is scoped to the hosts it was issued for (SNOW-579), so a
dev-mirror or test ``base_url`` falls outside the customer set and is
sent no key.

Moved here from ``apps/locations/services/open_meteo.py`` and
``elevation.py``, with the forecast request lifted out of
``apps/weather/services/{fetch,backfill}.py``, by SNOW-1249. The weather app
keeps the walk over locations and the write; this module knows a coordinate
and a date range, never a ``Location``. Settings are read through
``apps.ingest.conf``.

**No coordinate is ever logged here** (SNOW-718, SNOW-732). This module is
handed bare floats — a curated summit, but also a user's saved pin — and a
log is the wrong place for one. The endpoint is safe to log because the
coordinate travels in ``params``, never in the url.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import requests

from apps.ingest import conf
from apps.ingest.normalise.weather import DAILY_VARIABLES, HOURLY_VARIABLES

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds

# Endpoint path segments, appended to the resolved base URL. The forecast
# and historical-forecast hosts serve the same ``forecast`` path.
ELEVATION = "elevation"
FORECAST = "forecast"

# The free public hosts. A configured host that is still one of these is on
# the free tier, which takes no key. Matching on hostname rather than the
# whole base URL means a trailing slash, an ``http://`` scheme, or a
# different path version cannot defeat the comparison.
#
# This must stay in step with the ``OPEN_METEO_*_BASE_URL`` defaults in
# ``config/settings/base.py`` (and with
# ``apps.core.settings_spec.FREE_OPEN_METEO_HOSTS``); the "shipped defaults
# send no key" test in tests/ingest/sources/test_open_meteo.py fails if
# they drift apart.
FREE_HOSTNAMES = frozenset(
    {
        "api.open-meteo.com",
        "historical-forecast-api.open-meteo.com",
    }
)

# The settings a configured host can come from. Each is resolved
# independently: an operator can move one tier onto the paid subscription
# and leave the other on the free public host, and the key follows the
# host rather than the deploy.
_BASE_URL_SETTINGS = ("OPEN_METEO_API_BASE_URL", "OPEN_METEO_HISTORY_BASE_URL")


def request_url(endpoint: str, base_url: str | None = None) -> str:
    """
    Build the full request URL for one Open-Meteo endpoint.

    Args:
        endpoint: The endpoint path segment — ``ELEVATION`` or ``FORECAST``.
        base_url: When set, overrides the configured host. Defaults to
            ``None``, which uses ``settings.OPEN_METEO_API_BASE_URL``.

    Returns:
        The absolute request URL.

    """
    if base_url is None:
        base_url = conf.text("OPEN_METEO_API_BASE_URL")
    return f"{base_url}/{endpoint}"


def _hostname(url: str) -> str:
    """Return the lowercased hostname of ``url``, or an empty string."""
    return (urlsplit(url).hostname or "").lower()


def _customer_hostnames() -> frozenset[str]:
    """
    Return the configured hosts that are not free public ones.

    A host the operator has moved off its free default is, by definition,
    the paid tier they hold the key for. Deriving the set this way needs
    no extra setting and does not depend on the ``customer-`` hostname
    convention, which Open-Meteo documents but which should not be taken
    on trust.

    Both base-URL settings are considered, so the forecast tier and the
    historical tier (SNOW-731) can be moved independently — pointing one
    at the customer host does not start sending the key to the other.

    Returns:
        The hostnames the ``apikey`` parameter may be sent to. Empty on
        the free tier, which is the shipped default.

    """
    return frozenset(
        {
            hostname
            for name in _BASE_URL_SETTINGS
            if (hostname := _hostname(conf.text(name)))
            and hostname not in FREE_HOSTNAMES
        }
    )


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
    api_key = conf.text("OPEN_METEO_API_KEY")
    if not api_key:
        return params
    if _hostname(url) not in _customer_hostnames():
        return params
    return {**params, "apikey": api_key}


def history_base_url() -> str:
    """Return the configured historical-forecast host (SNOW-731).

    Returns:
        ``OPEN_METEO_HISTORY_BASE_URL``.

    """
    return conf.text("OPEN_METEO_HISTORY_BASE_URL")


def fetch_elevation(
    latitude: float,
    longitude: float,
    base_url: str | None = None,
) -> float:
    """
    Fetch the elevation (metres above sea level) for one lat/lon pair.

    Calls the Open-Meteo elevation endpoint (or a mirror when ``base_url``
    is set) and extracts the single-point elevation from the response's
    ``elevation`` array.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.
        base_url: When set, overrides the configured host as the endpoint
            base. The actual request goes to ``f"{base_url}/elevation"``.
            Defaults to ``None``, which uses
            ``settings.OPEN_METEO_API_BASE_URL``. An override points at a
            host outside the customer set, so no ``apikey`` is sent.

    Returns:
        The elevation in metres above sea level.

    Raises:
        requests.HTTPError: If the Open-Meteo API returns a non-2xx status.
        KeyError: If the response is missing the ``elevation`` key.
        IndexError: If the ``elevation`` array in the response is empty.

    """
    url = request_url(ELEVATION, base_url)
    logger.debug("Fetching elevation from %s", url)

    params = with_api_key(
        {
            "latitude": str(latitude),
            "longitude": str(longitude),
        },
        url,
    )
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data: dict[str, Any] = response.json()

    elevation: float = data["elevation"][0]

    logger.debug("Open-Meteo resolved elevation=%s", elevation)

    return elevation


def fetch_forecast(
    latitude: float,
    longitude: float,
    elevation: float | None,
    start: date,
    end: date,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Issue one Open-Meteo forecast request and return the parsed payload.

    Asks for ``DAILY_VARIABLES`` and ``HOURLY_VARIABLES`` over
    ``[start, end]`` in the location's own time zone. ``elevation``, when
    known, statistically downscales the forecast to the point's own
    altitude rather than the model cell's mean terrain; a point with none
    is still fetched, and the forecast is simply the cell's.

    The live fetch and the historical backfill send the same request to
    different hosts, so a backfilled day and a live day are comparable.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.
        elevation: The point's height in metres, or None.
        start: First day of the window.
        end: Last day of the window, inclusive.
        base_url: Overrides the configured host. Defaults to None, which
            uses ``OPEN_METEO_API_BASE_URL``; the backfill passes
            ``history_base_url()``.

    Returns:
        The parsed JSON body.

    Raises:
        requests.HTTPError: The API returned a non-2xx status.

    """
    url = request_url(FORECAST, base_url)
    params: dict[str, str] = {
        "latitude": str(latitude),
        "longitude": str(longitude),
        "daily": DAILY_VARIABLES,
        "hourly": HOURLY_VARIABLES,
        "timezone": "auto",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }
    if elevation is not None:
        params["elevation"] = str(elevation)

    logger.debug("Fetching forecast from %s start=%s end=%s", url, start, end)

    response = requests.get(
        url, params=with_api_key(params, url), timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload
