"""
apps/locations/services/elevation.py — Elevation via the Open-Meteo elevation API.

Contains a single function:

  fetch_elevation(latitude, longitude, base_url=None)
      Calls the Open-Meteo elevation endpoint for one lat/lon pair and
      returns the elevation in metres above sea level.

Plain ``requests.get`` with a module-level timeout, ``raise_for_status()``
so HTTP failures bubble to the caller, and a ``base_url`` override
parameter so tests (and a future local mirror) can point at something
other than the live API. The host and the customer-API key are resolved
by ``apps.locations.services.open_meteo``.

**Why this lives in the locations app.** It was
``apps.weather.services.elevation`` until SNOW-762 stripped the weather
app. A location's own height is location domain, not weather — it is a
fixed property of a point on the ground, fetched once and stored on
``Location.elevation_m``, where weather is a time-varying observation
about the air above it. The three callers left standing after the strip
are all location work: ``link_region_centroid_locations`` (SNOW-696's
backfill, which SNOW-758 depends on running), ``import_locations``, and
favourite creation in ``apps.favourites.services``.
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from apps.locations.services import open_meteo

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
