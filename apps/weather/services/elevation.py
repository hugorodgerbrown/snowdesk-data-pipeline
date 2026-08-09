"""
apps/weather/services/elevation.py — Elevation via the Open-Meteo elevation API.

Contains a single function:

  fetch_elevation(latitude, longitude, base_url=None)
      Calls the Open-Meteo elevation endpoint for one lat/lon pair and
      returns the elevation in metres above sea level. Used by
      ``apps.weather.services.forecast_points.resolve_forecast_point`` to
      populate the ``ForecastPoint.elevation`` field before quantising a
      new pin into an elevation band.

Follows the same idiom as ``weather_fetcher.py``: plain ``requests.get``
with a module-level timeout, ``raise_for_status()`` so HTTP failures
bubble to the caller, and a ``base_url`` override parameter so tests (and
a future local mirror) can point at something other than the live API.
The host and the customer-API key are resolved by
``apps.weather.services.open_meteo``.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from apps.weather.services import open_meteo

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds


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
    url = open_meteo.request_url(open_meteo.ELEVATION, base_url)
    logger.debug(
        "Fetching elevation for latitude=%s longitude=%s url=%s",
        latitude,
        longitude,
        url,
    )

    params: dict[str, str] = open_meteo.with_api_key(
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

    logger.debug(
        "Open-Meteo elevation: latitude=%s longitude=%s elevation=%s",
        latitude,
        longitude,
        elevation,
    )

    return elevation
