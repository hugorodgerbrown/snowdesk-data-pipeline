"""
regions/converters.py — Custom URL path converters for region identifiers.

Provides ``RegionIdConverter``, a Django path converter that restricts URL
matching to strings that look like real EAWS region IDs (e.g. ``CH-4115``,
``AT-02-01-01``, ``FR-52``).  The tight regex rejects bot-scanner probes
such as ``wp-login``, ``admin``, ``.git``, and ``sqlite.db`` at the URL
routing layer, so those requests never reach the view and never hit the DB.

Register before any URL pattern that uses ``<region_id:region_id>``:

    from django.urls import register_converter
    from regions.converters import RegionIdConverter

    register_converter(RegionIdConverter, "region_id")
"""

import logging

logger = logging.getLogger(__name__)


class RegionIdConverter:
    r"""
    Path converter for EAWS-style region identifiers.

    Regex breakdown:

    * ``[a-zA-Z]{2}``           — two-letter country code (CH, AT, FR, IT, …)
    * ``-``                     — literal dash separator
    * ``(?=[a-zA-Z0-9-]*\d)``   — lookahead: at least one digit must follow the
                                   prefix so purely alphabetic probes such as
                                   ``wp-login`` or ``wp-admin`` are rejected
    * ``[a-zA-Z0-9]+``          — first segment after the prefix
    * ``(-[a-zA-Z0-9]+)*``      — zero or more additional dash-separated segments

    Case-insensitive by design: ``CH-4115`` and ``ch-4115`` both match.
    The ``lowercase_region_id`` decorator on each view canonicalises
    incoming uppercase IDs to lowercase via a 301 redirect.

    ``to_python`` and ``to_url`` are identity functions — the raw string is
    passed to the view unchanged, and reversed back to the URL unchanged.
    """

    regex = r"[a-zA-Z]{2}-(?=[a-zA-Z0-9-]*\d)[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*"

    def to_python(self, value: str) -> str:
        """Return the matched path segment unchanged."""
        return value

    def to_url(self, value: str) -> str:
        """Return the value unchanged for URL reversal."""
        return value
