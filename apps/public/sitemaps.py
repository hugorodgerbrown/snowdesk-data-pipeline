"""
apps/public/sitemaps.py — XML sitemaps for the public bulletin site.

Three sections, registered together in config/urls.py:

    bulletins  BulletinSitemap     — regions with a bulletin for today
    resorts    ResortSitemap       — every resort's detail page
    static     StaticViewSitemap   — the homepage, the guides, the legal pages

Until SNOW-676 the sitemap was the bulletin section alone, which had two
consequences. The resort pages SNOW-504 built specifically as indexable,
shareable URLs were invisible to search, as were the homepage and every
reference page. And out of season — roughly May to November — the whole
sitemap was *empty*, because no bulletin is valid today, so the site
advertised nothing at all during the months when slow-moving pages have
time to be indexed.

Wire-up in config/urls.py:

    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap")

The sites framework (django.contrib.sites + SITE_ID = 1) is required for
Django's sitemap view to build absolute URLs.
"""

from __future__ import annotations

import logging
from typing import Any
from zoneinfo import ZoneInfo

from django.contrib.sitemaps import Sitemap
from django.db.models import Max
from django.urls import reverse
from django.utils import timezone

from apps.regions.models import MicroRegion, Resort

logger = logging.getLogger(__name__)

_ZURICH_TZ = ZoneInfo("Europe/Zurich")


class BulletinSitemap(Sitemap):
    """
    Sitemap listing every region with a bulletin for today (Europe/Zurich).

    ``items()`` returns only regions that have at least one Bulletin whose
    ``valid_to`` date (in UTC, which covers the full Zurich day) equals today.
    The ``lastmod`` date is the most recent ``updated_at`` among those bulletins
    for the region, retrieved via an annotation on the queryset to avoid N+1.

    URL form: form-2 evergreen URL ``/<region_id>/<slug>/`` (SNOW-395).
    LLMs and search engines cite whatever URL the sitemap advertises; a
    dated URL is stale by tomorrow, so the evergreen form is what stays
    useful for "current conditions" queries. Each evergreen page carries
    a ``<link rel="canonical">`` pointing back at that day's form-3 URL,
    so the historical anchor is still discoverable via the page itself.
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
        Return the evergreen canonical path for a region's bulletin (SNOW-395).

        Uses the form-2 evergreen URL ``/<region_id>/<slug>/`` — the URL
        that always renders today's bulletin. The bulletin page's own
        ``<link rel="canonical">`` points at the form-3 dated URL so
        search engines still see the historical anchor for the day.

        Args:
            item: A ``MicroRegion`` instance from ``items()``.

        Returns:
            The path string (e.g. ``"/ch-4115/martigny-verbier/"``).

        """
        return item.get_absolute_url()

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


class ResortSitemap(Sitemap):
    """
    Sitemap listing every resort's detail page (SNOW-676).

    SNOW-504 gave each ``Resort`` its own indexable, shareable URL at
    ``/resorts/<id>/<slug>/`` and then never told a crawler they existed.
    This is that half.

    Both ``Resort.Kind`` values are listed. ``resort_detail`` does not
    filter by kind, so lift-less touring terrain has a real page exactly as
    a lift-served resort does, and omitting it would hide the pages least
    likely to be found any other way.

    ``changefreq``/``priority`` are deliberately below the bulletin
    section's: a resort page is stable reference content, and should not
    compete for crawl budget with the page that changes every morning.
    """

    changefreq = "weekly"
    priority = 0.6

    def items(self) -> Any:
        """
        Return every resort, ordered by id for a stable sitemap.

        No ``region__isnull=False`` filter: ``Resort.region`` is a
        non-nullable FK, so the filter would be dead code and the
        "resort without a region" case cannot arise. ``select_related``
        because ``get_absolute_url`` is cheap but the ordering is not free.

        Returns:
            A queryset of ``Resort`` instances.

        """
        return Resort.objects.select_related("region").order_by("id")

    def location(self, item: Resort) -> str:
        """
        Return the canonical resort-page path.

        Args:
            item: A ``Resort`` instance from ``items()``.

        Returns:
            The path string (e.g. ``"/resorts/adelboden/"``).

        """
        return item.get_absolute_url()

    def lastmod(self, item: Resort) -> Any:
        """
        Return the resort row's last-modification datetime.

        Reads the already-loaded ``updated_at`` field — no extra query.
        This tracks edits to the curated sheet rather than to the rendered
        page, which is the honest signal available: the page also shows
        live weather and today's danger, neither of which has a
        modification date of its own.

        Args:
            item: A ``Resort`` instance from ``items()``.

        Returns:
            A ``datetime`` (UTC-aware).

        """
        return item.updated_at


class StaticViewSitemap(Sitemap):
    """
    Sitemap listing the public pages that aren't generated from data (SNOW-676).

    The homepage, the two guides, and the four legal pages. Priority varies
    by route rather than being flat — a sitemap that claims the data-licence
    page matters as much as the homepage is telling the crawler nothing.

    Excluded on purpose, and why:

    * ``public:observations`` — a public URL, but an anonymous visitor gets
      a sign-in call to action rather than the stream, so indexing it
      advertises a sign-in wall as content. Worth revisiting under SNOW-669,
      which is about giving that page a way in.
    * ``public:examples_random`` / ``examples_category`` — the content is a
      *random* bulletin per request, so listing them invites duplicate-content
      collisions with the real bulletin pages they sample.
    * ``/map/``, ``/random/`` and ``/terms/`` — permanent redirects, not
      destinations. ``/terms/`` became one in SNOW-770, when its page was
      merged into ``/terms-of-service/``; listing it would offer a crawler
      a redirect to a URL already listed two lines below it.
    * ``_components/``, ``_push-demo/``, ``_sw-version/`` — staff-only.
    * ``/account/…`` and ``/favourites/<uuid>/`` — per-user, and ``Disallow``ed
      in robots.txt (``apps.public.views.serve_robots``). Listing them here
      would contradict that file.
    """

    # Route name → (changefreq, priority).
    ROUTES: dict[str, tuple[str, float]] = {
        "public:home": ("daily", 1.0),
        "public:how_to_read_bulletin": ("monthly", 0.5),
        "public:help": ("monthly", 0.5),
        "public:privacy": ("yearly", 0.3),
        "public:terms_of_service": ("yearly", 0.3),
        "public:colophon": ("yearly", 0.3),
    }

    def items(self) -> list[str]:
        """
        Return the route names to list, in declaration order.

        Returns:
            A list of URL-pattern names.

        """
        return list(self.ROUTES)

    def location(self, item: str) -> str:
        """
        Resolve one route name to its path.

        ``reverse`` rather than a literal so a renamed or moved route fails
        loudly here instead of shipping a 404 into the sitemap.

        Args:
            item: A URL-pattern name from ``items()``.

        Returns:
            The path string (e.g. ``"/how-to-read-a-bulletin/"``).

        """
        return reverse(item)

    def changefreq(self, item: str) -> str:
        """
        Return the change frequency for one route.

        Args:
            item: A URL-pattern name from ``items()``.

        Returns:
            One of the sitemap-protocol changefreq values.

        """
        return self.ROUTES[item][0]

    def priority(self, item: str) -> float:
        """
        Return the crawl priority for one route.

        Args:
            item: A URL-pattern name from ``items()``.

        Returns:
            A float between 0.0 and 1.0.

        """
        return self.ROUTES[item][1]


# The full set, registered as one dict so config/urls.py has nothing to
# decide and adding a section is a one-line change here.
SITEMAPS: dict[str, type[Sitemap]] = {
    "bulletins": BulletinSitemap,
    "resorts": ResortSitemap,
    "static": StaticViewSitemap,
}
