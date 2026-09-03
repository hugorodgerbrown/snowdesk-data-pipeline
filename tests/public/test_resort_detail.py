"""
tests/public/test_resort_detail.py — Tests for the resort detail page (SNOW-504).

Covers ``apps.public.views.resort_detail`` (``/resorts/<slug>/``, SNOW-796):
  - 200 + content: resort name, canton, parent region name, today's danger
    chip, and a "View bulletin" link to the region's evergreen bulletin.
  - The pre-slug ``/resorts/<id>/<slug>/`` form 301s to the canonical URL
    whatever suffix it carries (``resort_legacy_redirect``), and does not
    loop.
  - A resort with ``needs_review=True`` or null coordinates still renders.
  - An unknown slug returns 404.
  - Favourite-star state: sign-in CTA for anonymous/ineligible visitors,
    the button (unfavourited or favourited) for eligible ones.
  - SNOW-807: the danger area is one link to the bulletin; each curated
    location is a link to its weather page (no inline weather); the
    observations section is one link to the map with the reports sheet
    open, flown to the resort.
  - Resort facts block (SNOW-695): the curated Resort columns the page
    stored but never rendered. Every cell renders when curated, an unset
    cell is omitted, and the whole block is omitted rather than rendered
    empty when nothing is curated at all.
"""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.bulletins.models import RegionDayRating
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortFactory,
    ResortLocationFactory,
    UserFactory,
    WeatherFactory,
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

    @pytest.mark.parametrize("rated", [True, False])
    def test_the_danger_area_is_one_link_to_the_bulletin(self, rated: bool) -> None:
        """SNOW-807: chip or note, the whole area is one tappable link.

        tests/e2e/test_resort_page.py clicks ``[data-testid="resort-danger"] a``,
        so the container must hold exactly one anchor whichever state it is in.
        """
        region = MicroRegionFactory.create()
        resort = ResortFactory.create(region=region)
        if rated:
            RegionDayRatingFactory.create(
                region=region, max_rating=RegionDayRating.Rating.CONSIDERABLE
            )

        content = Client().get(resort.get_absolute_url()).content.decode()

        area = re.search(
            r'<div[^>]*data-testid="resort-danger"[^>]*>(.*?)</div>', content, re.S
        )
        assert area is not None
        assert len(re.findall(r"<a\b", area.group(1))) == 1
        assert f'href="{region.get_absolute_url()}"' in area.group(1)
        assert "View bulletin" in area.group(1)

    def test_no_weather_is_rendered_inline(self) -> None:
        """SNOW-807: the page reads no Weather row — forecasts are links."""
        resort = ResortFactory.create()
        location = LocationFactory.create(name="Mont Fort", elevation_m=3328.0)
        ResortLocationFactory.create(resort=resort, location=location)
        WeatherFactory.create(location=location, observed_on=timezone.localdate())

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-weather"' not in content
        assert "weather-icon" not in content


@pytest.mark.django_db
class TestResortLegacyRedirect:
    """The pre-SNOW-796 ``/resorts/<id>/<slug>/`` form 301s to ``/resorts/<slug>/``."""

    def test_legacy_url_301s_to_canonical(self) -> None:
        """The integer form redirects to resort.get_absolute_url()."""
        resort = ResortFactory.create(name="Verbier")

        client = Client()
        response = client.get(f"/resorts/{resort.pk}/verbier/")

        assert response.status_code == 301
        assert response["Location"] == "/resorts/verbier/"

    def test_legacy_suffix_is_ignored(self) -> None:
        """A stale name suffix still lands on the stored slug's page."""
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

    def test_unknown_pk_returns_404(self) -> None:
        """An integer nothing owns is a 404, not a redirect to nowhere."""
        client = Client()
        response = client.get("/resorts/999999/nowhere/")
        assert response.status_code == 404


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

    def test_unknown_slug_returns_404(self) -> None:
        """An unknown slug returns 404."""
        client = Client()
        response = client.get("/resorts/nowhere/")
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
        """public:resort reverses to /resorts/<slug>/ — no pk in it."""
        resort = ResortFactory.create(name="Verbier")
        url = reverse("public:resort", kwargs={"slug": resort.slug})
        assert url == "/resorts/verbier/"
        assert str(resort.pk) not in url


@pytest.mark.django_db
class TestResortLocations:
    """SNOW-807: the resort's curated locations, each a link to its weather page."""

    def test_each_curated_location_links_to_its_weather_page(self) -> None:
        """Primary first, then role and name; label, role and elevation on the row."""
        resort = ResortFactory.create(name="Verbier")
        summit = LocationFactory.create(
            name="Mont Fort", kind="PEAK", elevation_m=3328.0
        )
        village = LocationFactory.create(
            name="Verbier village", kind="VILLAGE", elevation_m=1500.0
        )
        ResortLocationFactory.create(resort=resort, location=summit, role="TOP")
        ResortLocationFactory.create(
            resort=resort, location=village, role="BASE", is_primary=True
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-locations"' in content
        first = content.index('data-testid="resort-location-0"')
        second = content.index('data-testid="resort-location-1"')
        assert first < second
        assert f'href="{village.get_absolute_url()}"' in content[first:second]
        assert "Verbier village" in content[first:second]
        assert "Base" in content[first:second]
        assert "1500 m" in content[first:second]
        assert f'href="{summit.get_absolute_url()}"' in content[second:]
        assert "3328 m" in content[second:]

    def test_an_anonymous_pin_is_labelled_with_the_resorts_name(self) -> None:
        """The location link_resort_locations mints has no name of its own."""
        resort = ResortFactory.create(name="Verbier")
        ResortLocationFactory.create(
            resort=resort, location=LocationFactory.create(anonymous=True)
        )

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert re.search(r'data-testid="resort-location-0-link"\s*>Verbier<', content)

    def test_no_curated_locations_means_no_section(self) -> None:
        """A freshly geocoded resort renders no heading over an empty list."""
        resort = ResortFactory.create(geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-locations"' not in content
        assert "Forecasts" not in content


@pytest.mark.django_db
class TestResortObservationsLink:
    """SNOW-807: reports are on the map, with the sheet open and the camera here."""

    def test_links_to_the_map_with_the_reports_sheet_open_at_this_resort(
        self,
    ) -> None:
        resort = ResortFactory.create(name="Verbier", geocoded=True)

        content = Client().get(resort.get_absolute_url()).content.decode()

        assert 'data-testid="resort-observations"' in content
        assert f'href="/?panel=reports&amp;resort={resort.slug}"' in content
        assert "Reported nearby" not in content
        assert "Reported in this region" not in content


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
