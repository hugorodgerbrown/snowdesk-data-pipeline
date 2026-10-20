"""
tests/public/test_resorts_index.py — Tests for the resorts index (SNOW-1150).

Covers ``apps.public.views.resorts_index`` (``/resorts/``):
  - In season: CORE resorts sit under today's ``max_rating``, highest level
    first and A–Z within a level; an unrated CORE resort goes in the final
    "No bulletin" row; a STANDARD resort is in the index but not on the
    board; a rating row with no ``source_bulletin`` does not count.
  - Out of season (no CORE region rated today): the no-bulletins statement
    and the CORE resorts A–Z with their lift ranges, no range for a resort
    without one, and no level rows.
  - The index: every resort links to its page, cantons ordered by count,
    a compound code named in full, and touring terrain only in its own
    section.
  - The schema.org CollectionPage JSON-LD: one ListItem per resort with
    absolute URLs, and a ``</script>`` that cannot break out of the tag.
  - A query count that does not grow with the number of resorts.
  - The meta description: today's spread in season, the index wording out
    of it.
  - Discovery: the footer link, the resort page's link back, llms.txt.
    (The sitemap entry is covered by tests/public/test_sitemap.py, which
    walks ``StaticViewSitemap.ROUTES``.)
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any

import pytest
from django.conf import settings
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from freezegun import freeze_time

from apps.bulletins.models import RegionDayRating
from apps.regions.models import Resort
from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortFactory,
)

IN_SEASON = "2026-01-15T11:00:00+00:00"
OFF_SEASON = "2026-10-15T11:00:00+00:00"
TODAY = datetime.date(2026, 1, 15)

_LINK_TEXT_RE = re.compile(r'<a[^>]*href="([^"]*)"[^>]*>\s*([^<]*?)\s*</a>', re.S)


def _core(name: str, **kwargs: Any) -> Resort:
    """Create a CORE ski resort in its own region."""
    return ResortFactory.create(name=name, tier=Resort.Tier.CORE, **kwargs)


def _rate(resort: Resort, rating: str, *, with_bulletin: bool = True) -> None:
    """Give the resort's region a rating row for today."""
    RegionDayRatingFactory.create(
        region=resort.region,
        date=TODAY,
        max_rating=rating,
        min_rating=rating,
        source_bulletin=BulletinFactory.create() if with_bulletin else None,
    )


def _page() -> str:
    """Fetch ``/resorts/`` and return its HTML."""
    response = Client().get(reverse("public:resorts"))
    assert response.status_code == 200
    return response.content.decode()


def _section(content: str, testid: str) -> str:
    """Return the markup from a ``data-testid`` to the end of its section."""
    start = content.index(f'data-testid="{testid}"')
    end = content.find("</section>", start)
    return content[start:end]


def _board_rows(content: str) -> list[tuple[str, list[str]]]:
    """Return the board as ``[(data-level, [resort names])]``, in order."""
    board = _section(content, "resorts-board")
    rows = []
    for chunk in board.split('data-testid="resorts-board-row"')[1:]:
        level = re.search(r'data-level="([^"]*)"', chunk)
        assert level is not None
        rows.append(
            (level.group(1), [m.group(2) for m in _LINK_TEXT_RE.finditer(chunk)])
        )
    return rows


def _json_ld(content: str) -> dict[str, Any]:
    """Return the page's parsed JSON-LD payload."""
    match = re.search(
        r'<script type="application/ld\+json"[^>]*>(.*?)</script>', content, re.S
    )
    assert match, "expected a JSON-LD script tag"
    payload: dict[str, Any] = json.loads(
        re.sub(r"\{#.*?#\}", "", match.group(1), flags=re.S)
    )
    return payload


def _meta_description(content: str) -> str:
    """Return the rendered meta description."""
    match = re.search(r'<meta name="description" content="([^"]*)"', content)
    assert match is not None
    return match.group(1)


@pytest.mark.django_db
class TestBoardInSeason:
    """Today's danger, grouped by level, for the CORE resorts."""

    @freeze_time(IN_SEASON)
    def test_resorts_sit_under_their_level_highest_first_a_to_z(self) -> None:
        """Levels run High → Low; names run A–Z inside a level."""
        _rate(_core("Zermatt"), RegionDayRating.Rating.CONSIDERABLE)
        _rate(_core("Arosa"), RegionDayRating.Rating.CONSIDERABLE)
        _rate(_core("Verbier"), RegionDayRating.Rating.HIGH)
        _rate(_core("Davos"), RegionDayRating.Rating.LOW)

        assert _board_rows(_page()) == [
            ("high", ["Verbier"]),
            ("considerable", ["Arosa", "Zermatt"]),
            ("low", ["Davos"]),
        ]

    @freeze_time(IN_SEASON)
    def test_each_row_carries_the_danger_tile_and_level_word(self) -> None:
        """The tile is the shared ``.danger-tile`` with its digit."""
        _rate(_core("Verbier"), RegionDayRating.Rating.HIGH)

        board = _section(_page(), "resorts-board")

        assert re.search(
            r'<span\s+class="danger-tile"\s+data-level="high"\s*>4</span>', board
        )
        assert "High" in board

    @freeze_time(IN_SEASON)
    def test_an_unrated_core_resort_is_in_the_no_bulletin_row(self) -> None:
        """A CORE resort with no row today goes last, under "No bulletin"."""
        _rate(_core("Verbier"), RegionDayRating.Rating.MODERATE)
        _core("Andermatt")

        content = _page()

        assert _board_rows(content)[-1] == ("none", ["Andermatt"])
        assert "No bulletin" in _section(content, "resorts-board")

    @freeze_time(IN_SEASON)
    def test_a_standard_resort_is_indexed_but_not_on_the_board(self) -> None:
        """Only CORE resorts reach the board; every resort is in the index."""
        _rate(_core("Verbier"), RegionDayRating.Rating.MODERATE)
        ResortFactory.create(name="Grächen", tier=Resort.Tier.STANDARD)

        content = _page()

        names = [name for _level, row in _board_rows(content) for name in row]
        assert names == ["Verbier"]
        assert "Grächen" in _section(content, "resorts-index")

    @freeze_time(IN_SEASON)
    def test_a_row_without_a_source_bulletin_does_not_count(self) -> None:
        """A placeholder row is not a bulletin, as on the map."""
        _rate(_core("Verbier"), RegionDayRating.Rating.MODERATE)
        _rate(_core("Laax"), RegionDayRating.Rating.HIGH, with_bulletin=False)

        assert _board_rows(_page()) == [
            ("moderate", ["Verbier"]),
            ("none", ["Laax"]),
        ]

    @freeze_time(IN_SEASON)
    def test_yesterdays_rating_is_not_todays(self) -> None:
        """Only a row dated today is read."""
        resort = _core("Verbier")
        RegionDayRatingFactory.create(
            region=resort.region,
            date=TODAY - datetime.timedelta(days=1),
            max_rating=RegionDayRating.Rating.HIGH,
            source_bulletin=BulletinFactory.create(),
        )
        _rate(_core("Arosa"), RegionDayRating.Rating.LOW)

        assert _board_rows(_page())[-1] == ("none", ["Verbier"])


@pytest.mark.django_db
class TestOffSeason:
    """No CORE region rated today: a statement and the CORE resorts."""

    @freeze_time(OFF_SEASON)
    def test_says_there_are_no_bulletins_and_lists_core_a_to_z(self) -> None:
        """The note, then CORE resorts A–Z with their lift ranges."""
        _core("Verbier", base_elevation_m=1500, top_elevation_m=3330)
        _core("Andermatt", base_elevation_m=1444, top_elevation_m=2961)
        ResortFactory.create(name="Grächen", tier=Resort.Tier.STANDARD)

        content = _page()
        off_season = _section(content, "resorts-off-season")

        assert (
            "No avalanche bulletins today. The season usually opens in November."
            in off_season
        )
        names = [m.group(2) for m in _LINK_TEXT_RE.finditer(off_season)]
        assert names == ["Andermatt", "Verbier"]
        assert "1444&ndash;2961 m" in off_season
        assert "1500&ndash;3330 m" in off_season

    @freeze_time(OFF_SEASON)
    def test_a_resort_without_elevations_shows_no_range(self) -> None:
        """No base or top, no range — never a half-empty one."""
        _core("Verbier")
        _core("Arosa", top_elevation_m=2653)

        off_season = _section(_page(), "resorts-off-season")

        assert 'data-testid="resorts-elevation"' not in off_season

    @freeze_time(OFF_SEASON)
    def test_no_level_rows_render(self) -> None:
        """Out of season there is no board at all, and no season aggregate."""
        _core("Verbier")

        content = _page()

        assert 'data-testid="resorts-board"' not in content
        assert 'data-testid="resorts-board-row"' not in content
        assert "danger-tile" not in content
        assert "Peak" not in content


@pytest.mark.django_db
class TestIndex:
    """Every resort, by canton, then the touring areas."""

    @freeze_time(OFF_SEASON)
    def test_every_resort_links_to_its_page(self) -> None:
        """The index is the hub: each resort page is one link away."""
        resorts = [
            ResortFactory.create(name="Verbier"),
            ResortFactory.create(name="Davos", canton="GR"),
            ResortFactory.create(name="Grimsel", kind=Resort.Kind.TOURING_TERRAIN),
        ]

        content = _page()

        for resort in resorts:
            assert f'href="{resort.get_absolute_url()}"' in content

    @freeze_time(OFF_SEASON)
    def test_cantons_are_ordered_by_count_and_named(self) -> None:
        """Most resorts first, ties by name; the compound code in full."""
        for name in ("Davos", "Laax", "Arosa"):
            ResortFactory.create(name=name, canton="GR")
        for name in ("Verbier", "Zermatt"):
            ResortFactory.create(name=name, canton="VS")
        ResortFactory.create(name="Meiringen", canton="BE/VS")

        index = _section(_page(), "resorts-index")
        groups = index.split('data-testid="resorts-canton"')[1:]
        headings = [re.sub(r"\s+", " ", g.split("</h3>")[0]) for g in groups]
        names = [[m.group(2) for m in _LINK_TEXT_RE.finditer(g)] for g in groups]

        assert ["Graubünden" in h for h in headings] == [True, False, False]
        assert "Valais" in headings[1]
        assert "Bern / Valais" in headings[2]
        assert names == [
            ["Arosa", "Davos", "Laax"],
            ["Verbier", "Zermatt"],
            ["Meiringen"],
        ]

    @freeze_time(OFF_SEASON)
    def test_touring_terrain_is_only_in_its_own_section(self) -> None:
        """A lift-less area is never listed under a canton as a ski resort."""
        ResortFactory.create(name="Verbier")
        ResortFactory.create(
            name="Grimsel", canton="BE", kind=Resort.Kind.TOURING_TERRAIN
        )

        content = _page()
        touring = _section(content, "resorts-touring")

        assert "Grimsel" in touring
        assert "Bern" in touring
        assert "Grimsel" not in _section(content, "resorts-index")


@pytest.mark.django_db
class TestStructuredData:
    """The schema.org CollectionPage JSON-LD."""

    @freeze_time(OFF_SEASON)
    def test_one_list_item_per_resort_with_absolute_urls(self) -> None:
        """An ItemList of every resort page, positioned from 1."""
        verbier = ResortFactory.create(name="Verbier")
        grimsel = ResortFactory.create(name="Grimsel", kind=Resort.Kind.TOURING_TERRAIN)
        base = settings.SITE_BASE_URL.rstrip("/")

        data = _json_ld(_page())

        assert data["@context"] == "https://schema.org"
        assert data["@type"] == "CollectionPage"
        assert data["url"] == f"{base}/resorts/"
        items = data["mainEntity"]["itemListElement"]
        assert data["mainEntity"]["@type"] == "ItemList"
        assert data["mainEntity"]["numberOfItems"] == 2
        assert items == [
            {
                "@type": "ListItem",
                "position": 1,
                "url": f"{base}{grimsel.get_absolute_url()}",
                "name": "Grimsel",
            },
            {
                "@type": "ListItem",
                "position": 2,
                "url": f"{base}{verbier.get_absolute_url()}",
                "name": "Verbier",
            },
        ]

    @freeze_time(OFF_SEASON)
    def test_a_script_close_in_a_name_cannot_break_out(self) -> None:
        """``</script>`` in a curated name is escaped inside the tag."""
        ResortFactory.create(name="Evil</script><b>x</b>")

        content = _page()

        block = content.split('<script type="application/ld+json"', 1)[1]
        block = block.split("</script>", 1)[0]
        assert "<\\/script>" in block
        names = [i["name"] for i in _json_ld(content)["mainEntity"]["itemListElement"]]
        assert names == ["Evil</script><b>x</b>"]


@pytest.mark.django_db
class TestQueryCount:
    """The page costs the same whatever the number of resorts."""

    @freeze_time(IN_SEASON)
    def test_the_query_count_does_not_grow_with_the_resorts(
        self, django_assert_num_queries: Any
    ) -> None:
        """One resort query and one ratings query, for 1 resort or 10."""
        client = Client()
        _rate(_core("Resort 00"), RegionDayRating.Rating.MODERATE)
        # Warm any per-process caches (waffle flags, content types) first.
        client.get(reverse("public:resorts"))

        with CaptureQueriesContext(connection) as single:
            client.get(reverse("public:resorts"))
        for n in range(1, 10):
            _rate(_core(f"Resort {n:02d}"), RegionDayRating.Rating.HIGH)
            ResortFactory.create(
                name=f"Standard {n:02d}", region=MicroRegionFactory.create()
            )

        with django_assert_num_queries(len(single.captured_queries)):
            content = client.get(reverse("public:resorts")).content.decode()

        assert sum(len(names) for _level, names in _board_rows(content)) == 10


@pytest.mark.django_db
class TestPageMeta:
    """The title is fixed; the description follows today's board."""

    @freeze_time(IN_SEASON)
    def test_in_season_the_description_names_todays_spread(self) -> None:
        """Counts per level, highest first, matching the board."""
        _rate(_core("Verbier"), RegionDayRating.Rating.HIGH)
        _rate(_core("Arosa"), RegionDayRating.Rating.CONSIDERABLE)
        _rate(_core("Davos"), RegionDayRating.Rating.CONSIDERABLE)

        content = _page()
        description = _meta_description(content)

        assert description == (
            "Today&#x27;s avalanche danger at 3 Swiss ski resorts: "
            "1 High, 2 Considerable."
        )
        assert len(description) < 155
        assert "Swiss ski resorts — avalanche conditions today · Snowdesk" in content

    @freeze_time(OFF_SEASON)
    def test_out_of_season_the_description_describes_the_index(self) -> None:
        """No levels to name, so it says what the index links to."""
        _core("Verbier")
        ResortFactory.create(name="Grimsel", kind=Resort.Kind.TOURING_TERRAIN)

        description = _meta_description(_page())

        assert description == (
            "2 Swiss ski resorts and touring areas: links to each resort&#x27;s "
            "avalanche bulletin and mountain weather."
        )


@pytest.mark.django_db
class TestDiscovery:
    """The index is linked from the footer, every resort page and llms.txt."""

    @freeze_time(OFF_SEASON)
    def test_the_footer_links_to_the_index(self) -> None:
        """The site footer carries a Resorts link on every page."""
        content = Client().get(reverse("public:help")).content.decode()

        footer = content[content.index('data-testid="site-footer"') :]
        assert f'href="{reverse("public:resorts")}">Resorts</a>' in footer

    @freeze_time(OFF_SEASON)
    def test_a_resort_page_links_back_to_the_index(self) -> None:
        """Two-way linking: every resort page names the hub."""
        resort = ResortFactory.create(name="Verbier")

        content = Client().get(resort.get_absolute_url()).content.decode()

        link = content[content.index('data-testid="resort-all-resorts-link"') - 200 :]
        assert f'href="{reverse("public:resorts")}"' in link.split("</a>", 1)[0]
        assert "All resorts" in link.split("</a>", 1)[0]

    def test_llms_txt_lists_the_index(self) -> None:
        """The low-token site index names the resorts page."""
        body = Client().get("/llms.txt").content.decode()

        base = settings.SITE_BASE_URL.rstrip("/")
        assert f"[Swiss ski resorts]({base}{reverse('public:resorts')})" in body
