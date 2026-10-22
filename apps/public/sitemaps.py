"""
apps/public/sitemaps.py — XML sitemaps for the public bulletin site.

Four sections, published as three sitemap files behind one index
(``SITEMAP_FILES`` at the foot of this module, SNOW-1178):

    /sitemap.xml          the index — lists the three files below
    /sitemap-main.xml     StaticViewSitemap      — the homepage, the map, the
                                                   guides, legal
                          LocationWeatherSitemap — every named public
                                                   location's weather page
                                                   (SNOW-799)
    /sitemap-regions.xml  BulletinSitemap        — every region that has ever
                                                   had a bulletin (SNOW-1121)
    /sitemap-resorts.xml  ResortSitemap          — every resort's detail page

Until SNOW-676 the sitemap was the bulletin section alone, which had two
consequences. The resort pages SNOW-504 built specifically as indexable,
shareable URLs were invisible to search, as were the homepage and every
reference page. And out of season — roughly May to November — the whole
sitemap was *empty*, because no bulletin is valid today, so the site
advertised nothing at all during the months when slow-moving pages have
time to be indexed. SNOW-676 fixed the file; the bulletin *section* stayed
empty out of season until SNOW-1121, because it still listed only the
regions with a bulletin valid today.

Wire-up in config/urls.py:

    path("sitemap.xml", serve_sitemap_index, name="sitemap")
    path("sitemap-<slug:name>.xml", serve_sitemap, name="sitemap_file")

Every section inherits from ``OriginSitemap``, which takes the scheme and
host of each ``<loc>`` from ``settings.SITE_BASE_URL`` — the same origin
robots.txt, the feeds and the OG tags use. Django's default reads them from
the ``django_site`` row instead (``SITE_ID = 1``), and that row is the
``example.com`` the sites migration creates: until SNOW-1175 every URL the
live sitemap advertised was on a domain Snowdesk does not own.
django.contrib.sites stays installed only because the sitemap view imports
it.

Serving (SNOW-1178). The files are ``apps.public.views.serve_sitemap``, not
Django's ``sitemap`` view: it renders a file's sections with
``render_sitemap`` below and keeps the bytes in the cache for that file's
``cache_seconds``, so a crawler's repeat fetch never reaches the database.
Each file has its own lifetime because each changes at its own pace — the
regions with every bulletin, the resorts with a hand edit. Rendering outside
the request also keeps the template context processors — which read
``request.user`` and so add ``Vary: Cookie`` — off the response, which is
what lets it carry ``Cache-Control: public``.

SNOW-1121 made the bulletin section aggregate over every bulletin ever
issued (59,130 region links at the time). Before SNOW-1178 that aggregate
also selected — and so grouped and de-duplicated by — both regions'
boundary polygons, and the whole sitemap took ~3 s. Keep each section's
``items()`` to the columns its URL and ``lastmod`` read.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.db.models import Max
from django.template.loader import render_to_string
from django.urls import reverse

from apps.locations.models import Location
from apps.regions.models import MicroRegion, Resort

logger = logging.getLogger(__name__)


class OriginSitemap(Sitemap):
    """
    A ``Sitemap`` whose absolute URLs use ``settings.SITE_BASE_URL`` (SNOW-1175).

    Django's ``Sitemap`` takes the host from ``get_current_site(request)`` —
    the ``django_site`` row, ``example.com`` unless someone edits it by hand
    in every environment — and the scheme from the request, which is
    whatever the proxy hop spoke. ``SITE_BASE_URL`` is already the one
    per-environment origin setting, so both come from it instead.
    """

    def get_protocol(self, protocol: str | None = None) -> str:
        """
        Return the scheme of ``SITE_BASE_URL``, ignoring the request's.

        Args:
            protocol: The request's scheme, as Django's sitemap view passes it
                (unused).

        Returns:
            ``"https"`` or ``"http"``.

        """
        return urlsplit(str(settings.SITE_BASE_URL)).scheme

    def get_domain(self, site: Any = None) -> str:
        """
        Return the host (and any port) of ``SITE_BASE_URL``, ignoring the Site row.

        Args:
            site: The ``Site`` Django's sitemap view resolved (unused).

        Returns:
            The netloc, e.g. ``"snowdesk.info"``.

        """
        return urlsplit(str(settings.SITE_BASE_URL)).netloc


class BulletinSitemap(OriginSitemap):
    """
    Sitemap listing every region that has ever had a bulletin (SNOW-1121).

    ``items()`` returns each region linked to at least one Bulletin, whatever
    its date. The evergreen URL renders all year — out of season, with no
    bulletin covering today, it is the page's empty state — and it is the
    URL that will carry the bulletin once the season opens. Until SNOW-1121
    the section listed only the regions with a bulletin valid *today*, which
    left it empty from May to November: exactly the weeks before the season
    when a crawler has time to index the site's richest unique pages. A
    region no provider covers has no bulletin and stays out — its page would
    never have anything to show.

    The ``lastmod`` date is the most recent ``updated_at`` among the region's
    bulletins, retrieved via an annotation on the queryset to avoid N+1.

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
        Return every MicroRegion with at least one bulletin, of any date.

        Re-evaluated on every render — do not cache at module level.
        Uses ``Max("bulletins__updated_at")`` annotation so ``lastmod()``
        can retrieve the value without an extra query per region; the
        aggregate groups by region, so each appears once.

        ``only()`` the two columns ``get_absolute_url`` reads (SNOW-1178).
        The aggregate joins every region-bulletin link, and Postgres groups
        by every selected column: selecting the whole row — and, before
        SNOW-1178, the subregion's through ``select_related`` — made it
        group and de-duplicate 59,130 rows by two boundary polygons, 1.8 s
        of the sitemap's 3 s. With the pk alone in ``GROUP BY`` no
        ``distinct()`` is needed either.

        Evaluated to a list so the sitemap's paginator takes ``len()`` of it
        rather than running the same 59,130-row join a second time as a
        ``COUNT``.

        Returns:
            A list of ``MicroRegion`` instances annotated with
            ``latest_bulletin_updated_at``.

        """
        return list(
            MicroRegion.objects.filter(bulletins__isnull=False)
            .only("region_id", "name")
            .annotate(latest_bulletin_updated_at=Max("bulletins__updated_at"))
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


class ResortSitemap(OriginSitemap):
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
        "resort without a region" case cannot arise. ``only()`` the slug
        and ``updated_at`` — all ``location`` and ``lastmod`` read. Until
        SNOW-1178 a ``select_related("region")`` also fetched each resort's
        region, boundary polygon included, for nothing.

        Returns:
            A queryset of ``Resort`` instances.

        """
        return Resort.objects.only("slug", "updated_at").order_by("id")

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


class LocationWeatherSitemap(OriginSitemap):
    """
    Sitemap listing the weather page of every *named* public location (SNOW-799).

    The weather page is document two of the two-document IA
    (``docs/decisions/two-documents-and-a-map.md``) and had full sharing
    metadata but no sitemap entry. This is that entry.

    **Named locations only** — ``Location.objects.public().named()``. The
    public estate is ~540 rows of which ~461 are anonymous region
    centroids; their pages fall back to the region's name for a heading,
    so listing them would publish 461 pages titled after regions, each
    competing with that region's own bulletin page for the same query.
    Villages, mid-stations and peaks have a name of their own and are the
    pages worth an index entry.

    ``public()`` is the privacy ceiling — never ``active()``, which reaches
    the locations a ``Favourite`` created. A private pin must not appear
    in a sitemap any more than in ``weather.geojson``.

    Only the evergreen, undated URL is listed — ``?date=`` picks a row
    inside the page, not a different page
    (``docs/decisions/weather-day-picker-is-a-selector-not-navigation.md``),
    the same convention as ``BulletinSitemap``'s evergreen entries.

    A location with no ``short_id`` is excluded (SNOW-810). It has no
    address — ``get_absolute_url`` answers "" for it — and Django's
    ``Sitemap`` interpolates that answer into ``protocol://domain`` with no
    check, so listing one would publish a bare-domain entry as though it
    were a page. Before this exclusion the empty answer did not arise:
    ``get_absolute_url`` raised ``NoReverseMatch`` instead, and the whole
    of ``/sitemap.xml`` was a 500 in any environment between the SNOW-797
    migration and its backfill. Filtering here rather than trusting the
    backfill keeps that window a smaller sitemap instead of no sitemap.
    """

    changefreq = "daily"
    priority = 0.6

    def items(self) -> Any:
        """
        Return every named public location, ordered by id for a stable sitemap.

        Annotated with the newest ``fetched_at`` across the location's
        weather rows so ``lastmod()`` reads it without a query per row.

        Rows with no ``short_id`` are excluded — see the class docstring.

        Returns:
            A queryset of ``Location`` instances annotated with
            ``latest_weather_fetched_at``.

        """
        return (
            Location.objects.public()
            .named()
            .exclude(short_id__isnull=True)
            .annotate(latest_weather_fetched_at=Max("weather__fetched_at"))
            .distinct()
            .order_by("id")
        )

    def location(self, item: Location) -> str:
        """
        Return the location's undated weather-page path.

        Args:
            item: A ``Location`` instance from ``items()``.

        Returns:
            The path string (e.g. ``"/weather/Ab3dE_fGh1J/"``).

        """
        return item.get_absolute_url()

    def lastmod(self, item: Location) -> Any:
        """
        Return when the page last changed: the newest weather fetch, else the row.

        A weather page changes when its forecast is re-fetched, four times a
        day — that is the honest modification signal, and it is what the
        ``items()`` annotation carries. A location with no weather yet falls
        back to its own ``updated_at``.

        Args:
            item: An annotated ``Location`` instance.

        Returns:
            A ``datetime`` (UTC-aware).

        """
        return getattr(item, "latest_weather_fetched_at", None) or item.updated_at


class StaticViewSitemap(OriginSitemap):
    """
    Sitemap listing the public pages that aren't generated from data (SNOW-676).

    The homepage, the guides, the comparison page, and the legal pages.
    Priority varies
    by route rather than being flat — a sitemap that claims the data-licence
    page matters as much as the homepage is telling the crawler nothing.

    Excluded on purpose, and why:

    * ``public:observations`` — a permanent redirect to the map with the
      reports sheet open since SNOW-804 (before that, a page an anonymous
      visitor met as a sign-in wall). A redirect is not a destination.
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
      would contradict that file. (``/favourites/<uuid>/`` has been a
      redirect to the pin's weather page since SNOW-800; the weather page
      itself is listed in the ``locations`` section when it is public.)
    * ``/trips/…`` — per-user or token-bearing, and kept out of search by
      ``noindex`` rather than by a robots.txt ``Disallow`` (SNOW-821).
      ``Disallow: /trips/`` would prefix-match ``/trips/s/<token>/`` and
      block the very unfurlers that page exists to serve — a share link has
      to render a card in a message. ``noindex`` is the correct instrument
      for an unguessable URL, and the list page is behind auth anyway.
    """

    # Route name → (changefreq, priority).
    ROUTES: dict[str, tuple[str, float]] = {
        "public:home": ("weekly", 1.0),
        "public:map": ("daily", 0.9),
        # SNOW-1150: the resorts index — today's board changes daily.
        "public:resorts": ("daily", 0.8),
        "public:how_to_read_bulletin": ("monthly", 0.5),
        "public:help": ("monthly", 0.5),
        "public:compare": ("monthly", 0.5),
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


class SitemapFile(NamedTuple):
    """One sitemap file the index lists: its sections and its cache lifetime."""

    sections: tuple[type[Sitemap], ...]
    cache_seconds: int


# The published files, by the name in their URL (``/sitemap-<name>.xml``),
# in index order. Adding a section is a one-line change here.
SITEMAP_FILES: dict[str, SitemapFile] = {
    # Six hours: the reference pages barely change, and a weather page's
    # lastmod moves with each of the four daily forecast fetches.
    "main": SitemapFile((StaticViewSitemap, LocationWeatherSitemap), 6 * 60 * 60),
    # An hour: a region's lastmod moves whenever a bulletin lands, and the
    # providers publish more than once a day in season.
    "regions": SitemapFile((BulletinSitemap,), 60 * 60),
    # A day: a resort's lastmod is a hand edit to its curated row.
    "resorts": SitemapFile((ResortSitemap,), 24 * 60 * 60),
}

# The index lists the files above and nothing else, so it only changes when
# that dict does — on a deploy. A day at the edge is harmless.
SITEMAP_INDEX_CACHE_SECONDS = 24 * 60 * 60


def sitemap_cache_key(name: str) -> str:
    """
    Return the cache key for one rendered sitemap file.

    Keyed on ``SITE_BASE_URL`` because every ``<loc>`` is built from it: a
    body rendered for one origin must not be served for another.

    Args:
        name: A key of ``SITEMAP_FILES``.

    Returns:
        The cache key.

    """
    return f"public:sitemap-{name}.xml:{settings.SITE_BASE_URL}"


def render_sitemap(name: str) -> bytes:
    """
    Render one sitemap file's sections into a single ``<urlset>`` document.

    What Django's ``sitemap`` view does for a single page, without the
    request: the sections take their origin from ``SITE_BASE_URL`` (see
    ``OriginSitemap``), so the request has nothing to contribute, and
    rendering without it keeps ``request.user`` — and ``Vary: Cookie`` —
    out of the response. A single page holds 50,000 URLs, a hundred times
    the current estate.

    Args:
        name: A key of ``SITEMAP_FILES``.

    Returns:
        The XML document as UTF-8 bytes.

    """
    urls: list[dict[str, Any]] = []
    for section in SITEMAP_FILES[name].sections:
        urls.extend(section().get_urls())
    return render_to_string("sitemap.xml", {"urlset": urls}).encode()


def render_sitemap_index() -> bytes:
    """
    Render the ``<sitemapindex>`` listing every file in ``SITEMAP_FILES``.

    No ``<lastmod>`` per file: working one out means running that file's
    queries, which is the cost the index exists to keep off a crawler's
    first request. A crawler fetches each file and reads the per-URL
    ``lastmod`` there.

    Returns:
        The XML document as UTF-8 bytes.

    """
    base = str(settings.SITE_BASE_URL).rstrip("/")
    sitemaps = [
        {"location": base + reverse("sitemap_file", kwargs={"name": name})}
        for name in SITEMAP_FILES
    ]
    return render_to_string("sitemap_index.xml", {"sitemaps": sitemaps}).encode()
