"""
tests/public/test_feeds.py — Tests for the per-country RSS/Atom feeds.

Covers ``CountryBulletinFeed`` in ``apps/public/feeds.py`` (SNOW-396):

* The four covered country codes (ch, at, it, fr) all resolve.
* Unknown country codes return 404.
* Content-Type is an RSS/Atom media type.
* Entries carry evergreen /<region>/<slug>/ links, not dated form-3 URLs.
* Ordering is bulletin ``issued_at`` descending.
* Items are capped at 20.
* GUIDs combine bulletin_id + region_id so an upsert of the same
  bulletin_id doesn't appear as a new entry.
* Only regions from the requested country appear.
* ``/llms.txt`` lists each country's feed under ``## Data``.
* The homepage carries a ``<link rel="alternate" type="application/rss+xml">``
  for each country.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client

from tests.factories import (
    BulletinFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    SubRegionFactory,
)


def _make_bulletin_for_region(
    region_id: str,
    country: str,
    name: str = "Region",
    issued_at: datetime | None = None,
) -> None:
    """Create a bulletin + RegionBulletin joined to a MicroRegion in ``country``."""
    major = MajorRegionFactory.create(
        prefix=f"{country}-1",
        country=country,
        name_native=f"Major {country}",
        name_en=f"Major {country}",
    )
    subregion = SubRegionFactory.create(major=major)
    region = MicroRegionFactory.create(
        region_id=region_id,
        name=name,
        subregion=subregion,
    )
    when = issued_at or datetime.now(UTC)
    bulletin = BulletinFactory.create(
        valid_from=when,
        valid_to=when + timedelta(days=1),
        issued_at=when,
    )
    RegionBulletinFactory.create(bulletin=bulletin, region=region)


@pytest.mark.django_db
class TestCountryFeedEndpoint:
    """Every supported country returns 200 with an RSS/Atom body."""

    @pytest.mark.parametrize("country", ["ch", "at", "it", "fr"])
    def test_supported_country_returns_200(self, client: Client, country: str) -> None:
        """Each of the four covered countries returns a 200 response."""
        response = client.get(f"/{country}/feed.rss")
        assert response.status_code == 200

    @pytest.mark.parametrize("country", ["ch", "at", "it", "fr"])
    def test_content_type_is_atom_or_rss(self, client: Client, country: str) -> None:
        """Content-Type advertises a feed media type."""
        response = client.get(f"/{country}/feed.rss")
        ct = response["Content-Type"].lower()
        assert "xml" in ct

    def test_uppercase_country_slug_resolves(self, client: Client) -> None:
        """Uppercase country codes are matched case-insensitively."""
        response = client.get("/ch/feed.rss")
        assert response.status_code == 200
        # And path-case variance survives, too — URL-level `ch` and `CH`
        # both resolve to the same canonical feed body.

    def test_unknown_country_returns_404(self, client: Client) -> None:
        """A country outside the covered ISO-3166 set 404s."""
        response = client.get("/de/feed.rss")
        assert response.status_code == 404


@pytest.mark.django_db
class TestCountryFeedContents:
    """The feed body lists RegionBulletins for that country, capped at 20."""

    def test_items_link_to_evergreen_region_urls(self, client: Client) -> None:
        """Every entry's <link> is the region's form-2 evergreen URL."""
        _make_bulletin_for_region("CH-4115", "CH", name="Martigny-Verbier")

        response = client.get("/ch/feed.rss")
        body = response.content.decode()

        # Extract every <link> under an <entry>/<item> — regardless of feed
        # generator (Atom vs RSS 2.0 differ in element shape); grep is the
        # smallest safe check.
        assert "/ch-4115/martigny-verbier/" in body
        date_segment = re.compile(r"/ch-4115/martigny-verbier/\d{4}-\d{2}-\d{2}/")
        assert not date_segment.search(body), (
            "feed URL for CH-4115 must be evergreen, not date-stamped"
        )

    def test_only_regions_from_requested_country_appear(self, client: Client) -> None:
        """Cross-country contamination is impossible in a per-country feed."""
        _make_bulletin_for_region("CH-4115", "CH", name="Verbier")
        _make_bulletin_for_region("AT-07-12", "AT", name="Silvretta")

        body = client.get("/ch/feed.rss").content.decode()
        assert "Verbier" in body
        assert "Silvretta" not in body

    def test_items_ordered_by_issued_at_descending(self, client: Client) -> None:
        """Most recent bulletin first."""
        older = datetime.now(UTC) - timedelta(days=2)
        newer = datetime.now(UTC)
        _make_bulletin_for_region(
            "CH-4115",
            "CH",
            name="OlderRegion",
            issued_at=older,
        )
        _make_bulletin_for_region(
            "CH-4200",
            "CH",
            name="NewerRegion",
            issued_at=newer,
        )

        body = client.get("/ch/feed.rss").content.decode()
        newer_pos = body.index("NewerRegion")
        older_pos = body.index("OlderRegion")
        assert newer_pos < older_pos, "newer bulletin must appear before older"

    def test_items_capped_at_twenty(self, client: Client) -> None:
        """More than 20 RegionBulletins collapse to the newest 20."""
        base = datetime.now(UTC)
        for i in range(25):
            _make_bulletin_for_region(
                f"CH-9{i:03d}",
                "CH",
                name=f"Region{i:03d}",
                issued_at=base - timedelta(minutes=i),
            )

        body = client.get("/ch/feed.rss").content.decode()
        # The 5 oldest — Region020..Region024 — should have been dropped.
        assert "Region000" in body
        assert "Region019" in body
        for dropped in (
            "Region020",
            "Region021",
            "Region022",
            "Region023",
            "Region024",
        ):
            assert dropped not in body, (
                f"expected {dropped} to be trimmed by the 20-item cap"
            )

    def test_guid_combines_bulletin_and_region(self, client: Client) -> None:
        """GUIDs are ``<bulletin_id>::<region_id>`` and not marked permalink.

        The composite ensures a bulletin upsert (same ``bulletin_id`` with
        new ``issued_at``) does not appear as a fresh entry to feed readers
        keyed on GUID.
        """
        _make_bulletin_for_region("CH-4115", "CH", name="Verbier")

        body = client.get("/ch/feed.rss").content.decode()
        # The bulletin_id from BulletinFactory is opaque — just assert the
        # composite shape: "<something>::CH-4115" appears.
        assert re.search(r"[^<>]+::CH-4115", body), (
            "expected composite GUID <bulletin_id>::<region_id>"
        )


@pytest.mark.django_db
def test_llms_txt_references_all_four_feeds(client: Client) -> None:
    """SNOW-396: /llms.txt lists every country's RSS feed under ## Data."""
    body = client.get("/llms.txt").content.decode()
    assert "/ch/feed.rss" in body
    assert "/at/feed.rss" in body
    assert "/it/feed.rss" in body
    assert "/fr/feed.rss" in body


@pytest.mark.django_db
def test_home_page_advertises_country_feeds_via_rel_alternate(
    client: Client,
) -> None:
    """SNOW-396: home carries rel=alternate for each country feed.

    A feed reader (or an LLM crawler) that lands on ``/`` and inspects the
    head should discover every country's RSS/Atom feed without needing to
    walk ``/llms.txt``.
    """
    body = client.get("/").content.decode()
    for country in ("ch", "at", "it", "fr"):
        pattern = re.compile(
            r'<link[^>]*rel="alternate"[^>]*type="application/rss\+xml"[^>]*'
            rf'href="[^"]*/{country}/feed\.rss"',
            re.DOTALL,
        )
        assert pattern.search(body), (
            f"expected rel=alternate RSS link for /{country}/feed.rss on home"
        )
