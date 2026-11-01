"""
apps/public/feeds.py — RSS feeds for the public bulletin site.

Provides ``CountryBulletinFeed``, a per-country RSS feed of the most
recent bulletin updates for that country. Registered at
``/<country>/feed.rss`` (see ``apps/public/urls.py``) with ``country`` one
of the four covered ISO-3166 alpha-2 codes: ``ch``, ``at``, ``it``,
``fr`` (SNOW-396).

Each feed entry represents a bulletin as it applies to one micro-region
— the ``RegionBulletin`` through row — with:

* ``link`` pointing at the region's form-2 evergreen URL
  ``/<region_id>/<slug>/`` so the citation stays live day-to-day; and
* ``pubDate`` set to the bulletin's ``issued_at`` so feed readers order
  entries by the actual publication time of the upstream bulletin.

Feed items are capped at ``_MAX_ITEMS`` (20) — a low cap keeps the feed
scannable on days when a national bulletin (SLF) covers 149 CH
micro-regions at once. Revisit if consumers report the cap as too
short.

The feed is discoverable via three paths:

1. ``robots.txt`` allows the RSS endpoints under ``Allow: /``.
2. ``/llms.txt`` lists each country's feed under ``## Data``.
3. The map page carries ``<link rel="alternate" type="application/rss+xml">``
   for each active country so browsers and feed readers can pick them up
   from the page head.

Each entry's description is a plain-text summary drawn from the bulletin's
render model ``prose`` block (SNOW-1344) — the first non-empty of the
avalanche-activity highlights, the tendency lead, the snowpack structure
and the weather forecast, tags stripped and capped at ``_SUMMARY_MAX_CHARS``.
"""

from __future__ import annotations

import html
import re
from typing import Any

from django.conf import settings
from django.contrib.syndication.views import Feed
from django.http import Http404
from django.urls import reverse
from django.utils.feedgenerator import Atom1Feed
from django.utils.html import strip_tags

from apps.bulletins.models import RegionBulletin

# Covered ISO-3166 alpha-2 country codes. Kept as a lowercase set for
# case-insensitive URL matching without lowercasing at every call site.
_SUPPORTED_COUNTRIES: frozenset[str] = frozenset({"ch", "at", "it", "fr"})

# Human-readable country names. Duplicated from ``apps.public.api.COUNTRY_NAMES``
# rather than imported to keep this module's import graph independent of
# ``apps.public.api`` (which imports from ``apps.public.views``, creating a chain
# that has already caused one circular import — see
# ``serve_llms_full_txt`` in ``apps/public/views.py``).
_COUNTRY_NAMES: dict[str, str] = {
    "ch": "Switzerland",
    "at": "Austria",
    "it": "Italy",
    "fr": "France",
}

# Cap on the number of RegionBulletin items in each feed. High-volume
# days (SLF issues one national bulletin covering 149 CH micro-regions
# at once) would otherwise fill the feed with a single burst.
_MAX_ITEMS = 20

# Cap on an entry description's length, in characters. A summary longer
# than this is cut at the last word boundary inside the cap and ends "…".
_SUMMARY_MAX_CHARS = 280


def _prose_candidates(prose: dict[str, Any]) -> list[Any]:
    """
    Return the render model prose fields in summary-precedence order.

    The avalanche-activity highlights lead because they are the
    provider's own headline for the day; the tendency lead, the snowpack
    structure and the weather forecast follow, most to least specific.

    Args:
        prose: The render model's ``prose`` dict.

    Returns:
        The candidate values, in precedence order, unfiltered.

    """
    activity = prose.get("avalanche_activity")
    highlights = activity.get("highlights") if isinstance(activity, dict) else None
    return [
        highlights,
        prose.get("tendency_lead"),
        prose.get("snowpack_structure"),
        prose.get("weather_forecast"),
    ]


_HEADING = re.compile(r"<h[1-6][^>]*>.*?</h[1-6]>", re.IGNORECASE | re.DOTALL)


def _plain_summary(text: str) -> str:
    """
    Strip HTML from *text*, collapse whitespace and cap it for a feed entry.

    Args:
        text: Provider prose, possibly carrying HTML markup and entities.

    Returns:
        Plain text of at most ``_SUMMARY_MAX_CHARS`` characters; a longer
        text is cut at the last word boundary inside the cap and ends "…".

    """
    # A provider heading ("<h1>Snowpack</h1>") names the section, not the
    # bulletin, so it is dropped rather than run into the first sentence.
    text = _HEADING.sub(" ", text)
    plain = " ".join(html.unescape(strip_tags(text)).split())
    if len(plain) <= _SUMMARY_MAX_CHARS:
        return plain
    # Leave room for the ellipsis, then back up to the last space.
    cut = plain[: _SUMMARY_MAX_CHARS - 1]
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-") + "…"


def bulletin_summary(render_model: dict[str, Any] | None) -> str:
    """
    Return a plain-text summary of a bulletin from its render model.

    The first non-empty of the prose fields named in
    :func:`_prose_candidates` wins. A render model not yet built, or one
    whose prose is empty, yields ``""``.

    Args:
        render_model: The bulletin's stored render model, or None.

    Returns:
        The plain-text summary, or an empty string.

    """
    prose = (render_model or {}).get("prose")
    if not isinstance(prose, dict):
        return ""
    for candidate in _prose_candidates(prose):
        if isinstance(candidate, str):
            summary = _plain_summary(candidate)
            if summary:
                return summary
    return ""


class CountryBulletinFeed(Feed):
    """
    Per-country RSS feed of the most recent bulletin updates.

    Django's ``django.contrib.syndication.views.Feed`` builds the feed
    from method overrides — see the Django docs for the full protocol.
    The country code is captured in the URL, validated against
    ``_SUPPORTED_COUNTRIES`` in :meth:`get_object`, then threaded
    through every other override as the ``obj`` argument.
    """

    feed_type = Atom1Feed
    language = "en"

    def get_object(self, request: Any, country: str) -> str:
        """
        Validate the country URL kwarg and return the canonical lowercase code.

        Args:
            request: The incoming HTTP request (unused — the feed is
                origin-keyed via ``SITE_BASE_URL``, not per-request).
            country: The URL-captured country segment
                (case-insensitive; validated here).

        Returns:
            The canonical lowercase country code.

        Raises:
            Http404: If ``country`` is not one of the covered ISO-3166
                alpha-2 codes.

        """
        canonical = country.lower()
        if canonical not in _SUPPORTED_COUNTRIES:
            raise Http404(f"Unknown country code: {country!r}")
        return canonical

    def title(self, obj: str) -> str:
        """Return the feed title — country-specific."""
        return f"Snowdesk — {_COUNTRY_NAMES[obj]} avalanche bulletins"

    def link(self, obj: str) -> str:
        """Return the feed's canonical HTML page — the interactive map.

        ``/map/`` since SNOW-1047; ``/`` is the marketing homepage.
        """
        return f"{settings.SITE_BASE_URL.rstrip('/')}{reverse('public:map')}"

    def description(self, obj: str) -> str:
        """Return the feed description — country-specific."""
        return (
            f"Latest avalanche bulletins for {_COUNTRY_NAMES[obj]} "
            "micro-regions, sourced from SLF, ALBINA / EUREGIO "
            "avalanche.report, and Météo-France and normalised to CAAML v6."
        )

    def items(self, obj: str) -> Any:
        """
        Return the most recent ``_MAX_ITEMS`` RegionBulletins for the country.

        Ordering: most recent bulletin ``issued_at`` first; ties break on
        ``region_id`` for a stable order. ``select_related`` joins the
        ``bulletin`` and the region in a single query so ``item_link``,
        ``item_title``, and ``item_pubdate`` do not fan out into N+1
        lookups. ``only`` loads just the columns the feed reads (SNOW-1344).

        Args:
            obj: The canonical lowercase country code from
                :meth:`get_object`.

        Returns:
            A queryset of ``RegionBulletin`` instances, capped at
            ``_MAX_ITEMS``.

        """
        prefix = f"{obj.upper()}-"
        return (
            RegionBulletin.objects.filter(region__region_id__istartswith=prefix)
            .select_related("bulletin", "region")
            .only(
                "region_name_at_time",
                "bulletin__bulletin_id",
                "bulletin__issued_at",
                "bulletin__render_model",
                "region__region_id",
                "region__name",
            )
            .order_by("-bulletin__issued_at", "region__region_id")[:_MAX_ITEMS]
        )

    def item_title(self, item: RegionBulletin) -> str:
        """Return the entry title — the region name as at bulletin issue."""
        return item.region_name_at_time or item.region.name

    def feed_url(self, obj: str) -> str:
        """
        Return the feed's own absolute URL, for its ``rel="self"`` link.

        Absolute so Django's ``add_domain`` leaves it alone: given a path,
        it prefixes the ``django_site`` row's domain, which is
        ``example.com`` (SNOW-1175).
        """
        base = settings.SITE_BASE_URL.rstrip("/")
        return f"{base}{reverse('public:country_bulletin_feed', args=[obj])}"

    def item_link(self, item: RegionBulletin) -> str:
        """
        Return the entry link — the region's absolute evergreen bulletin URL.

        Absolute for the same reason as :meth:`feed_url` (SNOW-1175).
        """
        return f"{settings.SITE_BASE_URL.rstrip('/')}{item.region.get_absolute_url()}"

    def item_description(self, item: RegionBulletin) -> str:
        """
        Return the entry description — the bulletin's prose summary.

        A plain-text summary from the render model's ``prose`` block (see
        :func:`bulletin_summary`); an empty string when the render model
        is not yet built or its prose is empty.
        """
        return bulletin_summary(item.bulletin.render_model)

    def item_pubdate(self, item: RegionBulletin) -> Any:
        """Return the entry pubDate — the bulletin's ``issued_at`` timestamp."""
        return item.bulletin.issued_at

    def item_guid(self, item: RegionBulletin) -> str:
        """
        Return a stable per-entry GUID.

        Composed from bulletin_id + region_id so a bulletin update
        (upsert of the same ``bulletin_id`` with a new ``issued_at``)
        does not appear as a new entry to feed readers keyed on GUID.
        """
        return f"{item.bulletin.bulletin_id}::{item.region.region_id}"

    def item_guid_is_permalink(self) -> bool:
        """Mark the composite GUID as a non-URL identifier, not a permalink."""
        return False
