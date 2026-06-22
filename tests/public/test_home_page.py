"""
tests/public/test_home_page.py — Tests for the canonical map homepage (SNOW-344).

Covers:
  - GET / returns 200 with the map container (#map).
  - The intro overlay (#home-intro) is rendered (show_intro=True).
  - The season ribbon (#season-ribbon) is present when data exists.
  - Off-season note present when is_offseason is True.
  - sample_bulletin_url resolves to the CH-4115 2026-02-17 URL.
  - The sample-bulletin URL itself returns 200 (against test_data fixture).
  - #season-ribbon carries empty data-default-region-* attributes (no pre-selection).
  - The offmap-banner (#offmap-banner) is present on /.
  - GET /map/ returns 301 to / (query strings forwarded).
  - Edit-mode: /?edit=resorts + edit_map flag renders the edit panel.
"""

from __future__ import annotations

import datetime

import pytest
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from freezegun import freeze_time
from waffle.testutils import override_flag

from tests.factories import (
    BulletinFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    SubscriberFactory,
)


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

    def test_home_renders_offmap_banner(self) -> None:
        """The #offmap-banner element is present on / (moved from map.html in SNOW-344)."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'id="offmap-banner"' in content

    def test_map_redirect_returns_301_to_home(self) -> None:
        """/map/ returns a 301 to / (SNOW-344)."""
        client = Client()
        response = client.get("/map/")
        assert response.status_code == 301
        assert response["Location"] == "/"

    def test_map_redirect_forwards_query_string(self) -> None:
        """/map/?d=2026-01-15 redirects to /?d=2026-01-15 (query_string=True)."""
        client = Client()
        response = client.get("/map/?d=2026-01-15")
        assert response.status_code == 301
        assert response["Location"] == "/?d=2026-01-15"

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
        """Off-season note (.home-intro-offseason-ref) appears in the intro after season end."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert "home-intro-offseason-ref" in content

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
        assert "home-intro-offseason-ref" not in content


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
    def test_ribbon_season_data_available_via_api(self) -> None:
        """CH-4115 RegionDayRating rows are accessible via /api/season-ratings/.

        SNOW-314 moved ribbon cells from server-rendered HTML to JS-injected
        elements (painted by ``seasonRibbonInit`` after the ratings cache resolves).
        The API endpoint carries the data that drives the cell colours; this test
        confirms a High-danger row is present in the API response so the JS can
        paint the correct colour.
        """
        region = MicroRegionFactory.create(region_id="CH-4115")
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 2, 17),
            max_rating="high",
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("api:ratings"))
        assert response.status_code == 200
        data = response.json()
        # The High rating is encoded as 4 in the int-packed ratings payload.
        assert data.get("2026-02-17", {}).get("CH-4115") == 4

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_home_ribbon_present_when_data_exists(self) -> None:
        """#season-ribbon is present on / when CH-4115 has season data."""
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


@pytest.mark.django_db
class TestHomePageReadoutData:
    """Tests for the data-* attributes that drive the readout chip."""

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_homepage_ribbon_has_empty_region_defaults(self) -> None:
        """#season-ribbon on / carries empty data-default-region-name and -slug.

        SNOW-344: the homepage no longer pre-selects CH-4115. The readout and
        scrubber track start neutral so the user's first tap is the activation
        point. Requires CH-4115 and a RegionDayRating row so the ribbon block
        renders (the template skips the block when ribbon is falsy).
        """
        region = MicroRegionFactory.create(region_id="CH-4115", name="Martigny Verbier")
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 2, 17),
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'data-default-region-name=""' in content
        assert 'data-default-region-slug=""' in content

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_region_readout_is_anchor_element(self) -> None:
        """#region-readout is rendered as an <a> element for the bulletin CTA.

        Requires CH-4115 and a RegionDayRating row so the ribbon block renders
        (the template skips the {% if ribbon %} block when ribbon is falsy).
        """
        region = MicroRegionFactory.create(region_id="CH-4115", name="Martigny Verbier")
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 2, 17),
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert '<a id="region-readout"' in content


@pytest.mark.django_db
class TestMapRedirect:
    """SNOW-344: /map/ is now a permanent 301 redirect to /."""

    def test_map_redirect_is_permanent(self) -> None:
        """GET /map/ returns 301 (permanent redirect)."""
        client = Client()
        response = client.get("/map/")
        assert response.status_code == 301

    def test_map_redirect_target_is_home(self) -> None:
        """/map/ redirects to / (the canonical map page)."""
        client = Client()
        response = client.get("/map/")
        assert response["Location"] == "/"

    def test_map_redirect_forwards_query_string(self) -> None:
        """/map/?d=2026-01-15 redirects to /?d=2026-01-15."""
        client = Client()
        response = client.get("/map/?d=2026-01-15")
        assert response.status_code == 301
        assert response["Location"] == "/?d=2026-01-15"

    def test_map_redirect_followed_renders_map(self) -> None:
        """Following the /map/ redirect lands on the live map page."""
        client = Client()
        response = client.get("/map/", follow=True)
        assert response.status_code == 200
        content = response.content.decode()
        assert 'id="map"' in content
        assert 'id="home-intro"' in content


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


@pytest.mark.django_db
class TestHomeEditMode:
    """SNOW-344: /?edit=resorts + edit_map flag gates the edit-resorts panel.

    Mirrors the TestMapViewEditMode suite that previously lived against
    map_view (tests/public/test_edit_resorts_api.py). The same rules
    apply now that home() absorbs the edit-resorts context block.
    """

    @override_flag("edit_map", active=True)
    def test_query_string_with_flag_renders_panel(self) -> None:
        """/?edit=resorts + flag active shows the edit-resorts panel."""
        client = Client()
        response = client.get(reverse("public:home") + "?edit=resorts")
        assert response.status_code == 200
        assert b"edit-resorts-panel" in response.content

    @override_flag("edit_map", active=False)
    def test_query_string_without_flag_silent_fallback(self) -> None:
        """/?edit=resorts + flag inactive renders the normal map (no panel)."""
        client = Client()
        response = client.get(reverse("public:home") + "?edit=resorts")
        assert response.status_code == 200
        assert b"edit-resorts-panel" not in response.content

    @override_flag("edit_map", active=True)
    def test_no_query_string_does_not_render_panel(self) -> None:
        """Without ?edit=resorts the panel is absent even when the flag is on."""
        client = Client()
        response = client.get(reverse("public:home"))
        assert response.status_code == 200
        assert b"edit-resorts-panel" not in response.content


@pytest.mark.django_db
class TestHomePageReportButtonParity:
    """The field-report control is present on / (SNOW-330 / SNOW-344).

    Guards against the report context not being propagated into home().
    """

    @override_flag("field_observations", active=False)
    def test_report_button_absent_when_flag_off(self) -> None:
        """No report button on the homepage when the flag is inactive."""
        client = Client(SERVER_NAME="localhost")
        content = client.get(reverse("public:home")).content.decode()
        assert "report-btn" not in content

    @override_flag("field_observations", active=True)
    def test_report_button_shown_for_anonymous_with_flag(self) -> None:
        """Homepage shows the report button for anonymous users (parity with /map/)."""
        client = Client(SERVER_NAME="localhost")
        content = client.get(reverse("public:home")).content.decode()
        assert "report-btn" in content
        assert 'data-report-eligible="false"' in content
        # Anonymous users carry the sign-in URL so report.js can render the
        # sign-in CTA in place of the report form.
        assert "data-signin-url" in content

    @override_flag("field_observations", active=True)
    def test_report_button_eligible_for_subscriber(self) -> None:
        """Homepage marks the button eligible for a logged-in subscriber."""
        subscriber = SubscriberFactory.create()
        client = Client(SERVER_NAME="localhost")
        client.force_login(subscriber.user)
        content = client.get(reverse("public:home")).content.decode()
        assert "report-btn" in content
        assert 'data-report-eligible="true"' in content
