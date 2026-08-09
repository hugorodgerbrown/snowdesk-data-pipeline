"""
tests/public/test_resort_detail.py — Tests for the resort detail page (SNOW-504).

Covers ``apps.public.views.resort_detail`` (``/resorts/<id>/<slug>/``):
  - 200 + content: resort name, canton, parent region name, today's danger
    chip, and a "View bulletin" link to the region's evergreen bulletin.
  - Slug mismatch 301s to the canonical URL (mirrors the region
    canonical-slug behaviour); a rubbish slug does not loop.
  - A resort with ``needs_review=True`` or null coordinates still renders.
  - An unknown resort_id returns 404.
  - Favourite-star state: sign-in CTA for anonymous/ineligible visitors,
    the button (unfavourited or favourited) for eligible ones.
  - Distance-scoped field observations (SNOW-508): point-local when the
    resort has coordinates, region-wide fallback when it doesn't, and
    empty-state copy when nothing is nearby.
  - Weather (SNOW-509): the page shows the parent region's WeatherSnapshot
    as the page header and fallback, falls back to the no-snapshot panel
    with the ``?variant=panel`` HTMX retry when none exists, and renders
    regardless of favourite/auth state or ``needs_review``/coordinate gaps.
  - Point forecast (SNOW-572): below the region panel, a resort with a
    linked ``forecast_point`` carrying rows shows its own multi-day
    forecast (day strip + expandable hourly detail); a resort with no
    linked point, or a linked point with no rows in the window, shows no
    forecast section — the region panel above still covers it. One added
    query, guarded by the view's ``select_related("forecast_point")``.
"""

from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.models import RegionDayRating
from apps.bulletins.services.weather_fetcher import POINT_FORECAST_DAYS
from apps.observations.models import FieldObservation
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    ForecastPointFactory,
    ForecastPointWeatherFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortFactory,
    UserFactory,
    WeatherSnapshotFactory,
)


@pytest.mark.django_db
class TestResortDetailContent:
    """200 response with the expected content."""

    def test_returns_200_with_resort_and_region_content(self) -> None:
        """The page shows the resort name, canton, and parent region name."""
        region = MicroRegionFactory.create(name="Martigny – Verbier")
        resort = ResortFactory.create(name="Verbier", region=region, canton="VS")

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200
        content = response.content.decode()
        assert "Verbier" in content
        assert "VS" in content
        assert "Martigny" in content

    def test_includes_name_alt_when_set(self) -> None:
        """An alternative/marketing name renders when set."""
        resort = ResortFactory.create(name="Verbier", name_alt="4 Vallées")

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert "4 Vallées" in response.content.decode()

    def test_shows_todays_danger_chip_when_rating_exists(self) -> None:
        """Today's RegionDayRating renders the danger-tile chip."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        RegionDayRatingFactory.create(
            region=region, max_rating=RegionDayRating.Rating.CONSIDERABLE
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-danger-chip"' in content

    def test_shows_no_rating_note_when_no_bulletin(self) -> None:
        """No RegionDayRating for today → the no-rating note renders."""
        resort = ResortFactory.create()

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-no-rating"' in content
        assert 'data-testid="resort-danger-chip"' not in content

    def test_includes_bulletin_link(self) -> None:
        """A "View bulletin" link points at the parent region's bulletin page."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert region.get_absolute_url() in response.content.decode()


@pytest.mark.django_db
class TestResortDetailCanonicalSlug:
    """Slug-mismatch redirects to the canonical URL."""

    def test_wrong_slug_301s_to_canonical(self) -> None:
        """A stale/incorrect slug 301-redirects to resort.get_absolute_url()."""
        resort = ResortFactory.create(name="Verbier")

        client = Client()
        response = client.get(f"/resorts/{resort.pk}/wrong-slug/")

        assert response.status_code == 301
        assert response["Location"] == resort.get_absolute_url()

    def test_canonical_slug_renders_without_redirect(self) -> None:
        """The canonical slug renders directly with no redirect."""
        resort = ResortFactory.create(name="Verbier")

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200

    def test_redirect_target_itself_is_canonical(self) -> None:
        """Following the 301 lands on a 200 — the redirect doesn't loop."""
        resort = ResortFactory.create(name="Verbier")

        client = Client()
        response = client.get(f"/resorts/{resort.pk}/wrong-slug/", follow=True)

        assert response.status_code == 200
        assert response.redirect_chain == [(resort.get_absolute_url(), 301)]


@pytest.mark.django_db
class TestResortDetailEdgeCases:
    """A resort needing review or missing coordinates still renders."""

    def test_needs_review_resort_still_renders(self) -> None:
        """needs_review=True does not hide the page."""
        resort = ResortFactory.create(needs_review=True)

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200

    def test_null_coordinates_resort_still_renders(self) -> None:
        """A resort with no latitude/longitude still renders."""
        resort = ResortFactory.create(latitude=None, longitude=None)

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.status_code == 200

    def test_unknown_resort_id_returns_404(self) -> None:
        """An unknown resort_id returns 404."""
        client = Client()
        response = client.get("/resorts/999999/nowhere/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestResortDetailFavouriteState:
    """Favourite-star state mirrors apps.public.api.resort_popup's contract."""

    def test_anonymous_shows_signin_cta(self) -> None:
        """An anonymous visitor sees the sign-in CTA, not the toggle button."""
        resort = ResortFactory.create()

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-favourite-signin"' in content
        assert 'data-testid="resort-favourite-toggle"' not in content

    def test_authenticated_not_favourited_shows_unfavourited_button(self) -> None:
        """An eligible, not-yet-favouriting user sees the toggle, unfavourited."""
        resort = ResortFactory.create()
        user = UserFactory.create()

        client = Client()
        client.force_login(user)
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-favourite-toggle"' in content
        assert 'data-favourited="false"' in content

    def test_authenticated_already_favourited_shows_saved_state(self) -> None:
        """An already-favourited resort shows the toggle in its saved state."""
        resort = ResortFactory.create(latitude=46.1, longitude=7.4)
        user = UserFactory.create()
        point = ForecastPointFactory.create(latitude=46.1, longitude=7.4)
        FavouriteFactory.create(
            user=user,
            resort=resort,
            latitude=46.1,
            longitude=7.4,
            forecast_point=point,
        )

        client = Client()
        client.force_login(user)
        response = client.get(resort.get_absolute_url())

        assert 'data-favourited="true"' in response.content.decode()


@pytest.mark.django_db
class TestResortDetailUrl:
    """URL-reversal sanity check."""

    def test_url_reverses_to_expected_path(self) -> None:
        """public:resort reverses to /resorts/<id>/<slug>/."""
        resort = ResortFactory.create(name="Verbier")
        url = reverse(
            "public:resort", kwargs={"resort_id": resort.pk, "slug": "verbier"}
        )
        assert url == f"/resorts/{resort.pk}/verbier/"


@pytest.mark.django_db
class TestResortDetailLocalObservations:
    """Distance-scoped field-observation panel (SNOW-508)."""

    def _today_at_noon(self) -> datetime.datetime:
        """Return a tz-aware datetime for noon today."""
        today = timezone.localdate()
        return datetime.datetime(
            today.year, today.month, today.day, 12, 0, tzinfo=datetime.UTC
        )

    def test_point_local_scope_when_resort_has_coords(self) -> None:
        """A geocoded resort shows the point-local heading and its own count."""
        resort = ResortFactory.create(geocoded=True)  # (46.1, 7.4)
        FieldObservationFactory.create(
            latitude=resort.latitude,
            longitude=resort.longitude,
            observed_at=self._today_at_noon(),
            observation_type="WHUMPFING",
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert "Reported nearby" in content
        assert "Reported in this region" not in content
        assert "Whumpfing" in content

    def test_region_wide_fallback_when_coords_null(self) -> None:
        """A resort with no coordinates falls back to the region-wide count."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region, latitude=None, longitude=None)
        FieldObservationFactory.create(
            region=region,
            observed_at=self._today_at_noon(),
            observation_type="PINWHEELS",
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert "Reported in this region" in content
        assert "Reported nearby" not in content
        assert "Pinwheels" in content

    def test_empty_state_point_local(self) -> None:
        """A geocoded resort with nothing nearby shows the point empty-state copy."""
        resort = ResortFactory.create(geocoded=True)

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert "Reported nearby" in content
        assert "No reports near here today." in content

    def test_empty_state_region_wide(self) -> None:
        """A coord-null resort with nothing in-region shows the region empty-state copy."""
        resort = ResortFactory.create(latitude=None, longitude=None)

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert "Reported in this region" in content
        assert "No reports in this region today." in content

    def test_manual_footnote_absent_when_local_counts_are_empty(self) -> None:
        """The 'placed manually' footnote never contradicts the empty state.

        Regression: ``observation_has_user_located`` is a region-wide check
        (FK match only, no distance filter) — a MANUAL report can exist
        somewhere in the resort's region while sitting well outside the
        point-local radius, leaving ``local_observations.counts`` empty. The
        footnote must not render alongside the "no reports" empty state.
        """
        resort = ResortFactory.create(geocoded=True)  # (46.1, 7.4)
        assert resort.latitude is not None
        assert resort.longitude is not None
        FieldObservationFactory.create(
            region=resort.region,
            latitude=resort.latitude + 1.0,  # ~111 km away — outside 10 km
            longitude=resort.longitude,
            observed_at=self._today_at_noon(),
            location_source=FieldObservation.LOCATION_SOURCE.MANUAL,
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert "No reports near here today." in content
        assert "Some reports were placed manually" not in content


@pytest.mark.django_db
class TestResortDetailWeather:
    """The parent region's WeatherSnapshot renders on the resort page (SNOW-509).

    Product decision (Option 1): the resort page shows the region's
    snapshot — never a per-resort forecast — so no per-resort Open-Meteo
    fetch happens here; point weather stays a favourite-page feature.
    """

    def test_snapshot_present_shows_region_weather(self) -> None:
        """A WeatherSnapshot for the resort's region renders the populated panel."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=timezone.localdate(),
            weather_code=0,  # clear sky
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-weather-section"' in content
        assert 'data-testid="resort-weather"' in content
        assert 'data-weather-bucket="clear"' in content
        assert 'data-testid="resort-weather-hero-icon"' in content
        assert 'data-testid="resort-weather-meta"' in content
        # No region <h1> inside the weather panel — the page's own resort
        # <h1> is the only heading (SNOW-509: region_name="").
        assert 'data-testid="resort-weather-region"' not in content

    def test_no_snapshot_shows_fallback_and_panel_variant_trigger(self) -> None:
        """No WeatherSnapshot → the panel falls back with the ?variant=panel retry."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        # Deliberately no WeatherSnapshot for this region.

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.context["weather_display"] is None
        assert response.context["weather_htmx_trigger"] is True
        content = response.content.decode()
        assert 'data-testid="resort-weather"' in content
        assert 'data-weather-bucket="none"' in content
        assert "hx-post" in content
        assert "?variant=panel" in content

    def test_anonymous_visitor_sees_region_weather(self) -> None:
        """An anonymous (non-favourited) visitor still sees the region weather."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=timezone.localdate(), weather_code=3
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert 'data-testid="resort-weather"' in response.content.decode()

    def test_favourited_visitor_sees_same_region_weather(self) -> None:
        """A signed-in visitor who has favourited the resort sees the same panel."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=timezone.localdate(), weather_code=3
        )
        user = UserFactory.create()
        FavouriteFactory.create(user=user, resort=resort)

        client = Client()
        client.force_login(user)
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-weather"' in content
        assert 'data-weather-bucket="cloudy"' in content

    def test_needs_review_resort_still_shows_weather(self) -> None:
        """A needs_review resort still shows its region's weather."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region, needs_review=True)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=timezone.localdate(), weather_code=0
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert 'data-testid="resort-weather"' in response.content.decode()

    def test_no_coordinates_resort_still_shows_weather(self) -> None:
        """A resort with no lat/lon still shows its (region-keyed) weather."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region, latitude=None, longitude=None)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=timezone.localdate(), weather_code=0
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert 'data-testid="resort-weather"' in response.content.decode()

    def test_snapshot_for_other_region_does_not_leak(self) -> None:
        """A snapshot for a different region must not surface on this page."""
        region = MicroRegionFactory.create()
        other = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        WeatherSnapshotFactory.create(
            region=other, valid_for_date=timezone.localdate(), weather_code=0
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.context["weather_display"] is None

    def test_renders_temp_and_snowfall_when_populated(self) -> None:
        """A fully-populated snapshot renders temperature and snowfall (SNOW-571)."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=timezone.localdate(),
            weather_code=0,
            temperature_2m_max=4.2,
            temperature_2m_min=-3.1,
            snowfall_sum=12.0,
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert "4&deg;" in content
        assert "-3&deg;" in content
        assert "12 cm" in content

    def test_renders_explicit_zero_snowfall(self) -> None:
        """A 0 cm snowfall total still renders (SNOW-571)."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        WeatherSnapshotFactory.create(
            region=region,
            valid_for_date=timezone.localdate(),
            weather_code=0,
            snowfall_sum=0.0,
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert "0 cm" in response.content.decode()

    def test_omits_temp_and_snowfall_when_absent(self) -> None:
        """A sparse snapshot (fields unset) omits both groups (SNOW-571)."""
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        WeatherSnapshotFactory.create(
            region=region, valid_for_date=timezone.localdate(), weather_code=0
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'sr-only">Temperature<' not in content
        assert 'sr-only">Snowfall<' not in content


@pytest.mark.django_db
class TestResortDetailPointForecast:
    """The resort's own multi-day point forecast (SNOW-572).

    Reads the same forward ``ForecastPointWeather`` window and
    ``build_point_forecast_panel`` the favourite detail card uses, rendered
    below the region weather panel via the shared
    ``includes/_forecast_panel.html`` partial with
    ``testid_prefix="resort-forecast"``. No empty-state branch: a resort
    with no linked point, or a linked point with no rows, simply omits the
    section — the region panel above already covers that resort.
    """

    def test_linked_point_with_rows_renders_day_strip(self) -> None:
        """A linked forecast_point with a forward row renders the day strip."""
        point = ForecastPointFactory.create()
        resort = ResortFactory.create(forecast_point=point)
        today = timezone.localdate()
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=today, hourly_series=[]
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-forecast-section"' in content
        assert "Weather — this resort" in content
        assert 'data-testid="resort-forecast-day"' in content
        # Still present — the region panel is unaffected by the addition.
        assert 'data-testid="resort-weather-section"' in content

    def test_hourly_rows_render_expandable_detail(self) -> None:
        """A row carrying an hourly_series renders the expandable detail (rendered-markup check).

        Covers the hourly expand in pytest rather than Playwright — see the
        plan's "Deviation from the scope" note: ``<details>``/``<summary>``
        is native, zero-JavaScript markup, so the assertion belongs here.
        """
        point = ForecastPointFactory.create()
        resort = ResortFactory.create(forecast_point=point)
        today = timezone.localdate()
        # Default factory hourly_series is non-empty.
        ForecastPointWeatherFactory.create(forecast_point=point, valid_for_date=today)

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-forecast-hourly"' in content
        assert "<details" in content
        assert 'data-testid="resort-forecast-hourly-list"' in content

    def test_no_forecast_point_omits_section(self) -> None:
        """No linked forecast_point → no forecast section; region panel unaffected."""
        resort = ResortFactory.create(forecast_point=None)

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert response.context["forecast_panel"] is None
        assert "resort-forecast-section" not in content
        assert 'data-testid="resort-weather-section"' in content

    def test_linked_point_with_no_rows_omits_section(self) -> None:
        """A linked point with no ForecastPointWeather rows omits the section."""
        point = ForecastPointFactory.create()
        resort = ResortFactory.create(forecast_point=point)
        # Deliberately no ForecastPointWeather rows for this point.

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.context["forecast_panel"] is None
        assert "resort-forecast-section" not in response.content.decode()

    def test_other_points_rows_do_not_leak(self) -> None:
        """Rows for a different point never surface on this resort's page."""
        point = ForecastPointFactory.create()
        other_point = ForecastPointFactory.create(
            latitude=47.0, longitude=8.0, elevation=2000.0
        )
        resort = ResortFactory.create(forecast_point=point)
        today = timezone.localdate()
        ForecastPointWeatherFactory.create(
            forecast_point=other_point, valid_for_date=today
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        assert response.context["forecast_panel"] is None
        assert "resort-forecast-section" not in response.content.decode()

    def test_only_forward_rows_within_window_appear(self) -> None:
        """A yesterday row is excluded and the window caps at POINT_FORECAST_DAYS."""
        point = ForecastPointFactory.create()
        resort = ResortFactory.create(forecast_point=point)
        today = timezone.localdate()
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=today - datetime.timedelta(days=1)
        )
        for offset in range(POINT_FORECAST_DAYS + 3):
            ForecastPointWeatherFactory.create(
                forecast_point=point,
                valid_for_date=today + datetime.timedelta(days=offset),
            )

        client = Client()
        response = client.get(resort.get_absolute_url())

        panel = response.context["forecast_panel"]
        assert panel is not None
        dates = [day["date"] for day in panel["days"]]
        assert len(dates) == POINT_FORECAST_DAYS
        assert today - datetime.timedelta(days=1) not in dates
        assert min(dates) == today

    def test_added_query_is_the_only_added_query(self) -> None:
        """One query is added when a forecast_point exists; select_related prevents a second.

        Compares the query count for a resort with no linked point against
        an otherwise-identical resort whose point has one forward row. Any
        drift beyond exactly one extra query would mean the
        ``select_related("forecast_point")`` on the view's ``get_object_or_404``
        stopped doing its job — an unrelated-FK lazy fetch would show up as
        a second added query.
        """
        region = MicroRegionFactory.create()
        resort_without = ResortFactory.create(region=region, forecast_point=None)
        point = ForecastPointFactory.create()
        resort_with = ResortFactory.create(region=region, forecast_point=point)
        ForecastPointWeatherFactory.create(
            forecast_point=point, valid_for_date=timezone.localdate()
        )

        client = Client()
        # Uncounted warm-up — the first request in a run costs extra queries
        # for one-off cache fills (waffle flag lookup, CSP-rule lookup — see
        # tests/favourites/test_views.py's
        # test_rating_lookup_is_batched_not_n_plus_one for the same idiom).
        client.get(resort_without.get_absolute_url())

        with CaptureQueriesContext(connection) as ctx_without:
            response_without = client.get(resort_without.get_absolute_url())
        assert response_without.status_code == 200
        baseline = len(ctx_without.captured_queries)

        with CaptureQueriesContext(connection) as ctx_with:
            response_with = client.get(resort_with.get_absolute_url())
        assert response_with.status_code == 200

        assert len(ctx_with.captured_queries) == baseline + 1


@pytest.mark.django_db
class TestResortWhyItMatters:
    """The curated "why it matters" line on the resort page (SNOW-542).

    The field is curated over time rather than in one pass, so all three
    blank branches are behaviour worth pinning: staff get a curation hint,
    anonymous visitors get a register prompt (the empty slot is the most
    natural place on the page to ask for a sign-up), and a signed-in reader
    — who has no way to contribute copy — gets nothing at all.
    """

    def test_curated_line_renders(self) -> None:
        """A populated line renders as prose under the heading."""
        resort = ResortFactory.create(
            why_it_matters="High plateau above Adelboden, own cable car."
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        html = response.content.decode()
        assert 'data-testid="resort-why-it-matters"' in html
        assert "High plateau above Adelboden, own cable car." in html

    def test_blank_line_anonymous_prompts_register(self) -> None:
        """An anonymous visitor sees the register prompt, naming the resort."""
        resort = ResortFactory.create(name="Haldigrat")

        client = Client()
        response = client.get(resort.get_absolute_url())

        html = response.content.decode()
        assert 'data-testid="resort-why-it-matters-signup"' in html
        assert reverse("accounts:register") in html

    def test_blank_line_staff_shows_curation_hint(self) -> None:
        """Staff see the curation hint instead of the register prompt."""
        resort = ResortFactory.create()

        client = Client()
        client.force_login(UserFactory.create(is_staff=True))
        response = client.get(resort.get_absolute_url())

        html = response.content.decode()
        assert 'data-testid="resort-why-it-matters-hint"' in html
        assert "resort-why-it-matters-signup" not in html

    def test_blank_line_signed_in_reader_sees_nothing(self) -> None:
        """A signed-in non-staff reader gets no prompt — there is nowhere to send them."""
        resort = ResortFactory.create()

        client = Client()
        client.force_login(UserFactory.create(is_staff=False))
        response = client.get(resort.get_absolute_url())

        assert "resort-why-it-matters" not in response.content.decode()
