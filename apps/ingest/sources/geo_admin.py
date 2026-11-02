"""
apps/ingest/sources/geo_admin.py — Reading FOEN's wildlife rest zones from geo.admin.

The HTTP half of the rest-zone import (SNOW-1263); the mapping is
``apps/ingest/normalise/rest_zones.py``. FOEN publishes Switzerland's
designated wildlife rest zones (Wildruhezonen) as the layer
``ch.bafu.wrz-wildruhezonen_portal``. The layer is not on the STAC API, but
geo.admin's MapServer ``find`` returns it as WGS84 GeoJSON, filtered by
one attribute, so it is read one canton at a time:

  fetch_canton(canton)
      Every zone ``find`` returns for one canton code, as GeoJSON-shaped
      features. A canton with no zones answers an empty list, which is a
      real answer and not a failure.

The host is ``GEO_ADMIN_API_URL``, read through ``apps.ingest.conf``. The
licence is OGD; the credit is "© FOEN".
"""

from __future__ import annotations

import logging
from typing import Any

import requests

from apps.ingest import conf

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60  # seconds

# The layer FOEN publishes the zones under.
REST_ZONE_LAYER = "ch.bafu.wrz-wildruhezonen_portal"

# Every canton code. ``find`` is asked once per code; most cantons hold no
# zones at all, and the empty answer is kept as such.
CANTONS: tuple[str, ...] = (
    "AG", "AI", "AR", "BE", "BL", "BS", "FR", "GE", "GL", "GR", "JU", "LU", "NE",
    "NW", "OW", "SG", "SH", "SO", "SZ", "TG", "TI", "UR", "VD", "VS", "ZG", "ZH",
)  # fmt: skip


class GeoAdminError(RuntimeError):
    """A ``find`` request failed or did not answer with a result list."""


def fetch_canton(canton: str) -> list[dict[str, Any]]:
    """Return every rest zone geo.admin holds for one canton.

    ``contains=false`` makes the match exact, so ``GR`` never also matches a
    longer value that contains it.

    Args:
        canton: A two-letter canton code, e.g. ``"GR"``.

    Returns:
        The ``results`` list: features carrying ``featureId``,
        ``properties`` and a WGS84 ``geometry``.

    Raises:
        GeoAdminError: The request failed or the answer has no result list.

    """
    base = conf.text("GEO_ADMIN_API_URL").rstrip("/")
    params = {
        "layer": REST_ZONE_LAYER,
        "searchField": "kanton",
        "searchText": canton,
        "contains": "false",
        "returnGeometry": "true",
        "geometryFormat": "geojson",
        "sr": "4326",
    }
    try:
        response = requests.get(f"{base}/find", params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        results = response.json().get("results")
    except (requests.RequestException, ValueError, AttributeError) as exc:
        raise GeoAdminError(f"cannot read rest zones for {canton}: {exc}") from exc
    if not isinstance(results, list):
        raise GeoAdminError(f"rest zones for {canton}: no result list in the answer")
    logger.info("geo.admin: %s rest zone(s) for %s", len(results), canton)
    return results
