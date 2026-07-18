"""
public/feeds.py — RSS feeds for the public bulletin site.

Provides ``CountryBulletinFeed``, a per-country RSS feed of the most
recent bulletin updates for that country. Registered at
``/<country>/feed.rss`` (see ``public/urls.py``) with ``country`` one
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
3. The homepage carries ``<link rel="alternate" type="application/rss+xml">``
   for each active country so browsers and feed readers can pick them up
   from the page head.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.syndication.views import Feed
from django.http import Http404
from django.utils.feedgenerator import Atom1Feed

from bulletins.models import RegionBulletin

# Covered ISO-3166 alpha-2 country codes. Kept as a lowercase set for
# case-insensitive URL matching without lowercasing at every call site.
_SUPPORTED_COUNTRIES: frozenset[str] = frozenset({"ch", "at", "it", "fr"})

# Human-readable country names. Duplicated from ``public.api.COUNTRY_NAMES``
# rather than imported to keep this module's import graph independent of
# ``public.api`` (which imports from ``public.views``, creating a chain
# that has already caused one circular import — see
# ``serve_llms_full_txt`` in ``public/views.py``).
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
        """Return the feed's canonical HTML page — the interactive map."""
        return f"{settings.SITE_BASE_URL.rstrip('/')}/"

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
        ``bulletin`` and the region's ``subregion__major`` chain in a
        single query so ``item_link``, ``item_title``, and
        ``item_pubdate`` do not fan out into N+1 lookups.

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
            .select_related("bulletin", "region__subregion__major")
            .order_by("-bulletin__issued_at", "region__region_id")[:_MAX_ITEMS]
        )

    def item_title(self, item: RegionBulletin) -> str:
        """Return the entry title — the region name as at bulletin issue."""
        return item.region_name_at_time or item.region.name

    def item_link(self, item: RegionBulletin) -> str:
        """Return the entry link — the region's evergreen bulletin URL."""
        return item.region.get_absolute_url()

    def item_description(self, item: RegionBulletin) -> str:
        """
        Return the entry description — the bulletin's prose summary.

        Pulls ``fallback_key_message`` from the render model when
        present; falls back to an empty string when the render model is
        not yet built (``render_model_version == 0``) or does not
        expose that field.
        """
        render_model = item.bulletin.render_model or {}
        summary = render_model.get("fallback_key_message") or ""
        return str(summary)

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
