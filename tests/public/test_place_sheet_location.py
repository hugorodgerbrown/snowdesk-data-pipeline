"""
tests/public/test_place_sheet_location.py — the "Your location" sheet a tap
on the locate-me dot opens (SNOW-1395, ``place_sheet.location_sheet_partial``).

Covers the guards (HTMX only, a coordinate required, a malformed date), the
no-store policy (the fix is never kept), the title as the ground under the
fix and its fallbacks (the device's altitude, the region's name, the
coordinate), the bulletin card, the problem-terrain row shown only when the
fix stands in a decided rule on steep ground, and the save star's two shapes.

The ground reading and the region lookup are patched: the terrain tileset is
remote and the regions' geometry is fixture data the tests do not load.
"""

from __future__ import annotations

import re
from typing import Any
from unittest import mock

import pytest
from django.test import Client
from django.urls import reverse

from apps.public.your_location import GROUND_DEVICE, GROUND_TERRAIN, Ground
from apps.regions.models import MicroRegion
from tests.factories import (
    LocationFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortLocationFactory,
    UserFactory,
    WeatherFactory,
)
from tests.public.test_resort_detail import _todays_bulletin

# A fix NE of Verbier, on the ground the tests below describe.
LAT, LON = 46.0905, 7.2737

# 2,410 m, facing NE, 30–35°: inside a wind slab rule of N–E above 2,200 m.
STEEP_NE = Ground(height_m=2410, octant="NE", slope_band=6, source=GROUND_TERRAIN)


def _url(**params: Any) -> str:
    """Build the fragment's URL with ``params`` as its query."""
    query = "&".join(f"{key}={value}" for key, value in params.items())
    url = reverse("public:location_sheet_partial")
    return f"{url}?{query}" if query else url


def _get(
    client: Client,
    *,
    ground: Ground = STEEP_NE,
    region: MicroRegion | None = None,
    **params: Any,
) -> Any:
    """GET the fragment as the sheet script does, for a patched ground and region."""
    query = {"lat": LAT, "lon": LON, **params}
    with (
        mock.patch("apps.public.place_sheet.read_ground", return_value=ground),
        mock.patch("apps.public.place_sheet.region_for_point", return_value=region),
    ):
        return client.get(_url(**query), HTTP_HX_REQUEST="true")


def _rated_region() -> MicroRegion:
    """A region with today's bulletin (a wind slab, N–E, 2,200 m+) and its rating."""
    region = MicroRegionFactory.create(name="Martigny-Verbier")
    _todays_bulletin(region)
    RegionDayRatingFactory.create(region=region, max_rating="considerable")
    return region


@pytest.mark.django_db
class TestGuards:
    """The request guards, and the policy that keeps the fix out of every cache."""

    def test_plain_get_is_rejected(self, client: Client) -> None:
        assert client.get(_url(lat=LAT, lon=LON)).status_code == 400

    def test_missing_coordinate_is_400(self, client: Client) -> None:
        response = client.get(_url(lat=LAT), HTTP_HX_REQUEST="true")
        assert response.status_code == 400

    @pytest.mark.parametrize(
        ("lat", "lon"), [("north", LON), ("91", LON), (LAT, "181"), ("nan", LON)]
    )
    def test_malformed_coordinate_is_400(
        self, client: Client, lat: Any, lon: Any
    ) -> None:
        response = client.get(_url(lat=lat, lon=lon), HTTP_HX_REQUEST="true")
        assert response.status_code == 400

    def test_malformed_date_is_400(self, client: Client) -> None:
        assert _get(client, d="yesterday").status_code == 400

    def test_nothing_keeps_the_answer(self, client: Client) -> None:
        response = _get(client)
        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]

    def test_the_device_altitude_reaches_the_reader(self, client: Client) -> None:
        with (
            mock.patch(
                "apps.public.place_sheet.read_ground", return_value=STEEP_NE
            ) as read,
            mock.patch("apps.public.place_sheet.region_for_point", return_value=None),
        ):
            client.get(_url(lat=LAT, lon=LON, alt="2385"), HTTP_HX_REQUEST="true")
        assert read.call_args.args == (LAT, LON, 2385.0)

    @pytest.mark.parametrize("alt", ["high", "12000", "", "nan", "inf"])
    def test_an_unusable_altitude_is_dropped(self, client: Client, alt: str) -> None:
        with (
            mock.patch(
                "apps.public.place_sheet.read_ground", return_value=STEEP_NE
            ) as read,
            mock.patch("apps.public.place_sheet.region_for_point", return_value=None),
        ):
            client.get(_url(lat=LAT, lon=LON, alt=alt), HTTP_HX_REQUEST="true")
        assert read.call_args.args == (LAT, LON, None)


@pytest.mark.django_db
class TestTitle:
    """The kind line, then the ground itself as the title."""

    def test_kind_line_names_the_sheet(self, client: Client) -> None:
        content = _get(client).content.decode()
        assert 'data-place-kind="location"' in content
        assert "Your location" in content

    def test_title_is_height_aspect_and_band(self, client: Client) -> None:
        content = _get(client).content.decode()
        assert "2,410&nbsp;m" in content
        assert ">NE<" in content
        assert "30–35°" in content
        assert 'data-slope-class="0"' in content

    def test_gentle_ground_names_its_band_uncoloured(self, client: Client) -> None:
        gentle = Ground(height_m=1520, octant="S", slope_band=2, source=GROUND_TERRAIN)
        content = _get(client, ground=gentle).content.decode()
        assert "10–15°" in content
        assert "data-slope-class" not in content

    def test_device_height_alone(self, client: Client) -> None:
        device = Ground(
            height_m=980, octant=None, slope_band=None, source=GROUND_DEVICE
        )
        content = _get(client, ground=device).content.decode()
        assert "980&nbsp;m" in content
        assert 'data-testid="location-slope"' not in content

    def test_no_height_is_the_regions_name(self, client: Client) -> None:
        unknown = Ground(height_m=None, octant=None, slope_band=None, source=None)
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        content = _get(client, ground=unknown, region=region).content.decode()
        title = content.split('data-testid="location-title"', 1)[1].split("</h2>")[0]
        assert "Martigny-Verbier" in title

    def test_nothing_at_all_is_the_coordinate(self, client: Client) -> None:
        unknown = Ground(height_m=None, octant=None, slope_band=None, source=None)
        content = _get(client, ground=unknown).content.decode()
        assert "46.0905, 7.2737" in content


@pytest.mark.django_db
class TestBulletin:
    """The region's card, and the warning row only when the fix is inside."""

    def test_the_regions_level_opens_its_bulletin(self, client: Client) -> None:
        region = _rated_region()
        content = _get(client, region=region).content.decode()
        assert 'data-testid="location-region-link"' in content
        assert 'data-level="considerable"' in content

    def test_in_problem_terrain_says_so(self, client: Client) -> None:
        content = _get(client, region=_rated_region()).content.decode()
        assert 'data-testid="location-in-terrain"' in content
        assert "In problem terrain" in content
        assert "Wind slab · NE · 2,200 m+" in content

    @pytest.mark.parametrize(
        "ground",
        [
            # Below the band.
            Ground(height_m=1900, octant="NE", slope_band=6, source=GROUND_TERRAIN),
            # An aspect the problem does not name.
            Ground(height_m=2410, octant="S", slope_band=6, source=GROUND_TERRAIN),
            # Under 30°, where the map paints nothing either.
            Ground(height_m=2410, octant="NE", slope_band=5, source=GROUND_TERRAIN),
            # Level ground: no aspect to test.
            Ground(height_m=2410, octant=None, slope_band=0, source=GROUND_TERRAIN),
            # The device's height alone: no aspect or slope to test.
            Ground(height_m=2410, octant=None, slope_band=None, source=GROUND_DEVICE),
        ],
    )
    def test_anywhere_else_says_nothing(self, client: Client, ground: Ground) -> None:
        content = _get(client, ground=ground, region=_rated_region()).content.decode()
        assert 'data-testid="location-in-terrain"' not in content

    def test_a_region_with_no_bulletin_says_so(self, client: Client) -> None:
        region = MicroRegionFactory.create()
        content = _get(client, region=region).content.decode()
        assert "No bulletin available on this date." in content
        assert 'data-testid="location-in-terrain"' not in content

    def test_outside_every_region_says_so(self, client: Client) -> None:
        content = _get(client).content.decode()
        assert "No avalanche bulletin covers this spot" in content

    def test_problems_are_judged_at_the_fixs_height(self, client: Client) -> None:
        with mock.patch(
            "apps.public.place_sheet.annotate_problem_relevance", return_value=[]
        ) as annotate:
            _get(client, region=_rated_region())
        assert annotate.call_args.args[1] == 2410


@pytest.mark.django_db
class TestStar:
    """The star saves the spot; a reader who cannot save is offered sign-in."""

    def test_signed_in_reader_gets_the_save_star(self, client: Client) -> None:
        client.force_login(UserFactory.create())
        content = _get(client).content.decode()
        assert "data-location-star" in content
        assert 'data-lat="46.090500"' in content
        assert 'data-lon="7.273700"' in content
        assert "data-location-star-signin" not in content

    def test_the_star_is_a_glass_roundel(self, client: Client) -> None:
        """The resort sheet's star shape, and the region sheet's pin's."""
        client.force_login(UserFactory.create())
        content = _get(client).content.decode()
        star = re.search(r"<button\b[^>]*data-location-star\b[^>]*>", content)
        assert star is not None
        assert "map-glass-roundel" in star.group(0)
        assert "map-sheet-roundel" in star.group(0)

    def test_the_sign_in_link_is_a_glass_roundel(self, client: Client) -> None:
        """A visitor's way in takes the same shape as the star it stands for."""
        content = _get(client).content.decode()
        link = re.search(r"<a\b[^>]*data-location-star-signin[^>]*>", content)
        assert link is not None
        assert "map-glass-roundel" in link.group(0)

    def test_anonymous_reader_gets_sign_in(self, client: Client) -> None:
        content = _get(client).content.decode()
        assert "data-location-star-signin" in content
        assert reverse("accounts:sign_in") in content


@pytest.mark.django_db
class TestTerrainTodayRow:
    """SNOW-1422: the fix's Today row, and the rest of its day marked data-dated."""

    def test_the_row_names_the_level_and_one_reading(self, client: Client) -> None:
        region = _rated_region()
        point = LocationFactory.create(latitude=LAT + 0.01, longitude=LON)
        ResortLocationFactory.create(location=point)
        WeatherFactory.create(location=point, temperature_2m_max=-6.0)
        content = _get(client, region=region).content.decode()
        start = content.index('data-testid="place-sheet-today"')
        row = content[
            content.rindex("<button", 0, start) : content.index("</button>", start)
        ]
        assert "data-terrain-only" in row
        assert 'data-level="considerable"' in row
        text = " ".join(re.sub(r"<[^>]+>", " ", row).split())
        assert text.startswith("3 Today Considerable")
        assert "-6°" in text

    def test_the_body_under_the_peek_is_dated(self, client: Client) -> None:
        content = _get(client, region=_rated_region()).content.decode()
        region = content.index('data-testid="location-region"')
        assert content.rindex('<div class="space-y-4" data-dated>', 0, region) > (
            content.index('data-testid="place-sheet-today"')
        )

    def test_outside_coverage_the_row_says_no_bulletin(self, client: Client) -> None:
        content = _get(client).content.decode()
        start = content.index('data-testid="place-sheet-today"')
        row = content[start : content.index("</button>", start)]
        assert "No bulletin" in row
