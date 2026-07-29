"""
accounts/services/request_context.py — Request-context helpers for accounts.

Provides ``geo_match_snapshot``, which reads geolocation coordinates from a
``RequestLog`` instance, calls the pure-Python ``classify_match`` service, and
returns a dict ready to splat into ``Subscription.objects.get_or_create``
defaults.

Keeping this logic in a service function (rather than inline in the view) makes
the view readable and the logic unit-testable without HTTP infrastructure.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from regions.services.point_match import classify_match

if TYPE_CHECKING:
    from core.models import RequestLog
    from regions.models import MicroRegion

logger = logging.getLogger(__name__)


def geo_match_snapshot(
    req_log: "RequestLog",
    target_region: "MicroRegion",
) -> dict[str, object]:
    """Return the geo-match fields for a new Subscription row.

    Reads ``req_log.longitude`` and ``req_log.latitude``, runs the
    point-in-polygon classification against ``target_region`` and its
    neighbours, and returns a dict with ``geo_match_kind`` and
    ``geo_matched_region`` suitable for splatting into the ``defaults``
    argument of ``Subscription.objects.get_or_create``.

    When coordinates are absent (e.g. the IP could not be resolved), both
    fields default to their null/unknown sentinel values and no polygon query
    is issued.

    Args:
        req_log: The ``RequestLog`` frozen at subscribe time.
        target_region: The ``MicroRegion`` the subscriber is signing up for.

    Returns:
        Dict with keys ``geo_match_kind`` (str) and ``geo_matched_region``
        (``MicroRegion | None``).

    """
    lon: float | None = req_log.longitude
    lat: float | None = req_log.latitude

    kind, matched = classify_match(lon, lat, target_region)

    # Never log the coordinates themselves — an IP-derived lat/lon is personal
    # data, and the classification result is what makes this line useful for
    # debugging (SNOW-558, CodeQL py/clear-text-logging-sensitive-data). This
    # mirrors the SNOW-311 rule that account logs carry identifiers, not
    # plaintext personal data.
    logger.debug(
        "geo_match_snapshot: region=%s coords=%s → %s (matched=%s)",
        target_region.region_id,
        "present" if lon is not None and lat is not None else "absent",
        kind,
        matched.region_id if matched else None,
    )

    return {
        "geo_match_kind": kind,
        "geo_matched_region": matched,
    }
