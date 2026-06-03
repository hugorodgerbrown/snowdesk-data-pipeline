"""
bulletins/services/geoip.py — GeoIP city lookup via MaxMind GeoLite2.

Wraps geoip2.database.Reader behind a module-level cached instance so the
mmdb file is only opened once per process. Thread-safe: construction is
guarded by a threading.Lock so concurrent first-init from multiple request
threads does not open the file twice.

Returns a ``GeoLookup`` dataclass for a public IP, or ``None`` on any failure:
  - mmdb file missing or unreadable
  - private / loopback / unroutable address (geoip2 raises AddressNotFoundError)
  - malformed input
  - any other exception from the geoip2 library

Never propagates — callers can always treat ``None`` as "unknown location".
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    import geoip2.database

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_reader: "geoip2.database.Reader | None" = None
_reader_initialised = False


# ---------------------------------------------------------------------------
# GeoLookup dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeoLookup:
    """Resolved geographic information for a single IP address.

    All string fields are empty strings (not ``None``) when the value is
    absent in the database — callers should use ``bool(lookup.country)``
    rather than ``lookup.country is not None`` guards.

    Attributes:
        country: ISO 3166-1 alpha-2 country code (e.g. ``"CH"``), or ``""``.
        subdivision: ISO 3166-2 subdivision code without the country prefix
            (e.g. ``"VS"`` for Valais), or ``""``.
        city: City name in English, or ``""``.
        latitude: WGS-84 latitude, or ``None`` when not in the DB.
        longitude: WGS-84 longitude, or ``None`` when not in the DB.
        accuracy_radius_km: Accuracy radius in kilometres, or ``None``.

    """

    country: str
    subdivision: str
    city: str
    latitude: float | None
    longitude: float | None
    accuracy_radius_km: int | None

    @classmethod
    def empty(cls) -> GeoLookup:
        """Return a GeoLookup with all fields blank/None.

        Useful as a null-safe sentinel when a caller needs a ``GeoLookup``
        instance even when the lookup failed.
        """
        return cls(
            country="",
            subdivision="",
            city="",
            latitude=None,
            longitude=None,
            accuracy_radius_km=None,
        )


# ---------------------------------------------------------------------------
# Reader management
# ---------------------------------------------------------------------------


def _get_reader() -> "geoip2.database.Reader | None":
    """Return the module-level cached GeoLite2-City reader, opening it on first call.

    The lock ensures only one thread opens the file during first
    initialisation. Subsequent calls skip the lock entirely (fast path) once
    ``_reader_initialised`` is set.

    Returns:
        The open Reader, or None when the mmdb path is unset, missing, or
        fails to open.

    """
    global _reader, _reader_initialised  # noqa: PLW0603
    if _reader_initialised:
        return _reader
    with _lock:
        geoip_path = getattr(settings, "GEOIP_PATH", None)
        if not geoip_path:
            logger.warning("geoip: GEOIP_PATH is not configured — geo lookup disabled")
            _reader_initialised = True
            return None
        try:
            import geoip2.database  # noqa: PLC0415

            _reader = geoip2.database.Reader(str(geoip_path))
            logger.debug("geoip: opened %s", geoip_path)
        except Exception:
            logger.exception("geoip: failed to open database at %s", geoip_path)
        _reader_initialised = True
        return _reader


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def geo_lookup(ip: str) -> GeoLookup | None:
    """Return geographic information for an IP address, or None on failure.

    Looks up the address in the MaxMind GeoLite2-City database and returns a
    ``GeoLookup`` with country, subdivision, city, and coordinates.  Returns
    ``None`` on any failure (private/loopback address, missing database,
    malformed input, library exception) without propagating.

    Args:
        ip: An IPv4 or IPv6 address string.

    Returns:
        A populated ``GeoLookup`` when the IP is found in the database, or
        ``None`` when the lookup fails.

    """
    reader = _get_reader()
    if reader is None:
        return None
    try:
        response = reader.city(ip)
        country = response.country.iso_code or ""
        # Subdivision: take the first (most specific) entry's ISO code.
        subdivision = ""
        if response.subdivisions:
            subdivision = response.subdivisions.most_specific.iso_code or ""
        city = response.city.name or ""
        location = response.location
        latitude: float | None = location.latitude
        longitude: float | None = location.longitude
        accuracy_radius_km: int | None = location.accuracy_radius
        return GeoLookup(
            country=country,
            subdivision=subdivision,
            city=city,
            latitude=latitude,
            longitude=longitude,
            accuracy_radius_km=accuracy_radius_km,
        )
    except Exception:
        # Includes geoip2.errors.AddressNotFoundError for private / unroutable
        # addresses, ValueError for malformed input, and any other library
        # exception.
        logger.debug("geoip: city lookup failed for %r", ip)
        return None


def reset_reader_for_testing() -> None:
    """Reset the module-level cached reader and initialisation flag.

    Intended for test use only — allows tests to monkeypatch GEOIP_PATH and
    observe the resulting behaviour without cross-test pollution.
    """
    global _reader, _reader_initialised  # noqa: PLW0603
    with _lock:
        _reader = None
        _reader_initialised = False
