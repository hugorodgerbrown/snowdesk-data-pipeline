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
  - Resort facts block (SNOW-695): the curated Resort columns the page
    stored but never rendered. Every cell renders when curated, an unset
    cell is omitted, and the whole block is omitted rather than rendered
    empty when nothing is curated at all.
"""

from __future__ import annotations

import datetime
import re

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.models import RegionDayRating
from apps.observations.models import FieldObservation
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
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
        FavouriteFactory.create(
            user=user,
            resort=resort,
            latitude=46.1,
            longitude=7.4,
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


@pytest.mark.django_db
class TestResortFacts:
    """The curated Resort columns the page stored but never rendered (SNOW-695).

    Unlike ``_resort_meta_row.html`` — the map popup's row, which keeps a
    dashed placeholder so missing curation stays visible to staff — this
    block omits an unset cell entirely and omits the whole container when
    nothing is curated. It is a public detail page, not a curation surface.
    """

    @staticmethod
    def _fully_curated() -> dict[str, object]:
        """Return kwargs setting every field the facts block reads."""
        return {
            "operator_name": "Zermatt Bergbahnen AG",
            "website": "https://www.zermatt.ch/",
            "notes": "Lift-served access to the Theodul glacier.",
            "num_lifts": 34,
            "num_runs": 53,
            "total_piste_km": 196.5,
            "base_elevation_m": 1620,
            "top_elevation_m": 3899,
            "typical_season_open": "11-23",
            "typical_season_close": "04-27",
        }

    def test_every_curated_field_renders(self) -> None:
        """A fully curated resort renders each cell."""
        resort = ResortFactory.create(**self._fully_curated())

        client = Client()
        content = client.get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-facts"' in content
        for testid in (
            "resort-facts-elevation",
            "resort-facts-lifts",
            "resort-facts-runs",
            "resort-facts-piste",
            "resort-facts-season",
            "resort-facts-operator",
            "resort-facts-website",
            "resort-facts-notes",
        ):
            assert f'data-testid="{testid}"' in content, testid
        assert "1620&ndash;3899 m" in content
        assert "34" in content
        assert "196.5 km" in content
        assert "23 Nov&ndash;27 Apr" in content
        assert "Zermatt Bergbahnen AG" in content
        assert "Theodul glacier" in content
        # Compare the rendered href exactly rather than asking whether the
        # URL appears somewhere on the page: the loose check would pass on a
        # link pointing anywhere that merely contained this string.
        website_cell = content.split('data-testid="resort-facts-website"')[1]
        href = re.search(r'href="([^"]+)"', website_cell)
        assert href is not None
        assert href.group(1) == self._fully_curated()["website"]

    def test_nothing_curated_omits_the_whole_block(self) -> None:
        """No curated field renders no container — not an empty one.

        The factory's defaults are the uncurated state, which is the
        majority case for the fixture: this is the assertion that keeps a
        thin resort page from growing an empty box.
        """
        resort = ResortFactory.create()

        client = Client()
        content = client.get(resort.get_absolute_url()).content.decode()

        assert "resort-facts" not in content
        # The page itself still renders.
        assert 'data-testid="resort-detail"' in content

    def test_partial_curation_renders_only_the_cells_it_has(self) -> None:
        """A base elevation with no top degrades to a one-sided reading."""
        resort = ResortFactory.create(
            num_lifts=4,
            base_elevation_m=1343,
            top_elevation_m=None,
            typical_season_open="12-14",
            typical_season_close="",
        )

        client = Client()
        content = client.get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-facts"' in content
        assert 'data-testid="resort-facts-elevation"' in content
        assert "from 1343 m" in content
        assert "from 14 Dec" in content
        # Nothing curated for these, so no cell at all.
        assert "resort-facts-runs" not in content
        assert "resort-facts-piste" not in content
        assert "resort-facts-operator" not in content
        assert "resort-facts-website" not in content
        assert "resort-facts-notes" not in content

    def test_zero_lifts_renders_rather_than_vanishing(self) -> None:
        """A genuine zero is a fact; the cells test `is not None`, not truthiness."""
        resort = ResortFactory.create(num_lifts=0, total_piste_km=0.0)

        client = Client()
        content = client.get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-facts-lifts"' in content
        assert 'data-testid="resort-facts-piste"' in content
        assert "0 km" in content
