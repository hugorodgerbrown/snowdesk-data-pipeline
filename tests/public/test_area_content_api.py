"""
tests/public/test_area_content_api.py — Tests for /api/area-content/ (SNOW-953).

Covers:
  the superset invariant — over every real CH micro-region boundary, the
                      endpoint never names fewer regions than really
                      intersect the box. The assertion SNOW-924 made in
                      ``tests/js/test_basemap_download_core.js``, moved
                      here with the selection it guards.
  cross-border boxes — a rectangle over two countries answers with both.
  bbox validation    — missing, non-numeric, wrong-arity, out-of-range and
                      inverted boxes all 400.
  edge inclusion     — a region touching the box only at its edge is in.
  privacy            — a favourite-only Location is never named.
  response policy    — ETag, revalidation, and no ``Vary: Cookie`` even
                      with analytics enabled.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse
from pytest_django import DjangoDbBlocker

from apps.regions.models import MicroRegion
from tests.factories import (
    FavouriteFactory,
    LocationFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    ResortLocationFactory,
    SubRegionFactory,
)


def _url(bbox: str | None = None) -> str:
    """Return the endpoint URL, optionally with a ``?bbox=`` parameter.

    Args:
        bbox: The raw query-parameter value, or None for a bare request.

    Returns:
        The URL to GET.

    """
    base = reverse("api:area_content")
    return base if bbox is None else f"{base}?bbox={bbox}"


def _square(bbox: list[float]) -> dict[str, Any]:
    """Return a GeoJSON Polygon covering ``bbox`` exactly.

    Args:
        bbox: ``[west, south, east, north]`` in degrees.

    Returns:
        A GeoJSON Polygon geometry dict.

    """
    west, south, east, north = bbox
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, south],
                [east, south],
                [east, north],
                [west, north],
                [west, south],
            ]
        ],
    }


@pytest.fixture(autouse=True)
def _clear_index_cache() -> None:
    """Drop the memoised region/weather indexes before every test.

    The indexes are cached for a day, which is right in production and
    wrong here: two tests with different rows would otherwise share one
    answer.
    """
    cache.clear()


# ---------------------------------------------------------------------------
# The answer
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_names_the_regions_the_rectangle_touches() -> None:
    """The payload is ids and slugs, and only for what the box reaches."""
    major = MajorRegionFactory.create(prefix="CH-9", country="CH")
    sub = SubRegionFactory.create(prefix="CH-91", major=major)
    MicroRegionFactory.create(
        region_id="CH-9101",
        name="Inside",
        subregion=sub,
        boundary=_square([7.0, 46.0, 7.3, 46.3]),
    )
    MicroRegionFactory.create(
        region_id="CH-9103",
        name="Far Away",
        subregion=sub,
        boundary=_square([9.0, 47.0, 9.5, 47.5]),
    )

    response = Client().get(_url("7.05,46.05,7.25,46.25"))

    assert response.status_code == 200
    assert response.json()["regions"] == [{"id": "CH-9101", "slug": "inside"}]


@pytest.mark.django_db
def test_a_cross_border_box_answers_with_both_countries() -> None:
    """The candidate set is the whole estate, not one country's feed.

    This is the failure SNOW-931 closed, made impossible by construction:
    there is no client-side "which countries are loaded" state for the
    answer to depend on.
    """
    swiss = SubRegionFactory.create(
        prefix="CH-71", major=MajorRegionFactory.create(prefix="CH-7", country="CH")
    )
    french = SubRegionFactory.create(
        prefix="FR-71", major=MajorRegionFactory.create(prefix="FR-7", country="FR")
    )
    MicroRegionFactory.create(
        region_id="CH-7101",
        name="Chablais",
        subregion=swiss,
        boundary=_square([6.7, 46.1, 6.9, 46.3]),
    )
    MicroRegionFactory.create(
        region_id="FR-7101",
        name="Haut Chablais",
        subregion=french,
        boundary=_square([6.5, 46.0, 6.75, 46.2]),
    )

    payload = Client().get(_url("6.6,46.05,6.85,46.25")).json()

    assert [row["id"] for row in payload["regions"]] == ["CH-7101", "FR-7101"]


@pytest.mark.django_db
def test_a_region_touching_only_the_edge_is_included() -> None:
    """Edge-inclusive: a shared edge costs one page, exclusion costs a bulletin."""
    sub = SubRegionFactory.create(
        prefix="CH-81", major=MajorRegionFactory.create(prefix="CH-8", country="CH")
    )
    MicroRegionFactory.create(
        region_id="CH-8101",
        name="Neighbour",
        subregion=sub,
        boundary=_square([7.3, 46.0, 7.5, 46.2]),
    )

    payload = Client().get(_url("7.1,46.0,7.3,46.2")).json()

    assert [row["id"] for row in payload["regions"]] == ["CH-8101"]


@pytest.mark.django_db
def test_a_box_inside_no_region_answers_with_nothing() -> None:
    """An empty answer is a real answer, not an error."""
    sub = SubRegionFactory.create(
        prefix="CH-61", major=MajorRegionFactory.create(prefix="CH-6", country="CH")
    )
    MicroRegionFactory.create(
        region_id="CH-6101",
        name="Elsewhere",
        subregion=sub,
        boundary=_square([7.0, 46.0, 7.3, 46.3]),
    )

    response = Client().get(_url("1.0,41.0,1.1,41.1"))

    assert response.status_code == 200
    assert response.json() == {"regions": [], "weather": []}


@pytest.mark.django_db
def test_a_favourite_only_location_is_never_named() -> None:
    """The privacy contract ``weather_geojson`` states, held here too.

    This endpoint is unauthenticated and cacheable, so a private pin named
    in it is a private pin handed to everyone.
    """
    curated = LocationFactory.create(latitude=46.15, longitude=7.15)
    ResortLocationFactory.create(location=curated)
    private = LocationFactory.create(latitude=46.16, longitude=7.16)
    FavouriteFactory.create(location=private)

    payload = Client().get(_url("7.1,46.1,7.2,46.2")).json()

    assert payload["weather"] == [{"short_id": curated.short_id}]


# ---------------------------------------------------------------------------
# bbox validation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bbox",
    [
        None,  # missing entirely
        "",  # present and empty
        "7.0,46.0,8.0",  # three parts
        "7.0,46.0,8.0,47.0,1.0",  # five parts
        "west,46.0,8.0,47.0",  # non-numeric
        "7.0,46.0,181.0,47.0",  # east out of range
        "7.0,-91.0,8.0,47.0",  # south out of range
        "8.0,46.0,7.0,47.0",  # inverted east/west
        "7.0,47.0,8.0,46.0",  # inverted north/south
        "nan,46.0,8.0,47.0",  # parses as a float, is not a coordinate
    ],
)
def test_a_bbox_that_cannot_be_read_is_a_400(bbox: str | None) -> None:
    """Every unusable box is rejected, never normalised into a guess."""
    response = Client().get(_url(bbox))

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_bbox"}


@pytest.mark.django_db
def test_a_degenerate_box_is_valid() -> None:
    """West == east is a line, not an inversion — and a legal question."""
    response = Client().get(_url("7.0,46.0,7.0,46.0"))

    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Response policy
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_the_response_carries_an_etag_and_a_revalidatable_policy() -> None:
    """Same policy as the geojson feeds: an ETag and a public max-age."""
    response = Client().get(_url("7.0,46.0,8.0,47.0"))

    assert response.status_code == 200
    assert response["ETag"]
    cache_control = response["Cache-Control"]
    assert "public" in cache_control
    assert "max-age=300" in cache_control
    assert "stale-while-revalidate" in cache_control


@pytest.mark.django_db
def test_a_client_holding_the_answer_is_told_it_is_unchanged() -> None:
    """The ETag is usable: a matching ``If-None-Match`` gets a 304."""
    client = Client()
    first = client.get(_url("7.0,46.0,8.0,47.0"))

    second = client.get(
        _url("7.0,46.0,8.0,47.0"), headers={"if-none-match": first["ETag"]}
    )

    assert second.status_code == 304
    assert second["ETag"] == first["ETag"]


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
def test_no_cookie_vary_with_analytics_enabled() -> None:
    """SNOW-299's rule: ``Vary: Cookie`` would defeat the shared caching.

    The exemption is in ``_POSTHOG_EXEMPT_PATHS``; without it,
    ``PosthogContextMiddleware`` reads ``request.user`` and
    ``SessionMiddleware`` appends the header.
    """
    response = Client().get(_url("7.0,46.0,8.0,47.0"))

    assert response.status_code == 200
    vary = response.get("Vary", "")
    assert "Accept-Encoding" in vary
    assert "Cookie" not in vary


@pytest.mark.django_db
def test_the_indexes_are_memoised_across_requests() -> None:
    """Two requests, one walk over the boundaries.

    The index is the expensive half — it reads every region's geometry —
    and it is fixture-backed reference data, so a second request inside
    the cache window must not rebuild it.
    """
    sub = SubRegionFactory.create(
        prefix="CH-51", major=MajorRegionFactory.create(prefix="CH-5", country="CH")
    )
    MicroRegionFactory.create(
        region_id="CH-5101",
        name="Cached",
        subregion=sub,
        boundary=_square([7.0, 46.0, 7.3, 46.3]),
    )
    client = Client()
    first = client.get(_url("7.05,46.05,7.25,46.25")).json()

    MicroRegion.objects.all().delete()
    second = client.get(_url("7.05,46.05,7.25,46.25")).json()

    assert second == first


# ---------------------------------------------------------------------------
# The superset invariant, over every real CH boundary
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.xdist_group(name="eaws_ch_area_content")
class TestSupersetInvariantOverRealBoundaries:
    """The endpoint never under-selects, anywhere over Switzerland.

    THE assertion this endpoint exists to keep, and the one that must never
    be relaxed. Selection is by RECTANGLE overlap rather than real
    geometry — a deliberate over-selection, because a region wrongly
    included costs one HTML page and one wrongly excluded costs a user
    their bulletin (``docs/decisions/inside-the-boundary-is-complete.md``).

    Moved here from ``tests/js/test_basemap_download_core.js``'s
    ``areaContentPlan — the superset invariant (SNOW-924)`` sweep, which
    SNOW-953 retired along with the client-side selection it tested. Runs
    against every real CH boundary, like
    ``tests/regions/services/test_basemap_tiles.py``'s
    ``test_clip_ranges_is_a_subset_of_the_candidate_rectangle`` does at the
    other end of the pipeline.
    """

    @pytest.fixture(autouse=True, scope="class")
    @staticmethod
    def _loaded(
        django_db_setup: None, django_db_blocker: DjangoDbBlocker
    ) -> Iterator[None]:
        """Load eaws_CH.json once for this class, and flush it afterwards.

        The same shape ``tests/regions/conftest.py``'s
        ``load_eaws_fixture_once`` uses — committed outside any test's
        transaction so the class shares one load, flushed on teardown so
        the worker database is empty for whatever runs next.

        Args:
            django_db_setup: pytest-django's database-creation fixture,
                depended on so the database exists before the load.
            django_db_blocker: pytest-django's guard, unblocked so the
                load can commit.

        Yields:
            None — the loaded rows are the fixture.

        """
        del django_db_setup
        with django_db_blocker.unblock():
            call_command("loaddata", "apps/regions/fixtures/eaws_CH.json", verbosity=0)
        yield
        with django_db_blocker.unblock():
            call_command("flush", "--no-input", verbosity=0)

    @staticmethod
    def _regions_with_vertex_inside(bbox: list[float]) -> set[str]:
        """Return regions with at least one boundary vertex inside ``bbox``.

        A sound under-approximation of "really intersects": a vertex inside
        the box proves the region is, while a region crossing the box with
        every vertex outside it is missed. The asymmetry is the right way
        round — everything this returns MUST be selected, and what it
        misses only weakens the test rather than making it wrong.

        Args:
            bbox: ``[west, south, east, north]`` in degrees.

        Returns:
            The matching ``region_id`` values.

        """
        west, south, east, north = bbox
        hit: set[str] = set()
        rows = MicroRegion.objects.filter(boundary__isnull=False).values_list(
            "region_id", "boundary"
        )
        for region_id, raw in rows:
            boundary = cast("dict[str, Any]", raw)
            rings = (
                boundary["coordinates"]
                if boundary["type"] == "Polygon"
                else [ring for poly in boundary["coordinates"] for ring in poly]
            )
            for ring in rings:
                if any(
                    west <= lon <= east and south <= lat <= north for lon, lat in ring
                ):
                    hit.add(region_id)
                    break
        return hit

    def test_never_selects_fewer_regions_than_really_intersect(self) -> None:
        """A sweep across Switzerland at three box sizes, not one tidy rectangle.

        The failure this catches is a bbox derivation that is subtly tight
        rather than one that is obviously wrong, so it takes a valley-sized
        box up to one covering several cantons.
        """
        client = Client()
        checked = 0
        lon = 6.0
        while lon <= 10.0:
            lat = 45.9
            while lat <= 47.6:
                for size in (0.05, 0.2, 0.75):
                    bbox = [lon, lat, lon + size, lat + size]
                    payload = client.get(
                        _url(",".join(f"{value:.4f}" for value in bbox))
                    ).json()
                    selected = {row["id"] for row in payload["regions"]}
                    assert self._regions_with_vertex_inside(bbox) <= selected
                    checked += 1
                lat += 0.4
            lon += 0.5
        # The sweep is worth nothing if the loop bounds ever collapse.
        assert checked > 100
