"""
bulletins/services/geoip.py — GeoIP country lookup via MaxMind GeoLite2.

Wraps geoip2.database.Reader behind a module-level cached instance so the
mmdb file is only opened once per process. Thread-safe: construction is
guarded by a threading.Lock so concurrent first-init from multiple request
threads does not open the file twice.

Returns a 2-letter ISO 3166-1 alpha-2 country code (e.g. "CH") for a
public IP, or an empty string on any failure:
  - mmdb file missing or unreadable
  - private / loopback / unroutable address (geoip2 raises AddressNotFoundError)
  - malformed input
  - any other exception from the geoip2 library

Never propagates — callers can always treat "" as "unknown country".
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from django.conf import settings

if TYPE_CHECKING:
    import geoip2.database

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_reader: "geoip2.database.Reader | None" = None
_reader_initialised = False


def _get_reader() -> "geoip2.database.Reader | None":
    """Return the module-level cached GeoLite2-Country reader, opening it on first call.

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
            logger.warning(
                "geoip: GEOIP_PATH is not configured — country lookup disabled"
            )
            _reader_initialised = True
            return None
        try:
            import geoip2.database  # noqa: PLC0415

            _reader = geoip2.database.Reader(str(geoip_path))
            logger.debug("geoip: opened %s", geoip_path)
        except Exception:
            logger.warning(
                "geoip: failed to open database at %s", geoip_path, exc_info=True
            )
        _reader_initialised = True
        return _reader


def country_code_for(ip: str) -> str:
    """Return the ISO 3166-1 alpha-2 country code for an IP address.

    Looks up the address in the MaxMind GeoLite2-Country database. Returns
    an empty string on any failure (private/loopback address, missing
    database, malformed input, library exception) without propagating.

    Args:
        ip: An IPv4 or IPv6 address string.

    Returns:
        A 2-letter uppercase country code (e.g. ``"CH"``), or ``""`` when
        the lookup fails or the address is not in the database.

    """
    reader = _get_reader()
    if reader is None:
        return ""
    try:
        response = reader.country(ip)
        return response.country.iso_code or ""
    except Exception:
        # Includes geoip2.errors.AddressNotFoundError for private / unroutable
        # addresses, ValueError for malformed input, and any other library
        # exception.
        logger.debug("geoip: country lookup failed for %r", ip)
        return ""


def reset_reader_for_testing() -> None:
    """Reset the module-level cached reader and initialisation flag.

    Intended for test use only — allows tests to monkeypatch GEOIP_PATH and
    observe the resulting behaviour without cross-test pollution.
    """
    global _reader, _reader_initialised  # noqa: PLW0603
    with _lock:
        _reader = None
        _reader_initialised = False
