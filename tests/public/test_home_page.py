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
  - #season-ribbon carries data-default-region-name and -slug on homepage.
  - #season-ribbon carries data-default-subregion-name and -major-name (SNOW-342).
  - #season-ribbon carries empty defaults on /map/.
  - _default_region_label() returns a 4-tuple (SNOW-342).
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
from waffle.testutils import override_flag

from public.views import _default_region_label
from tests.factories import (
    BulletinFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    SubRegionFactory,
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

    def test_home_sample_bulletin_link_not_in_html(self) -> None:
        """The sample-bulletin CTA link is no longer rendered in the HTML (SNOW-342).

        The prototype removed the "View a sample bulletin →" anchor from the
        intro overlay actions.  sample_bulletin_url is still passed to the
        context (tested above) in case it is needed by a future feature, but it
        is not rendered as an <a> in the current template.
        """
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
        assert expected not in content

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
    """Tests for the off-season note in the intro overlay and the persistent chip."""

    @freeze_time("2026-06-15")  # past the May 31 season end
    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    def test_offseason_note_present_when_past_season_end(self) -> None:
        """Off-season note (.home-intro-offseason-ref) appears in the intro after season end."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert "home-intro-offseason-ref" in content

    @freeze_time("2026-06-15")  # past the May 31 season end
    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    def test_persistent_chip_present_when_past_season_end(self) -> None:
        """SNOW-343: persistent #map-offseason-note chip is present on / when off-season."""
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'id="map-offseason-note"' in content

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

    @freeze_time("2026-03-10")  # within active season
    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    def test_persistent_chip_absent_during_season(self) -> None:
        """SNOW-343: persistent #map-offseason-note chip is absent on / when in-season."""
        region = MicroRegionFactory.create(region_id="CH-5500")
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 3, 15),
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'id="map-offseason-note"' not in content


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
class TestHomePageReadoutData:
    """Tests for the data-* attributes that drive the readout chip."""

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_homepage_ribbon_carries_region_name(self) -> None:
        """#season-ribbon has data-default-region-name on the homepage."""
        MicroRegionFactory.create(region_id="CH-4115", name="Martigny Verbier")
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'data-default-region-name="Martigny Verbier"' in content

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_homepage_ribbon_carries_region_slug(self) -> None:
        """#season-ribbon has data-default-region-slug on the homepage.

        ``name_slug`` is derived from ``slugify(name)``; factory receives
        ``name`` so the property returns the expected slug value.
        """
        # slugify("Martigny Verbier") == "martigny-verbier"
        MicroRegionFactory.create(region_id="CH-4115", name="Martigny Verbier")
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'data-default-region-slug="martigny-verbier"' in content

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_map_page_ribbon_has_empty_region_defaults(self) -> None:
        """#season-ribbon on /map/ carries empty data-default-region-name and -slug.

        /map/ never pre-selects a region — the readout and track start empty
        so the user's first tap is the activation point.  Requires CH-4115 to
        exist so the ribbon renders (the template skips the block when ribbon is
        falsy), and a RegionDayRating row so the ribbon has at least one day.
        """
        region = MicroRegionFactory.create(region_id="CH-4115", name="Martigny Verbier")
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 2, 17),
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()
        assert 'data-default-region-name=""' in content
        assert 'data-default-region-slug=""' in content

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_region_readout_is_div_element(self) -> None:
        """#region-readout is rendered as a <div> (info-only chip; SNOW-342).

        The bulletin link is the separate #region-readout-action roundel.
        Requires CH-4115 and a RegionDayRating row so the ribbon block renders.
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
        assert '<div id="region-readout"' in content
        assert 'id="region-readout-action"' in content


@pytest.mark.django_db
class TestHomePageBreadcrumbData:
    """SNOW-342: #season-ribbon carries data-default-subregion-name and -major-name.

    The readout chip's breadcrumb (Major › Minor › Micro) is seeded from these
    data attributes so it is correct on first paint before any region-selected
    event fires.
    """

    def _make_ch4115(self, *, subregion_name_en: str, major_name_en: str) -> None:
        """Create CH-4115 with the given L2/L1 names wired up."""
        ch_major = MajorRegionFactory.create(
            prefix="CH-4",
            country="CH",
            name_en=major_name_en,
        )
        sub = SubRegionFactory.create(
            prefix="CH-41",
            major=ch_major,
            name_en=subregion_name_en,
        )
        MicroRegionFactory.create(
            region_id="CH-4115",
            name="Martigny Verbier",
            subregion=sub,
        )

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_homepage_ribbon_carries_subregion_name(self) -> None:
        """#season-ribbon has data-default-subregion-name on the homepage."""
        self._make_ch4115(subregion_name_en="Lower Valais", major_name_en="Wallis")
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'data-default-subregion-name="Lower Valais"' in content

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_homepage_ribbon_carries_major_name(self) -> None:
        """#season-ribbon has data-default-major-name on the homepage."""
        self._make_ch4115(subregion_name_en="Lower Valais", major_name_en="Wallis")
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'data-default-major-name="Wallis"' in content

    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    @freeze_time("2026-02-17")
    def test_map_page_ribbon_has_empty_breadcrumb_defaults(self) -> None:
        """/map/ carries empty data-default-subregion-name and -major-name.

        /map/ never pre-selects a region — those attributes are empty strings so
        the breadcrumb starts blank.
        """
        region = MicroRegionFactory.create(region_id="CH-4115", name="Martigny Verbier")
        bulletin = BulletinFactory.create()
        RegionDayRatingFactory.create(
            region=region,
            date=datetime.date(2026, 2, 17),
            source_bulletin=bulletin,
        )
        client = Client()
        response = client.get(reverse("public:map"))
        content = response.content.decode()
        assert 'data-default-subregion-name=""' in content
        assert 'data-default-major-name=""' in content


@pytest.mark.django_db
class TestDefaultRegionLabel:
    """Unit tests for _default_region_label() (SNOW-342).

    The function is imported directly so behaviour can be tested without an
    HTTP round-trip.  It returns a 4-tuple (name, slug, subregion_name,
    major_name) and four empty strings when CH-4115 is absent from the DB.
    """

    def test_returns_four_empty_strings_when_region_absent(self) -> None:
        """Returns four empty strings when CH-4115 does not exist."""
        result = _default_region_label()
        assert result == ("", "", "", "")

    def test_returns_name_and_slug(self) -> None:
        """Returns the micro-region name and slug as the first two elements."""
        ch_major = MajorRegionFactory.create(
            prefix="CH-4", country="CH", name_en="Wallis"
        )
        sub = SubRegionFactory.create(
            prefix="CH-41",
            major=ch_major,
            name_en="Lower Valais",
        )
        MicroRegionFactory.create(
            region_id="CH-4115",
            name="Martigny Verbier",
            subregion=sub,
        )
        name, slug, _sub, _major = _default_region_label()
        assert name == "Martigny Verbier"
        assert slug == "martigny-verbier"

    def test_returns_subregion_and_major_names(self) -> None:
        """Returns the L2 subregion name and L1 major name as the last two elements."""
        ch_major = MajorRegionFactory.create(
            prefix="CH-4",
            country="CH",
            name_en="Wallis",
            name_native="Valais",
        )
        sub = SubRegionFactory.create(
            prefix="CH-41",
            major=ch_major,
            name_en="Lower Valais",
        )
        MicroRegionFactory.create(
            region_id="CH-4115",
            name="Martigny Verbier",
            subregion=sub,
        )
        _name, _slug, subregion_name, major_name = _default_region_label()
        assert subregion_name == "Lower Valais"
        assert major_name == "Wallis"

    def test_major_falls_back_to_native_name_when_no_en(self) -> None:
        """major_name falls back to name_native when name_en is empty."""
        ch_major = MajorRegionFactory.create(
            prefix="CH-4",
            country="CH",
            name_en="",
            name_native="Valais",
        )
        sub = SubRegionFactory.create(
            prefix="CH-41",
            major=ch_major,
            name_en="Lower Valais",
        )
        MicroRegionFactory.create(
            region_id="CH-4115",
            name="Martigny Verbier",
            subregion=sub,
        )
        _name, _slug, _sub, major_name = _default_region_label()
        assert major_name == "Valais"

    def test_subregion_name_suppressed_when_equals_prefix(self) -> None:
        """subregion_name is empty when name_en equals the prefix (placeholder)."""
        ch_major = MajorRegionFactory.create(
            prefix="CH-4", country="CH", name_en="Wallis"
        )
        sub = SubRegionFactory.create(
            prefix="CH-41",
            major=ch_major,
            # placeholder: name_en == prefix
            name_en="CH-41",
        )
        MicroRegionFactory.create(
            region_id="CH-4115",
            name="Martigny Verbier",
            subregion=sub,
        )
        _name, _slug, subregion_name, _major = _default_region_label()
        assert subregion_name == ""


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


@pytest.mark.django_db
class TestHomePageReportButtonParity:
    """The homepage embeds the same map surface as /map/, so the field-report
    control must render identically on both — they are indistinguishable to a
    user (SNOW-330). Guards against the report context being wired into
    map_view but not home().
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
