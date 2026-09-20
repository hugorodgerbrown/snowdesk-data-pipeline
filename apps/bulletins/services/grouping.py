"""
apps/bulletins/services/grouping.py — Bulletin grouping boundary computation (SNOW-323).

Provides ``compute_bulletin_grouping_boundary``, which dissolves the
boundaries of all micro-regions linked to a bulletin into a single GeoJSON
Polygon/MultiPolygon and persists the result as a ``BulletinGrouping`` row.

A row is written only where the provider actually aggregated — the bulletin
must link at least ``apps.bulletins.models.MIN_GROUPED_REGIONS`` micro-regions
carrying a boundary. Dissolving one polygon returns that polygon, so the
layer would draw an outline directly on top of ``regions-line`` and assert a
grouping that never happened (SNOW-1001). Météo-France is 1:1 across its whole
archive and SLF became 1:1 under SNOW-998, so the layer is now an ALBINA
surface in practice.

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

    Fewer than ``MIN_GROUPED_REGIONS`` boundaried regions is degenerate —
    either none are linked (the bulletin is very old, or its regions have
    no geometry) or exactly one is, in which case the dissolve returns that
    region's own boundary and the drawn outline duplicates ``regions-line``.
    In both cases the function returns ``None`` and ensures no stale row
    remains by deleting any existing grouping for this bulletin, which also
    covers a re-ingest that drops region links from a previously
    multi-region bulletin.

    Args:
        bulletin: The Bulletin instance to compute a grouping for.
            Must have a tz-aware ``valid_from`` field.

    Returns:
        The created-or-updated ``BulletinGrouping`` instance, or ``None``
        when there are too few boundaried regions to dissolve.

    """
    # Import here to avoid a circular import — models imports services
    # only via TYPE_CHECKING guards; services import models at call time.
    from apps.bulletins.models import MIN_GROUPED_REGIONS, BulletinGrouping

    regions = list(
        bulletin.regions.filter(boundary__isnull=False).select_related(
            "subregion__major"
        )
    )

    if len(regions) < MIN_GROUPED_REGIONS:
        # Nothing to dissolve, or nothing the micro-region layer does not
        # already draw — clean up any stale row and return None. The two
        # cases mean different things operationally (missing geometry vs a
        # provider that simply does not aggregate), so they log separately.
        BulletinGrouping.objects.filter(bulletin=bulletin).delete()
        reason = (
            "has no boundaried regions"
            if not regions
            else "covers one boundaried region"
        )
        logger.debug(
            "compute_bulletin_grouping_boundary: bulletin %s %s — skipping grouping",
            bulletin.bulletin_id,
            reason,
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
