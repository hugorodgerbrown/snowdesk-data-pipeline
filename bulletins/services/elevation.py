"""
bulletins/services/elevation.py — Elevation lookup via the Open-Meteo elevation API.

Contains a single function:

  fetch_elevation(latitude, longitude, base_url=None)
      Calls the Open-Meteo elevation endpoint for one lat/lon pair and
      returns the elevation in metres above sea level. Used by
      ``bulletins.services.forecast_points.resolve_forecast_point`` to
      populate the ``ForecastPoint.elevation`` field before quantising a
      new pin into an elevation band.

Follows the same idiom as ``weather_fetcher.py``: plain ``requests.get``
with a module-level timeout, ``raise_for_status()`` so HTTP failures
bubble to the caller, and a ``base_url`` override parameter so tests (and
a future local mirror) can point at something other than the live API.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)

ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
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
        base_url: When set, overrides ``ELEVATION_URL`` as the request URL.
            Defaults to ``None``, which uses the module-level constant.

    Returns:
        The elevation in metres above sea level.

    Raises:
        requests.HTTPError: If the Open-Meteo API returns a non-2xx status.
        KeyError: If the response is missing the ``elevation`` key.
        IndexError: If the ``elevation`` array in the response is empty.

    """
    url = base_url or ELEVATION_URL
    logger.debug(
        "Fetching elevation for latitude=%s longitude=%s url=%s",
        latitude,
        longitude,
        url,
    )

    params: dict[str, str] = {
        "latitude": str(latitude),
        "longitude": str(longitude),
    }
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
