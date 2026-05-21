"""
public/sitemaps.py — XML sitemaps for the public bulletin site.

Provides BulletinSitemap, which lists every MicroRegion that has a bulletin
covering the current day (Europe/Zurich local date).  The sitemap is
re-evaluated on every request so it reflects the day's actual published
bulletins without a pre-warm step.

Wire-up in config/urls.py:

    path("sitemap.xml", sitemap, {"sitemaps": {"bulletins": BulletinSitemap}},
         name="sitemap")

The sites framework (django.contrib.sites + SITE_ID = 1) is required for
Django's sitemap view to build absolute URLs.
"""

from __future__ import annotations

import logging
from typing import Any
from zoneinfo import ZoneInfo

from django.contrib.sitemaps import Sitemap
from django.db.models import Max
from django.utils import timezone

from regions.models import MicroRegion

logger = logging.getLogger(__name__)

_ZURICH_TZ = ZoneInfo("Europe/Zurich")


class BulletinSitemap(Sitemap):
    """
    Sitemap listing every region with a bulletin for today (Europe/Zurich).

    ``items()`` returns only regions that have at least one Bulletin whose
    ``valid_to`` date (in UTC, which covers the full Zurich day) equals today.
    The ``lastmod`` date is the most recent ``updated_at`` among those bulletins
    for the region, retrieved via an annotation on the queryset to avoid N+1.

    URL form: form-3 dated URL ``/<region_id>/<slug>/<YYYY-MM-DD>/`` for all
    regions so the entry is a stable, historical record once the day passes.
    """

    changefreq = "daily"
    priority = 0.8

    def items(self) -> Any:
        """
        Return the set of MicroRegions with a bulletin valid for today.

        Re-evaluated on every request — do not cache at module level.
        Uses ``Max("bulletins__updated_at")`` annotation so ``lastmod()``
        can retrieve the value without an extra query per region.

        Returns:
            A queryset of ``MicroRegion`` instances annotated with
            ``latest_bulletin_updated_at``.

        """
        today = timezone.localdate(timezone=_ZURICH_TZ)
        return (
            MicroRegion.objects.filter(bulletins__valid_to__date=today)
            .annotate(latest_bulletin_updated_at=Max("bulletins__updated_at"))
            .distinct()
            .select_related("subregion")
            .order_by("region_id")
        )

    def location(self, item: MicroRegion) -> str:
        """
        Return the canonical path for a region's bulletin.

        Uses the form-3 dated URL ``/<region_id>/<slug>/<YYYY-MM-DD>/``
        so the sitemap entry points to today's specific bulletin, not the
        evergreen "today" URL.

        Args:
            item: A ``MicroRegion`` instance from ``items()``.

        Returns:
            The path string (e.g. ``"/ch-4115/martigny-verbier/2026-05-20/"``).

        """
        today = timezone.localdate(timezone=_ZURICH_TZ)
        return item.get_absolute_url(target_date=today)

    def lastmod(self, item: MicroRegion) -> Any:
        """
        Return the last-modification datetime for the region's bulletin.

        Uses the ``latest_bulletin_updated_at`` annotation added in
        ``items()`` — no extra query per region.

        Args:
            item: An annotated ``MicroRegion`` instance.

        Returns:
            A ``datetime`` (UTC-aware), or ``None`` when the annotation is
            absent.

        """
        return getattr(item, "latest_bulletin_updated_at", None)
