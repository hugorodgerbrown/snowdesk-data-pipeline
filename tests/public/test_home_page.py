"""
tests/public/test_home_page.py — Tests for the canonical map homepage (SNOW-344).

Covers:
  - GET / returns 200 with the map container (#map).
  - The intro overlay (#home-intro) is rendered (show_intro=True).
  - The season ribbon (#season-ribbon) is present when data exists.
  - Off-season note present when is_offseason is True.
  - The sample-bulletin URL itself returns 200 (against test_data fixture).
  - #season-ribbon carries data-default-region-name and -slug on homepage
    (CH-4115 pre-selection, retained — SNOW-342).
  - #season-ribbon carries data-default-subregion-name and -major-name (SNOW-342).
  - _default_region_label() returns a 4-tuple (SNOW-342).
  - The offmap-banner (#offmap-banner) is present on / (moved from map.html).
  - GET /map/ returns 301 to / (query strings forwarded — SNOW-344).
  - Edit-mode: /?edit=resorts + edit_map flag renders the edit panel (SNOW-344).
"""

from __future__ import annotations

import datetime

import pytest
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from freezegun import freeze_time
from waffle.testutils import override_flag

from public.views import (
    _community_reports_context,
    _default_region_label,
    _favourites_context,
)
from tests.factories import (
    AccountFactory,
    BulletinFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    SubRegionFactory,
)
from tests.seeding import seed_test_dataset


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
        """The #offmap-banner element is present on / (moved from map.html in SNOW-344).

        SNOW-486: it now renders through the shared floating-banner primitive
        and is wired to overlays.js — assert the consolidation contract
        (``data-overlay`` + the class hide idiom + a ``data-action="dismiss"``
        control) so it can't silently regress back to bespoke markup with no
        shared dismiss.
        """
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'id="offmap-banner"' in content
        # The element opts into the shared dismiss handler.
        assert "data-overlay" in content
        assert 'data-overlay-hide="class"' in content
        # And carries the standard × dismiss control every other overlay uses.
        assert 'data-action="dismiss"' in content

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
    """Tests for the off-season note in the intro overlay and the persistent bar."""

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
    def test_persistent_bar_present_when_past_season_end(self) -> None:
        """SNOW-445: full-width #map-offseason-bar is present on / when off-season.

        The bar replaces the old bottom-left #map-offseason-note chip and
        carries the derived "2025/26"-style season label.
        """
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'id="map-offseason-bar"' in content
        assert "2025/26 season archive" in content
        assert "New season starts in November" in content
        # The old chip is gone.
        assert 'id="map-offseason-note"' not in content

    @freeze_time("2026-07-20")  # summer — past the data window
    @override_settings(SEASON_START_DATE=datetime.date(2025, 11, 1))
    def test_offseason_bar_label_uses_calendar_season_not_data_start(self) -> None:
        """SNOW-445: the bar names the November→May season, not the first data date.

        With data that starts mid-season (February), the archive label must
        still read "2025/26" and "starts in November" — derived from the
        calendar season containing the data window, not the data-narrowed
        season_start (which is the first populated date, e.g. February).
        Regression guard: an earlier cut derived the label from season_start
        and rendered "starts in April — 2026/26".
        """
        region = MicroRegionFactory.create(region_id="CH-5500")
        bulletin = BulletinFactory.create()
        for day in (datetime.date(2026, 2, 10), datetime.date(2026, 2, 28)):
            RegionDayRatingFactory.create(
                region=region, date=day, source_bulletin=bulletin
            )
        client = Client()
        response = client.get(reverse("public:home"))
        content = response.content.decode()
        assert 'id="map-offseason-bar"' in content
        assert (
            "New season starts in November — currently showing the 2025/26" in content
        )
        assert "2026/26" not in content

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
    def test_persistent_bar_absent_during_season(self) -> None:
        """SNOW-445: full-width #map-offseason-bar is absent on / when in-season."""
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
        assert 'id="map-offseason-bar"' not in content


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
    def test_homepage_ribbon_has_region_defaults(self) -> None:
        """#season-ribbon on / carries the CH-4115 data-default-region-name and -slug.

        SNOW-342: the homepage pre-selects CH-4115 so the readout chip and
        breadcrumb are correct on first paint. SNOW-344 consolidated /map/ into
        / but kept this pre-selection. Requires CH-4115 and a RegionDayRating
        row so the ribbon block renders (the template skips the block when
        ribbon is falsy).
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
        assert 'data-default-region-name="Martigny Verbier"' in content
        assert 'data-default-region-slug="martigny-verbier"' in content

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


@pytest.mark.django_db
def test_sample_bulletin_url_returns_200() -> None:
    """
    The sample-bulletin URL (CH-4115 2026-02-17) returns HTTP 200 after
    seeding the test dataset.

    This verifies the contract stated in the plan: the CTA link on the
    homepage intro overlay never lands on a 404 or "No bulletin available"
    page when the dataset is seeded.
    """
    seed_test_dataset()
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
        # SNOW-457: the map-help coachmark always references "#report-btn" as
        # a step target selector, so a bare substring check would be a false
        # positive here — assert on the button's own id attribute instead.
        assert 'id="report-btn"' not in content

    @override_flag("field_observations", active=True)
    def test_report_button_shown_for_anonymous_with_flag(self) -> None:
        """Homepage shows the report button for anonymous users (parity with /map/)."""
        client = Client(SERVER_NAME="localhost")
        content = client.get(reverse("public:home")).content.decode()
        assert "report-btn" in content
        assert 'data-report-eligible="false"' in content
        # Anonymous users are not "unverified" — they get the sign-in CTA, not
        # the "verify your email" prompt (SNOW-477).
        assert 'data-report-unverified="false"' in content
        # Anonymous users carry the sign-in URL so report.js can render the
        # sign-in CTA in place of the report form.
        assert "data-signin-url" in content

    @override_flag("field_observations", active=True)
    def test_report_button_eligible_for_verified_account(self) -> None:
        """Homepage marks the button eligible for a logged-in, verified account.

        Eligibility requires a verified ``Account`` (matching the server gate,
        SNOW-477).
        """
        account = AccountFactory.create(is_verified=True)
        client = Client(SERVER_NAME="localhost")
        client.force_login(account.user)
        content = client.get(reverse("public:home")).content.decode()
        assert "report-btn" in content
        assert 'data-report-eligible="true"' in content
        assert 'data-report-unverified="false"' in content

    @override_flag("field_observations", active=True)
    def test_report_button_unverified_for_unverified_account(self) -> None:
        """Homepage marks the button unverified for a logged-in, unverified user.

        An account whose email is not verified is not eligible; the button
        carries ``data-report-unverified="true"`` so report.js shows the
        "verify your email" prompt (SNOW-477).
        """
        account = AccountFactory.create(is_verified=False)
        client = Client(SERVER_NAME="localhost")
        client.force_login(account.user)
        content = client.get(reverse("public:home")).content.decode()
        assert "report-btn" in content
        assert 'data-report-eligible="false"' in content
        assert 'data-report-unverified="true"' in content


@pytest.mark.django_db
class TestFavouritesContext:
    """Unit tests for _favourites_context() (SNOW-414).

    Called directly (via RequestFactory) rather than through the full
    home() round-trip so the {flag on/off} x {anon/auth} matrix is cheap
    to exercise — mirrors the existing _default_region_label() unit tests
    in TestDefaultRegionLabel above. django_db is required even for the
    "build" (unsaved) cases: waffle.flag_is_active() queries the Flag
    table regardless of override_flag's monkeypatched active state.
    """

    def _request(self, *, user: "AbstractBaseUser | AnonymousUser") -> HttpRequest:
        request = RequestFactory().get("/")
        request.user = user  # type: ignore[assignment]
        return request

    @override_flag("favourites", active=False)
    def test_flag_off_anon_not_visible_not_eligible(self) -> None:
        """Flag off + anonymous: neither visible nor eligible, no URLs."""
        ctx = _favourites_context(self._request(user=AnonymousUser()))
        assert ctx["favourites_visible"] is False
        assert ctx["favourites_eligible"] is False
        assert "favourites_geojson_url" not in ctx

    @override_flag("favourites", active=False)
    def test_flag_off_auth_not_visible_not_eligible(self) -> None:
        """Flag off + authenticated: still neither visible nor eligible.

        The flag gates the feature entirely, regardless of auth state.
        """
        account = AccountFactory.create()
        ctx = _favourites_context(self._request(user=account.user))
        assert ctx["favourites_visible"] is False
        assert ctx["favourites_eligible"] is False

    @override_flag("favourites", active=True)
    def test_flag_on_anon_visible_not_eligible(self) -> None:
        """Flag on + anonymous: visible (so the anon CTA can render), not eligible."""
        ctx = _favourites_context(self._request(user=AnonymousUser()))
        assert ctx["favourites_visible"] is True
        assert ctx["favourites_eligible"] is False
        assert ctx["favourites_geojson_url"] == reverse("favourites:geojson")
        assert ctx["favourite_create_url"] == reverse("favourites:create")
        assert ctx["favourites_signin_url"] == reverse("accounts:sign_in")
        assert "__UUID__" in ctx["favourite_rename_url_template"]
        assert "__UUID__" in ctx["favourite_delete_url_template"]

    @override_flag("favourites", active=True)
    def test_flag_on_auth_visible_and_eligible(self) -> None:
        """Flag on + authenticated: both visible and eligible."""
        account = AccountFactory.create()
        ctx = _favourites_context(self._request(user=account.user))
        assert ctx["favourites_visible"] is True
        assert ctx["favourites_eligible"] is True


@pytest.mark.django_db
class TestHomePageFavouritesParity:
    """The favourites map controls render on / per SNOW-414's eligibility rules."""

    @override_flag("favourites", active=False)
    def test_favourite_controls_absent_when_flag_off(self) -> None:
        """No Add-favourite control, overlay toggle, or #map data-* when the flag is off."""
        client = Client(SERVER_NAME="localhost")
        content = client.get(reverse("public:home")).content.decode()
        # SNOW-457: the map-help coachmark always references
        # "#favourite-add-btn" as a step target selector, so a bare
        # substring check would be a false positive — assert on the
        # button's own id attribute instead.
        assert 'id="favourite-add-btn"' not in content
        assert 'data-overlay-key="favourites"' not in content
        assert "data-favourites-url" not in content

    @override_flag("favourites", active=True)
    def test_add_control_shown_for_anonymous_with_flag(self) -> None:
        """Anonymous visitors see the Add-favourite control (with a sign-in CTA)
        but not the overlay toggle (eligible-only) or the geojson URL.
        """
        client = Client(SERVER_NAME="localhost")
        content = client.get(reverse("public:home")).content.decode()
        assert "favourite-add-btn" in content
        assert 'data-favourites-eligible="false"' in content
        assert "data-signin-url" in content
        assert 'data-overlay-key="favourites"' not in content
        assert "data-favourites-url" not in content

    @override_flag("favourites", active=True)
    def test_add_control_and_overlay_eligible_for_account(self) -> None:
        """A logged-in account sees the Add control, the overlay toggle,
        and #map carries the per-user geojson URL.
        """
        account = AccountFactory.create()
        client = Client(SERVER_NAME="localhost")
        client.force_login(account.user)
        content = client.get(reverse("public:home")).content.decode()
        assert "favourite-add-btn" in content
        assert 'data-favourites-eligible="true"' in content
        assert 'data-overlay-key="favourites"' in content
        assert reverse("favourites:geojson") in content


@pytest.mark.django_db
class TestCommunityReportsContext:
    """Unit tests for _community_reports_context() (SNOW-419).

    Called directly (via RequestFactory) rather than through the full
    home() round-trip — mirrors TestFavouritesContext above. Unlike
    favourites there is no per-user eligibility split: the overlay shows
    anonymised, publicly-shared data, so the flag alone controls
    visibility regardless of authentication state.
    """

    def _request(self, *, user: "AbstractBaseUser | AnonymousUser") -> HttpRequest:
        request = RequestFactory().get("/")
        request.user = user  # type: ignore[assignment]
        return request

    @override_flag("community_reports", active=False)
    def test_flag_off_not_visible_no_url(self) -> None:
        """Flag off: not visible, no geojson URL, regardless of auth state."""
        ctx = _community_reports_context(self._request(user=AnonymousUser()))
        assert ctx["community_reports_visible"] is False
        assert "community_reports_geojson_url" not in ctx

    @override_flag("community_reports", active=True)
    def test_flag_on_visible_with_url_for_anonymous(self) -> None:
        """Flag on + anonymous: visible, carries the geojson URL."""
        ctx = _community_reports_context(self._request(user=AnonymousUser()))
        assert ctx["community_reports_visible"] is True
        assert ctx["community_reports_geojson_url"] == reverse(
            "api:community_reports_geojson"
        )

    @override_flag("community_reports", active=True)
    def test_flag_on_visible_with_url_for_authenticated(self) -> None:
        """Flag on + authenticated: also visible — no eligibility gate."""
        account = AccountFactory.create()
        ctx = _community_reports_context(self._request(user=account.user))
        assert ctx["community_reports_visible"] is True
        assert ctx["community_reports_geojson_url"] == reverse(
            "api:community_reports_geojson"
        )


@pytest.mark.django_db
class TestHomePageCommunityReportsParity:
    """The community-reports map controls render on / per SNOW-419's rules."""

    @override_flag("community_reports", active=False)
    def test_controls_absent_when_flag_off(self) -> None:
        """No overlay toggle or #map data-* when the flag is off."""
        client = Client(SERVER_NAME="localhost")
        content = client.get(reverse("public:home")).content.decode()
        assert 'data-overlay-key="community_reports"' not in content
        assert "data-community-reports-url" not in content
        assert 'data-community-reports-eligible="false"' in content

    @override_flag("community_reports", active=True)
    def test_controls_shown_when_flag_on(self) -> None:
        """Overlay toggle and #map geojson URL render when the flag is on."""
        client = Client(SERVER_NAME="localhost")
        content = client.get(reverse("public:home")).content.decode()
        assert 'data-overlay-key="community_reports"' in content
        assert 'data-community-reports-eligible="true"' in content
        assert reverse("api:community_reports_geojson") in content
