"""
apps/bulletins/services/grouping.py — Bulletin grouping boundary computation (SNOW-323).

Provides ``compute_bulletin_grouping_boundary``, which dissolves the
boundaries of all micro-regions linked to a bulletin into a single GeoJSON
Polygon/MultiPolygon and persists the result as a ``BulletinGrouping`` row.

This service is called from ``upsert_bulletin`` immediately after
``apply_bulletin_day_ratings``, wrapped in a try/except so geometry errors
never abort bulletin ingest. The grouping is a denormalisation; the
authoritative data is the set of ``RegionBulletin`` rows.

The dissolve delegates to ``apps.regions.fixture_utils.boundary_from_children``
which uses ``shapely.ops.unary_union`` — shapely is a runtime dependency
(promoted from dev-only in SNOW-323 so this path can run in production).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.bulletins.services.day_rating import target_day_for_valid_from
from apps.regions.fixture_utils import boundary_from_children

if TYPE_CHECKING:
    from apps.bulletins.models import Bulletin, BulletinGrouping

logger = logging.getLogger(__name__)


def compute_bulletin_grouping_boundary(
    bulletin: "Bulletin",
) -> "BulletinGrouping | None":
    """
    Dissolve the bulletin's micro-region boundaries into one polygon.

    Queries the micro-regions linked to the bulletin that carry a non-null
    ``boundary``, resolves the ISO-2 country codes from each region's parent
    ``MajorRegion``, then calls ``boundary_from_children`` (Shapely
    ``unary_union``) to merge all polygons into a single GeoJSON geometry.

    The result is persisted via ``update_or_create`` so re-ingest is
    idempotent — existing grouping rows are updated in place.

    When no boundaried regions are linked (e.g. the bulletin is very old
    or its regions have no geometry) the function returns ``None`` and
    ensures no stale row remains by deleting any existing grouping for
    this bulletin (rare, but guards against the case where a re-ingest
    removes all region links).

    Args:
        bulletin: The Bulletin instance to compute a grouping for.
            Must have a tz-aware ``valid_from`` field.

    Returns:
        The created-or-updated ``BulletinGrouping`` instance, or ``None``
        when there are no boundaried regions to dissolve.

    """
    # Import here to avoid a circular import — models imports services
    # only via TYPE_CHECKING guards; services import models at call time.
    from apps.bulletins.models import BulletinGrouping

    regions = list(
        bulletin.regions.filter(boundary__isnull=False).select_related(
            "subregion__major"
        )
    )

    if not regions:
        # No boundaried regions — clean up any stale row and return None.
        BulletinGrouping.objects.filter(bulletin=bulletin).delete()
        logger.debug(
            "compute_bulletin_grouping_boundary: bulletin %s has no boundaried "
            "regions — skipping grouping",
            bulletin.bulletin_id,
        )
        return None

    children = [{"boundary": r.boundary} for r in regions]
    dissolved = boundary_from_children(children)

    countries = sorted({r.subregion.major.country for r in regions})
    target_date = bulletin.target_date or target_day_for_valid_from(bulletin.valid_from)

    grouping, created = BulletinGrouping.objects.update_or_create(
        bulletin=bulletin,
        defaults={
            "target_date": target_date,
            "boundary": dissolved,
            "countries": countries,
        },
    )

    action = "Created" if created else "Updated"
    logger.debug(
        "%s BulletinGrouping for bulletin %s → %s (%d regions, countries=%s)",
        action,
        bulletin.bulletin_id,
        target_date,
        len(regions),
        countries,
    )
    return grouping
