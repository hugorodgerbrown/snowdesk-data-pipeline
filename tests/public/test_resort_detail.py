"""
tests/public/test_resort_detail.py — Tests for the resort detail page (SNOW-504).

Covers ``public.views.resort_detail`` (``/resorts/<id>/<slug>/``):
  - 200 + content: resort name, canton, parent region name, today's danger
    chip, and a "View bulletin" link to the region's evergreen bulletin.
  - Slug mismatch 301s to the canonical URL (mirrors the region
    canonical-slug behaviour); a rubbish slug does not loop.
  - A resort with ``needs_review=True`` or null coordinates still renders.
  - An unknown resort_id returns 404.
  - Favourite-star state: sign-in CTA for anonymous/ineligible visitors,
    the button (unfavourited or favourited) for eligible ones.
  - Distance-scoped field observations (SNOW-508): point-local when the
    resort has coordinates, region-wide fallback when it doesn't, hidden
    when the ``field_observations`` flag is inactive, and empty-state copy
    when nothing is nearby.
"""

from __future__ import annotations

import datetime

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from waffle.testutils import override_flag

from bulletins.models import RegionDayRating
from observations.models import FieldObservation
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    ForecastPointFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortFactory,
    UserFactory,
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
    """Favourite-star state mirrors public.api.resort_popup's contract."""

    @override_flag("favourites", active=True)
    def test_anonymous_shows_signin_cta(self) -> None:
        """An anonymous visitor sees the sign-in CTA, not the toggle button."""
        resort = ResortFactory.create()

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-favourite-signin"' in content
        assert 'data-testid="resort-favourite-toggle"' not in content

    @override_flag("favourites", active=True)
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

    @override_flag("favourites", active=True)
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

    def test_flag_inactive_shows_signin_cta(self) -> None:
        """An authenticated user sees the sign-in CTA when the flag is inactive."""
        resort = ResortFactory.create()
        user = UserFactory.create()

        client = Client()
        client.force_login(user)
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'data-testid="resort-favourite-toggle"' not in content
        assert 'data-testid="resort-favourite-signin"' in content


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

    @override_flag("field_observations", active=True)
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

    @override_flag("field_observations", active=True)
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

    def test_panel_hidden_when_flag_inactive(self) -> None:
        """The panel is absent entirely when field_observations is off."""
        resort = ResortFactory.create(geocoded=True)
        FieldObservationFactory.create(
            latitude=resort.latitude,
            longitude=resort.longitude,
            observed_at=self._today_at_noon(),
        )

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert 'id="obs-counts-heading"' not in content

    @override_flag("field_observations", active=True)
    def test_empty_state_point_local(self) -> None:
        """A geocoded resort with nothing nearby shows the point empty-state copy."""
        resort = ResortFactory.create(geocoded=True)

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert "Reported nearby" in content
        assert "No reports near here today." in content

    @override_flag("field_observations", active=True)
    def test_empty_state_region_wide(self) -> None:
        """A coord-null resort with nothing in-region shows the region empty-state copy."""
        resort = ResortFactory.create(latitude=None, longitude=None)

        client = Client()
        response = client.get(resort.get_absolute_url())

        content = response.content.decode()
        assert "Reported in this region" in content
        assert "No reports in this region today." in content

    @override_flag("field_observations", active=True)
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
