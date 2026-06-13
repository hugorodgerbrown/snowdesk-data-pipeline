"""
tests/public/test_home_page.py — Tests for the SNOW-314 map-as-homepage.

Covers:
  - GET / returns 200.
  - The map container (#map) is rendered.
  - The intro overlay (#home-intro) is rendered (show_intro=True).
  - The season ribbon (#season-ribbon) is present when data exists.
  - Off-season note present when is_offseason is True.
  - sample_bulletin_url resolves to the CH-4115 2026-02-17 URL.
  - The sample-bulletin URL itself returns 200 (against test_data fixture).
  - Homepage <title> is distinct from /map/ title.
  - /map/ regression: still 200, scrubber present, #season-ribbon present,
    no #home-intro.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from freezegun import freeze_time

from tests.factories import BulletinFactory, MicroRegionFactory, RegionDayRatingFactory


@pytest.mark.django_db
class TestHomePageBasic:
    """Basic acceptance tests for the SNOW-314 map-as-homepage."""

    def test_home_returns_200(self) -> None:
        """GET / returns HTTP 200."""
        client = Client()
        response = client.get(reverse("public:home"))
        assert response.status_code == 200

    def test_home_renders_map_container(self) -> None:
        """The #map element is present — the map surface is embedded."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'id="map"' in content

    def test_home_renders_intro_overlay(self) -> None:
        """The #home-intro overlay is rendered (show_intro=True on home)."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'id="home-intro"' in content

    def test_map_page_has_no_intro_overlay(self) -> None:
        """/map/ does not render #home-intro (show_intro=False)."""
        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()
        assert 'id="home-intro"' not in content

    def test_home_title_distinct_from_map_title(self) -> None:
        """The homepage <title> is different from the /map/ page <title>."""
        client = Client()
        home_title_start = (
            client.get(reverse("public:home")).content.decode().index("<title>")
        )
        map_title_start = (
            client.get(reverse("public:map")).content.decode().index("<title>")
        )
        home_title = client.get(reverse("public:home")).content.decode()[
            home_title_start : home_title_start + 120
        ]
        map_title = client.get(reverse("public:map")).content.decode()[
            map_title_start : map_title_start + 120
        ]
        assert home_title != map_title

    def test_home_sample_bulletin_url_in_context(self) -> None:
        """home() passes sample_bulletin_url pointing to CH-4115 2026-02-17."""
        client = Client()
        response = client.get(reverse("public:home"))
        assert "sample_bulletin_url" in response.context
        expected = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "martigny-verbier",
                "date_str": "2026-02-17",
            },
        )
        assert response.context["sample_bulletin_url"] == expected

    def test_home_sample_bulletin_link_in_html(self) -> None:
        """The sample-bulletin URL appears in the rendered HTML."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        expected = reverse(
            "public:bulletin_date",
            kwargs={
                "region_id": "ch-4115",
                "slug": "martigny-verbier",
                "date_str": "2026-02-17",
            },
        )
        assert expected in content

    def test_home_loads_map_assets(self) -> None:
        """The homepage loads maplibre, map.css, map.js, and home_intro.js."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert "maplibre-gl" in content
        assert "/static/css/map.css" in content
        assert "/static/js/map.js" in content
        assert "home_intro.js" in content


@pytest.mark.django_db
class TestHomePageOffseason:
    """Tests for the off-season note in the intro overlay."""

    @freeze_time("2026-06-15")  # past the May 31 season end
    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    def test_offseason_note_present_when_past_season_end(self) -> None:
        """Off-season note (data-testid=home-intro-offseason) appears after season end."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'data-testid="home-intro-offseason"' in content

    @freeze_time("2026-03-10")  # today is past data_end when no data exists after Feb
    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    def test_offseason_note_absent_during_season(self) -> None:
        """Off-season note is absent when today is within the active season window.

        Uses today=2026-03-10 with data through 2026-03-15 so that data_end
        (2026-03-15) > today, meaning is_offseason=False.  Without the factory
        row, data_end would be None and season_end would fall back to the
        calendar end (2026-05-31), which is also > today — making the factory
        data load non-vacuous: the assertion exercises the in-season branch via
        the data-narrowed season_end rather than the calendar fallback.
        """
        region = MicroRegionFactory.create(region_id="CH-5500")
        bulletin = BulletinFactory.create()
        # data_end will be 2026-03-15; today (2026-03-10) < data_end → in-season.
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 3, 15),
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'data-testid="home-intro-offseason"' not in content


@pytest.mark.django_db
class TestHomePageRibbon:
    """Tests for the default-region ribbon rendered at first paint."""

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_ribbon_present_when_region_exists(self) -> None:
        """#season-ribbon is present when CH-4115 exists and season has data."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 2, 17),
            max_rating="high",
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'id="season-ribbon"' in content

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_ribbon_contains_high_cell_when_high_day_exists(self) -> None:
        """A High-danger day produces a ribbon-cell--high cell in the HTML."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 2, 17),
            max_rating="high",
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert "ribbon-cell--high" in content

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_map_page_ribbon_present(self) -> None:
        """/map/ also renders #season-ribbon (ribbon is a shared map feature)."""
        region = MicroRegionFactory.create(region_id="CH-4115")
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 2, 17),
            max_rating="high",
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()
        assert 'id="season-ribbon"' in content


@pytest.mark.django_db
class TestMapViewRegression:
    """Regression tests: /map/ is behaviour-preserved after SNOW-314."""

    def test_map_still_returns_200(self) -> None:
        """GET /map/ returns HTTP 200."""
        client = Client()
        response = client.get(reverse("public:map"))
        assert response.status_code == 200

    def test_map_scrubber_present(self) -> None:
        """The season scrubber is still present on /map/."""
        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()
        assert 'id="season-scrubber"' in content

    def test_map_has_no_home_intro(self) -> None:
        """The /map/ page does not render the intro overlay."""
        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()
        assert 'id="home-intro"' not in content

    def test_map_loads_map_js(self) -> None:
        """map.js is still loaded on /map/."""
        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()
        assert "/static/js/map.js" in content

    def test_map_does_not_load_home_intro_js(self) -> None:
        """/map/ does not load home_intro.js (that's a homepage-only script)."""
        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()
        assert "home_intro.js" not in content


@pytest.mark.django_db(transaction=True)
def test_sample_bulletin_url_returns_200() -> None:
    """
    The sample-bulletin URL (CH-4115 2026-02-17) returns HTTP 200 after
    loading test_data.

    This verifies the contract stated in the plan: the CTA link on the
    homepage intro overlay never lands on a 404 or "No bulletin available"
    page when the fixture data is loaded.
    """
    call_command("loaddata", "test_data", verbosity=0)
    client = Client()
    url = reverse(
        "public:bulletin_date",
        kwargs={
            "region_id": "ch-4115",
            "slug": "martigny-verbier",
            "date_str": "2026-02-17",
        },
    )
    response = client.get(url)
    assert response.status_code == 200, (
        f"Expected 200 from {url} but got {response.status_code}. "
        "The sample-bulletin CTA must always land on a valid bulletin."
    )
