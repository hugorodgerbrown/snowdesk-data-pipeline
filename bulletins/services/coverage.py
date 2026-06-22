"""
bulletins/services/coverage.py — Coverage-status helper for displayed regions.

Provides a single cached lookup:

    covered_region_ids() -> frozenset[str]

A region is "covered" if it has at least one ``RegionDayRating`` row — i.e.
the pipeline has ever produced a danger rating for it. Regions without any
rating row are "uncovered", and the map distinguishes them with a cross-hatch
fill and a provider-specific tooltip note.

Lives in ``bulletins/`` (which owns ``RegionDayRating``) so that regions/ does
not need to import from bulletins/ (which would create a circular import).
"""

from __future__ import annotations

import logging

from django.core.cache import cache

from bulletins.models import RegionDayRating

logger = logging.getLogger(__name__)

# Server-side cache TTL in seconds. One hour is short enough that a previously
# uncovered region whose first rating arrives after a pipeline run will recover
# within an acceptable window, while long enough to avoid any meaningful query
# pressure on a high-traffic map endpoint.
_COVERAGE_TTL = 3600

_CACHE_KEY = "coverage:covered_region_ids"


def covered_region_ids() -> frozenset[str]:
    """Return the set of region_ids that have at least one RegionDayRating row.

    The result is cached for ``_COVERAGE_TTL`` seconds (default 1 hour).
    A cache miss issues exactly one database query; subsequent calls within the
    TTL are served from cache without any query.

    Returns:
        A frozenset of region_id strings (e.g. ``{"CH-4115", "AT-07-01-01"}``).

    """

    def _compute() -> frozenset[str]:
        ids = RegionDayRating.objects.values_list(
            "region__region_id", flat=True
        ).distinct()
        result: frozenset[str] = frozenset(ids)
        logger.debug("coverage: computed %d covered region_ids", len(result))
        return result

    return cache.get_or_set(_CACHE_KEY, _compute, timeout=_COVERAGE_TTL)  # type: ignore[return-value]
