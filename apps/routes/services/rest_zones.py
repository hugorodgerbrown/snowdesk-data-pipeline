"""
apps/routes/services/rest_zones.py — FOEN's wildlife rest zones on the map.

The map's "Wildlife rest zones" layer (SNOW-1384) draws this instance's
``RestZone`` rows, which ``ski_network.py`` applies from the service
(SNOW-1263). What the layer needs from a row is decided here, so the
endpoint (``apps/public/api.py`` ``rest_zones_geojson``) only selects rows.

  in_force(period, day)
      Whether a zone's protected period covers one day. FOEN states the
      period as day-and-month text ("21.12. - 30.04."), year-less, so a
      period may run across the new year. Text this cannot read is taken
      to be in force: a zone drawn as protected on a day it is not costs
      a detour, one drawn as open on a day it is protected costs a fine
      and the wildlife it exists for.
  rules_url(zone)
      Where a reader learns the zone's rules: the canton's page, or the
      national site when FOEN gives none.
  zone_feature(zone, day)
      One zone as the GeoJSON Feature the layer draws and its sheet reads.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from apps.routes.models import RestZone
from apps.routes.services.ski_tours import localised

logger = logging.getLogger(__name__)

# The national site every canton's zones are listed on: where a zone with
# no rules page of its own sends a reader.
NATIONAL_RULES_URL = "https://www.wildruhezonen.ch"

# "21.12. - 30.04.": a day and a month at each end, each followed by its
# full stop, an ASCII hyphen between them. Nothing looser: a period this
# cannot read with certainty counts as in force.
_PERIOD_RE = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\.\s*-\s*(\d{1,2})\.(\d{1,2})\.\s*$")

# A leap year, so a period ending on 29.02. reads as a real day.
_LEAP_YEAR = 2000


def _day_of_year(day: int, month: int) -> date | None:
    """Return the day and month in a leap year, or None when there is no such day.

    Args:
        day: The day of the month.
        month: The month.

    Returns:
        The date in ``_LEAP_YEAR``, or None for "31.04." and the like.

    """
    try:
        return date(_LEAP_YEAR, month, day)
    except ValueError:
        return None


def in_force(period: str, day: date) -> bool:
    """Return whether a zone's protected period covers ``day``.

    Both ends are inclusive. A period whose end falls before its start
    runs across the new year ("21.12. - 30.04." covers 1 January). Only
    the strict form "DD.MM. - DD.MM." is read; anything else (FOEN's free
    text, a missing stop, a day that does not exist) counts as in force.

    Args:
        period: FOEN's period text, as stored on ``RestZone.period``.
        day: The day the map is showing.

    Returns:
        True when the day is inside the period, or when the period cannot
        be read.

    """
    match = _PERIOD_RE.match(period or "")
    if match is None:
        return True
    start_day, start_month, end_day, end_month = (int(g) for g in match.groups())
    start = _day_of_year(start_day, start_month)
    end = _day_of_year(end_day, end_month)
    if start is None or end is None:
        return True
    point = (day.month, day.day)
    first = (start.month, start.day)
    last = (end.month, end.day)
    if first <= last:
        return first <= point <= last
    return point >= first or point <= last


def rules_url(zone: RestZone) -> str:
    """Return the page a reader learns this zone's rules on.

    Args:
        zone: The zone.

    FOEN's ``url_kanton`` is outside data, so only an absolute http or
    https URL is passed on: anything else (``javascript:``, ``data:``, a
    bare string) would become a link's href on the sheet.

    Returns:
        The canton's rules page, or ``NATIONAL_RULES_URL`` when FOEN gives
        none, or gives one that is not an http(s) URL.

    """
    url = (zone.url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return NATIONAL_RULES_URL
    if parts.scheme.lower() in ("http", "https") and parts.netloc:
        return url
    return NATIONAL_RULES_URL


def zone_feature(zone: RestZone, day: date) -> dict[str, Any]:
    """Return one zone as the Feature the map layer draws.

    Args:
        zone: The zone.
        day: The day the map is showing, for ``in_force``.

    Returns:
        A GeoJSON Feature, its id the service's uuid (repeated as the
        ``uuid`` property, which the client promotes), with the name, whether
        it is binding, the restriction in the request's language (German
        when FOEN has none in it), the period as published, whether it is
        in force on ``day``, the canton and the rules page.

    """
    restriction = zone.restriction or {}
    return {
        "type": "Feature",
        "id": str(zone.source_uuid),
        "geometry": zone.geometry,
        "properties": {
            # Again as a property: MapLibre keeps a Feature's top-level id
            # only when it is a number, so the client promotes this one.
            "uuid": str(zone.source_uuid),
            "name": zone.name,
            "binding": zone.binding,
            "restriction": localised(restriction, restriction.get("de", "")),
            "period": zone.period,
            "in_force": in_force(zone.period, day),
            "canton": zone.canton,
            "url": rules_url(zone),
        },
    }
