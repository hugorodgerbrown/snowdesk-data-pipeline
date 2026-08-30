"""
tests/public/test_map_api.py — Tests for the /api/ JSON endpoints.

Covers the endpoints consumed by the /map/ page:

* ``api:ratings``               — unified ratings endpoint (SNOW-239).
                                  Optional ?d=YYYY-MM-DD and ?country= filters.
* ``api:resorts_by_region``     — resort list per region.
* ``api:regions_geojson``       — FeatureCollection of L4 region polygons;
                                  carries ``slug`` property (SNOW-314).
* ``api:major_regions_geojson`` — FeatureCollection of L1 region polygons (SNOW-59).
* ``api:sub_regions_geojson``   — FeatureCollection of L2 region polygons (SNOW-59).
* ``api:region_summary``        — tooltip with danger-rating chip (?d= aware),
                                  English breadcrumb, date caption, and bulletin
                                  CTA (SNOW-174). Resort list removed.
* ``api:resort_popup``          — SNOW-499: minimal resort-pin popup (name,
                                  region, favourite star, "View resort →"
                                  link to the resort's own page — SNOW-504).
                                  Public; the star is gated on the
                                  ``favourites`` flag + authentication.
                                  SNOW-501: curated metadata rows (operator,
                                  website, lift/run counts, piste length,
                                  elevation range, typical season) — blank
                                  fields render a placeholder instead of
                                  being omitted, staff-worded via ``is_staff``.

SNOW-252 — peak semantics:
* ``api:ratings`` choropleth emits peak (max_rating) for two-period escalating days.
* ``api:region_summary`` tooltip chip shows peak, not morning, on split days.

SNOW-419:
* ``api:community_reports_geojson`` — anonymised, 48h-windowed
  ``FieldObservation`` overlay.

"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext, override_settings
from django.urls import reverse
from django.utils import timezone
from freezegun import freeze_time

from apps.bulletins.models import RegionDayRating
from apps.bulletins.services.slf_fetcher import BulletinSource
from apps.observations.models import FieldObservation
from apps.public import api as public_api
from apps.regions.models import Resort
from apps.regions.services.basemap_tiles import MICRO_BAND, blob_summary, build_blob
from tests.factories import (
    BulletinGroupingFactory,
    FavouriteFactory,
    FieldObservationFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionDayRatingFactory,
    ResortFactory,
    SubRegionFactory,
    UserFactory,
)

# ---------------------------------------------------------------------------
# ratings (SNOW-239)
# ---------------------------------------------------------------------------

# Shared fixture data used across ratings tests.
# CH: two regions, two dates. FR: one region, one date.
_RATINGS_DAY_ONE = dt.date(2026, 1, 15)
_RATINGS_DAY_TWO = dt.date(2026, 1, 16)


def _make_ratings_fixture() -> None:
    """Create RegionDayRating rows spanning CH and FR for two dates."""
    ch_major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    ch_sub = SubRegionFactory.create(prefix="CH-41", major=ch_major)
    region_a = MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=ch_sub
    )
    region_b = MicroRegionFactory.create(
        region_id="CH-4116", slug="ch-4116", subregion=ch_sub
    )

    fr_major = MajorRegionFactory.create(prefix="FR-1", country="FR")
    fr_sub = SubRegionFactory.create(prefix="FR-1A", major=fr_major)
    region_fr = MicroRegionFactory.create(
        region_id="FR-01", slug="fr-01", subregion=fr_sub
    )

    RegionDayRatingFactory.create(
        region=region_a,
        date=_RATINGS_DAY_ONE,
        max_rating=RegionDayRating.Rating.CONSIDERABLE,
    )
    RegionDayRatingFactory.create(
        region=region_b,
        date=_RATINGS_DAY_ONE,
        max_rating=RegionDayRating.Rating.MODERATE,
    )
    RegionDayRatingFactory.create(
        region=region_a,
        date=_RATINGS_DAY_TWO,
        max_rating=RegionDayRating.Rating.HIGH,
    )
    RegionDayRatingFactory.create(
        region=region_fr,
        date=_RATINGS_DAY_ONE,
        max_rating=RegionDayRating.Rating.LOW,
    )


@pytest.fixture(autouse=True)
def clear_ratings_cache() -> None:
    """Ensure the ratings cache is clear before every test in this module."""
    cache.clear()


@pytest.mark.django_db
def test_ratings_empty_when_no_day_ratings() -> None:
    """No RegionDayRating rows → empty dict."""
    client = Client()
    response = client.get(reverse("api:ratings"))
    assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.django_db
def test_ratings_no_filters_returns_all_countries_and_dates() -> None:
    """No filters → full season, all countries, expected shape and ints."""
    _make_ratings_fixture()
    response = Client().get(reverse("api:ratings"))
    assert response.status_code == 200
    data = response.json()

    assert set(data.keys()) == {"2026-01-15", "2026-01-16"}
    # Considerable=3, moderate=2, high=4, low=1 — see _RATING_TO_INT.
    assert data["2026-01-15"]["CH-4115"] == 3
    assert data["2026-01-15"]["CH-4116"] == 2
    assert data["2026-01-15"]["FR-01"] == 1
    assert data["2026-01-16"]["CH-4115"] == 4
    assert "FR-01" not in data["2026-01-16"]


@pytest.mark.django_db
def test_ratings_date_filter_returns_single_key_dict() -> None:
    """?d=YYYY-MM-DD → single-key dict for that date only."""
    _make_ratings_fixture()
    response = Client().get(reverse("api:ratings") + "?d=2026-01-15")
    assert response.status_code == 200
    data = response.json()

    assert set(data.keys()) == {"2026-01-15"}
    assert data["2026-01-15"]["CH-4115"] == 3
    assert data["2026-01-15"]["CH-4116"] == 2


@pytest.mark.django_db
def test_ratings_country_filter_returns_only_that_country() -> None:
    """?country=ch → season filtered to CH regions only."""
    _make_ratings_fixture()
    response = Client().get(reverse("api:ratings") + "?country=ch")
    assert response.status_code == 200
    data = response.json()

    # CH-4115 and CH-4116 present; FR-01 absent.
    assert "2026-01-15" in data
    assert "CH-4115" in data["2026-01-15"]
    assert "CH-4116" in data["2026-01-15"]
    assert "FR-01" not in data.get("2026-01-15", {})


@pytest.mark.django_db
def test_ratings_date_and_country_filter() -> None:
    """?d=YYYY-MM-DD&country=ch → single-day, single-country."""
    _make_ratings_fixture()
    response = Client().get(reverse("api:ratings") + "?d=2026-01-15&country=ch")
    assert response.status_code == 200
    data = response.json()

    assert set(data.keys()) == {"2026-01-15"}
    assert "CH-4115" in data["2026-01-15"]
    assert "FR-01" not in data["2026-01-15"]


@pytest.mark.django_db
def test_ratings_rejects_unknown_country() -> None:
    """An unknown ?country= code returns 400."""
    response = Client().get(reverse("api:ratings") + "?country=zz")
    assert response.status_code == 400
    assert response.json()["error"] == "unknown country"


@pytest.mark.django_db
def test_ratings_rejects_malformed_date_string() -> None:
    """A malformed ?d= value returns 400."""
    for bad in ("not-a-date", "2026-13-99", "20260115", ""):
        if bad == "":
            # Empty string is treated as "no filter", not an error.
            continue
        response = Client().get(reverse("api:ratings") + f"?d={bad}")
        assert response.status_code == 400, f"Expected 400 for ?d={bad!r}"
        assert response.json()["error"] == "malformed date"


@pytest.mark.django_db
def test_ratings_country_filter_case_insensitive() -> None:
    """?country=CH and ?country=ch are treated identically."""
    _make_ratings_fixture()
    r_lower = Client().get(reverse("api:ratings") + "?country=ch")
    r_upper = Client().get(reverse("api:ratings") + "?country=CH")
    assert r_lower.status_code == 200
    assert r_upper.status_code == 200
    assert r_lower.json() == r_upper.json()


@pytest.mark.django_db
def test_ratings_cache_hit_avoids_db_on_second_call() -> None:
    """After the first call, the DB is not hit again for the same cache key."""
    _make_ratings_fixture()
    client = Client()
    url = reverse("api:ratings") + "?d=2026-01-15&country=ch"

    # First call — populates the cache.
    resp1 = client.get(url)
    assert resp1.status_code == 200

    # Second call — should return from cache without hitting _build_ratings_payload.
    with patch("apps.public.api._build_ratings_payload") as mock_build:
        resp2 = client.get(url)
        assert resp2.status_code == 200
        assert resp2.json() == resp1.json()
        mock_build.assert_not_called()


@pytest.mark.django_db
def test_ratings_cache_control_header() -> None:
    """Cache-Control is public, max-age=300."""
    response = Client().get(reverse("api:ratings"))
    assert response.status_code == 200
    cc = response.get("Cache-Control", "")
    assert "public" in cc
    assert "max-age=300" in cc


@pytest.mark.django_db
def test_ratings_emits_freshness_headers_when_data_present() -> None:
    """SNOW-370: ratings response carries the three freshness headers.

    ``X-Data-Generated-At`` reflects the newest ``updated_at`` across the
    returned rows (freshest fact in the payload). ``X-Data-Max-Age`` and
    ``X-Data-Unsafe-After`` are the safety-critical defaults.
    """
    _make_ratings_fixture()
    response = Client().get(reverse("api:ratings"))
    assert response.status_code == 200
    assert "X-Data-Generated-At" in response
    assert "X-Data-Max-Age" in response
    assert "X-Data-Unsafe-After" in response
    # Ratings are safety-critical, so both max-age and unsafe-after
    # are always emitted (spec §5.4).
    assert int(response["X-Data-Max-Age"]) > 0
    assert int(response["X-Data-Unsafe-After"]) > int(response["X-Data-Max-Age"])


@pytest.mark.django_db
def test_ratings_emits_freshness_headers_when_empty() -> None:
    """Empty payload still emits freshness headers using a ``now`` fallback."""
    response = Client().get(reverse("api:ratings"))
    assert response.status_code == 200
    assert response.json() == {}
    # ``X-Data-Generated-At`` is present even on empty data — a missing
    # header would signal "server doesn't know the freshness contract"
    # to the PWA client, which is wrong.
    assert "X-Data-Generated-At" in response


@pytest.mark.django_db
def test_ratings_vary_accept_encoding_not_cookie() -> None:
    """Vary header includes Accept-Encoding but not Cookie."""
    response = Client().get(reverse("api:ratings"))
    assert response.status_code == 200
    vary = response.get("Vary", "")
    assert "Accept-Encoding" in vary
    assert "Cookie" not in vary


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
def test_ratings_vary_no_cookie_with_analytics_enabled() -> None:
    """SNOW-299 regression: Vary: Cookie absent on ratings even when PostHog is active.

    When POSTHOG_API_KEY is non-empty, PosthogContextMiddleware would normally
    read request.user to tag identities, causing SessionMiddleware to append
    Vary: Cookie and defeat Cache-Control: public CDN caching.
    The _POSTHOG_EXEMPT_PATHS exemption in config/settings/base.py prevents
    that access for this endpoint.
    """
    response = Client().get(reverse("api:ratings"))
    assert response.status_code == 200
    vary = response.get("Vary", "")
    assert "Accept-Encoding" in vary
    assert "Cookie" not in vary


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
@pytest.mark.parametrize(
    "url_name",
    ["api:regions_geojson", "api:major_regions_geojson", "api:sub_regions_geojson"],
)
def test_geojson_no_cookie_vary_with_analytics_enabled(url_name: str) -> None:
    """SNOW-299 regression: Vary: Cookie absent on GeoJSON endpoints when PostHog is active.

    When POSTHOG_API_KEY is non-empty, PosthogContextMiddleware would normally
    read request.user to tag identities, causing SessionMiddleware to append
    Vary: Cookie and defeat Cache-Control: public CDN caching.
    The _POSTHOG_EXEMPT_PATHS exemption in config/settings/base.py prevents
    that access for these endpoints.
    """
    url = reverse(url_name) + "?country=ch"
    response = Client().get(url)
    assert response.status_code == 200
    vary = response.get("Vary", "")
    assert "Accept-Encoding" in vary, (
        f"Expected Accept-Encoding in Vary with analytics enabled; got: {vary!r}"
    )
    assert "Cookie" not in vary, (
        f"Vary: Cookie must not be set even with analytics enabled; got: {vary!r}"
    )


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
@pytest.mark.parametrize(
    "url",
    [
        "api:resorts_by_region",
        "api:resorts_geojson",
        # bulletin_groupings requires a ?d= param to return 200.
        "api:bulletin_groupings_geojson?d=2026-01-15",
    ],
)
def test_remaining_cacheable_endpoints_no_cookie_vary_with_analytics_enabled(
    url: str,
) -> None:
    """SNOW-299 regression: the remaining exempt cacheable endpoints stay Cookie-free.

    Completes the key-on coverage of _POSTHOG_EXEMPT_PATHS — the three
    paths not exercised by the ratings/GeoJSON regression tests above. With
    POSTHOG_API_KEY set, PosthogContextMiddleware would otherwise read
    request.user and let SessionMiddleware append Vary: Cookie, defeating the
    Cache-Control: public CDN caching these endpoints set.
    """
    name, _, query = url.partition("?")
    target = reverse(name) + (f"?{query}" if query else "")
    response = Client().get(target)
    assert response.status_code == 200
    vary = response.get("Vary", "")
    assert "Accept-Encoding" in vary, (
        f"Expected Accept-Encoding in Vary with analytics enabled; got: {vary!r}"
    )
    assert "Cookie" not in vary, (
        f"Vary: Cookie must not be set even with analytics enabled; got: {vary!r}"
    )


# ---------------------------------------------------------------------------
# resorts-by-region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resorts_by_region_empty_when_no_resorts() -> None:
    """No Resort rows → empty dict."""
    client = Client()
    response = client.get(reverse("api:resorts_by_region"))
    assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.django_db
def test_resorts_by_region_groups_names_alphabetically() -> None:
    """Resorts are grouped by region_id and returned in alphabetical order."""
    region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
    ResortFactory.create(region=region, name="Verbier")
    ResortFactory.create(region=region, name="La Chaux")

    # A region with no resorts should be absent from the response.
    MicroRegionFactory.create(region_id="CH-9999", slug="ch-9999")

    client = Client()
    data = client.get(reverse("api:resorts_by_region")).json()
    assert data == {"CH-4115": ["La Chaux", "Verbier"]}


# ---------------------------------------------------------------------------
# regions.geojson
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_regions_geojson_returns_feature_collection() -> None:
    """Every Region with a non-null boundary becomes a Feature (CH filter)."""
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    ch_major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    sub_with_name = SubRegionFactory.create(
        prefix="CH-41",
        major=ch_major,
        name_native="Bas-Valais",
        name_en="Lower Valais",
    )
    # AT/IT fixtures store the prefix as the placeholder name; the API
    # blanks it out so the client doesn't render a redundant code.
    at_major = MajorRegionFactory.create(prefix="AT-02", country="AT")
    sub_placeholder = SubRegionFactory.create(
        prefix="AT-02-01",
        major=at_major,
        name_native="AT-02-01",
        name_en="AT-02-01",
    )
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Valais",
        slug="ch-4115",
        boundary=boundary,
        subregion=sub_with_name,
    )
    MicroRegionFactory.create(
        region_id="AT-02-01-01",
        name="AT-02-01-01",
        slug="at-02-01-01",
        boundary=boundary,
        subregion=sub_placeholder,
    )
    # Region without boundary — should be skipped.
    MicroRegionFactory.create(
        region_id="CH-9999",
        name="No geometry",
        slug="ch-9999",
        boundary=None,
    )

    client = Client()
    response = client.get(reverse("api:regions_geojson") + "?country=ch")
    assert response.status_code == 200
    data = response.json()

    assert data["type"] == "FeatureCollection"
    # Find our CH-4115 feature among any that exist.
    by_id = {f["properties"]["id"]: f for f in data["features"]}
    assert "CH-4115" in by_id
    assert "CH-9999" not in by_id  # boundary=None → skipped
    feature = by_id["CH-4115"]
    assert feature["type"] == "Feature"
    assert feature["properties"]["id"] == "CH-4115"
    assert feature["properties"]["name"] == "Valais"
    assert feature["properties"]["country"] == "CH"
    assert feature["properties"]["subregion_name"] == "Lower Valais"
    assert feature["geometry"] == boundary

    # AT placeholder name_en (== prefix) is suppressed by the view.
    at_response = client.get(reverse("api:regions_geojson") + "?country=at")
    at_by_id = {f["properties"]["id"]: f for f in at_response.json()["features"]}
    assert at_by_id["AT-02-01-01"]["properties"]["subregion_name"] == ""


@pytest.mark.django_db
def test_regions_geojson_features_carry_major_name() -> None:
    """SNOW-342: each feature's properties include the L1 major-region name.

    The season-header readout uses major_name to build the Major › Minor › Micro
    breadcrumb without a second fetch. The CH feature should carry the English
    name of its MajorRegion; the AT feature uses name_en as well (no placeholder
    suppression logic applies at L1).
    """
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    ch_major = MajorRegionFactory.create(
        prefix="CH-4",
        country="CH",
        name_en="Wallis",
        name_native="Valais",
    )
    sub_ch = SubRegionFactory.create(prefix="CH-41", major=ch_major)
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Valais",
        slug="ch-4115",
        boundary=boundary,
        subregion=sub_ch,
    )

    at_major = MajorRegionFactory.create(
        prefix="AT-02",
        country="AT",
        name_en="Styria",
        name_native="Steiermark",
    )
    sub_at = SubRegionFactory.create(prefix="AT-02-01", major=at_major)
    MicroRegionFactory.create(
        region_id="AT-02-01-01",
        name="AT-02-01-01",
        slug="at-02-01-01",
        boundary=boundary,
        subregion=sub_at,
    )

    client = Client()
    ch_data = client.get(reverse("api:regions_geojson") + "?country=ch").json()
    by_id = {f["properties"]["id"]: f for f in ch_data["features"]}
    assert by_id["CH-4115"]["properties"]["major_name"] == "Wallis"

    # AT region: name_en present → used as major_name.
    at_data = client.get(reverse("api:regions_geojson") + "?country=at").json()
    at_by_id = {f["properties"]["id"]: f for f in at_data["features"]}
    assert at_by_id["AT-02-01-01"]["properties"]["major_name"] == "Styria"


@pytest.mark.django_db
def test_regions_geojson_features_carry_slug() -> None:
    """SNOW-314: each feature's properties include the region's name_slug value.

    The slug is used by the client-side readout chip to build the bulletin URL
    without a second lookup (``/ch-4115/martigny-verbier/<date>/``).
    ``name_slug`` is a ``@property`` derived from ``slugify(name)`` so the
    factory receives ``name`` and we derive the expected slug value from it.
    """
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    ch_major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    sub = SubRegionFactory.create(prefix="CH-41", major=ch_major)
    # slugify("Martigny Verbier") == "martigny-verbier"
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Martigny Verbier",
        boundary=boundary,
        subregion=sub,
    )
    client = Client()
    response = client.get(reverse("api:regions_geojson") + "?country=ch")
    assert response.status_code == 200
    data = response.json()
    by_id = {f["properties"]["id"]: f for f in data["features"]}
    assert "slug" in by_id["CH-4115"]["properties"]
    assert by_id["CH-4115"]["properties"]["slug"] == "martigny-verbier"


# ---------------------------------------------------------------------------
# major-regions.geojson / sub-regions.geojson  (SNOW-59)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_major_regions_geojson_returns_feature_collection() -> None:
    """L1 majors with a non-null boundary become Features; null boundary skipped.

    Uses AT prefix to avoid collisions with any pre-loaded CH-* fixture rows.
    """
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    MajorRegionFactory.create(
        prefix="AT-1", country="AT", name_en="Vorarlberg", boundary=boundary
    )
    # Major without boundary — should be skipped.
    MajorRegionFactory.create(
        prefix="AT-2", country="AT", name_en="Tirol", boundary=None
    )

    client = Client()
    response = client.get(reverse("api:major_regions_geojson") + "?country=at")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "FeatureCollection"

    by_prefix = {f["properties"]["prefix"]: f for f in data["features"]}
    assert "AT-1" in by_prefix
    assert "AT-2" not in by_prefix  # boundary=None → skipped

    feature = by_prefix["AT-1"]
    assert feature["type"] == "Feature"
    assert feature["properties"]["prefix"] == "AT-1"
    assert feature["properties"]["name_en"] == "Vorarlberg"
    assert feature["properties"]["country"] == "AT"
    assert feature["geometry"] == boundary


@pytest.mark.django_db
def test_sub_regions_geojson_returns_feature_collection() -> None:
    """L2 subs with a non-null boundary become Features; null boundary skipped."""
    major = MajorRegionFactory.create(prefix="AT-1", country="AT", name_en="Vorarlberg")
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    SubRegionFactory.create(
        prefix="AT-11",
        major=major,
        name_en="Vorarlberg North",
        boundary=boundary,
    )
    SubRegionFactory.create(
        prefix="AT-12",
        major=major,
        name_en="Vorarlberg South",
        boundary=None,
    )

    client = Client()
    response = client.get(reverse("api:sub_regions_geojson") + "?country=at")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "FeatureCollection"

    by_prefix = {f["properties"]["prefix"]: f for f in data["features"]}
    assert "AT-11" in by_prefix
    assert "AT-12" not in by_prefix  # boundary=None → skipped

    feature = by_prefix["AT-11"]
    assert feature["properties"]["prefix"] == "AT-11"
    assert feature["properties"]["name_en"] == "Vorarlberg North"
    assert feature["properties"]["country"] == "AT"
    assert feature["geometry"] == boundary


# ---------------------------------------------------------------------------
# properties.download + region-basemap-tiles  (SNOW-521 — MicroRegion only)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_major_regions_geojson_never_carries_download() -> None:
    """MajorRegion (L1) has no basemap_download field — the key never appears."""
    MajorRegionFactory.create(
        prefix="AT-1",
        country="AT",
        boundary={
            "type": "Polygon",
            "coordinates": [
                [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
            ],
        },
    )
    client = Client()
    data = client.get(reverse("api:major_regions_geojson") + "?country=at").json()
    feature = next(f for f in data["features"] if f["properties"]["prefix"] == "AT-1")
    assert "download" not in feature["properties"]


@pytest.mark.django_db
def test_sub_regions_geojson_never_carries_download() -> None:
    """SubRegion (L2) has no basemap_download field — the key never appears."""
    major = MajorRegionFactory.create(prefix="AT-1", country="AT")
    SubRegionFactory.create(
        prefix="AT-11",
        major=major,
        boundary={
            "type": "Polygon",
            "coordinates": [
                [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
            ],
        },
    )
    client = Client()
    data = client.get(reverse("api:sub_regions_geojson") + "?country=at").json()
    feature = next(f for f in data["features"] if f["properties"]["prefix"] == "AT-11")
    assert "download" not in feature["properties"]


@pytest.mark.django_db
def test_regions_geojson_download_is_flat_micro_summary() -> None:
    """L4's properties.download is the region's own flat summary, no tier nesting."""
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    micro_blob = build_blob([6.9, 46.4, 7.0, 46.5], *MICRO_BAND)
    MicroRegionFactory.create(
        region_id="CH-4115",
        boundary=boundary,
        basemap_download=micro_blob,
    )

    client = Client()
    data = client.get(reverse("api:regions_geojson") + "?country=ch").json()
    feature = next(f for f in data["features"] if f["properties"]["id"] == "CH-4115")
    download = feature["properties"]["download"]
    assert download == blob_summary(micro_blob)
    assert "z" not in download
    assert "micro" not in download
    assert "minor" not in download
    assert "major" not in download


@pytest.mark.django_db
def test_regions_geojson_omits_download_when_unset() -> None:
    """The download key is omitted when the region has no computed blob."""
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    MicroRegionFactory.create(
        region_id="CH-4115",
        boundary=boundary,
        basemap_download=None,
    )

    client = Client()
    data = client.get(reverse("api:regions_geojson") + "?country=ch").json()
    feature = next(f for f in data["features"] if f["properties"]["id"] == "CH-4115")
    assert "download" not in feature["properties"]


@pytest.mark.django_db
def test_region_basemap_tiles_returns_full_blob_for_micro_region() -> None:
    """?id=<region_id> resolves a MicroRegion and returns its full blob incl. z."""
    blob = build_blob([6.9, 46.4, 7.0, 46.5], *MICRO_BAND)
    MicroRegionFactory.create(region_id="CH-4115", basemap_download=blob)

    client = Client()
    response = client.get(reverse("api:region_basemap_tiles") + "?id=CH-4115")
    assert response.status_code == 200
    assert response.json() == blob
    assert "z" in response.json()


@pytest.mark.django_db
def test_region_basemap_tiles_unknown_id_returns_404() -> None:
    """An id matching no MicroRegion 404s."""
    client = Client()
    response = client.get(reverse("api:region_basemap_tiles") + "?id=ZZ-9999")
    assert response.status_code == 404


@pytest.mark.django_db
def test_region_basemap_tiles_does_not_resolve_major_or_sub_region_prefix() -> None:
    """A MajorRegion/SubRegion prefix never resolves — MicroRegion only."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    SubRegionFactory.create(prefix="CH-41", major=major)

    client = Client()
    assert (
        client.get(reverse("api:region_basemap_tiles") + "?id=CH-4").status_code == 404
    )
    assert (
        client.get(reverse("api:region_basemap_tiles") + "?id=CH-41").status_code == 404
    )


@pytest.mark.django_db
def test_region_basemap_tiles_known_region_without_blob_returns_404() -> None:
    """A known region_id whose basemap_download hasn't been computed 404s."""
    MicroRegionFactory.create(region_id="CH-4115", basemap_download=None)
    client = Client()
    response = client.get(reverse("api:region_basemap_tiles") + "?id=CH-4115")
    assert response.status_code == 404


def test_region_basemap_tiles_missing_id_returns_400() -> None:
    """A request with no ?id= param 400s rather than 404ing or 500ing."""
    client = Client()
    response = client.get(reverse("api:region_basemap_tiles"))
    assert response.status_code == 400


@pytest.mark.django_db
def test_region_basemap_tiles_cache_control_header() -> None:
    """The endpoint sets the same public/24h Cache-Control as the geojson feeds."""
    blob = build_blob([6.9, 46.4, 7.0, 46.5], *MICRO_BAND)
    MicroRegionFactory.create(region_id="CH-4115", basemap_download=blob)
    client = Client()
    response = client.get(reverse("api:region_basemap_tiles") + "?id=CH-4115")
    assert "public" in response["Cache-Control"]
    assert "max-age=86400" in response["Cache-Control"]


# ---------------------------------------------------------------------------
# region-summary  (SNOW-174 pivot: tooltip, no bulletin dependency)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_region_summary_returns_html_for_known_region() -> None:
    """200 response with a single ``html`` key containing the region name."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Martigny – Verbier",
        slug="ch-4115",
        subregion=sub,
    )
    client = Client()
    response = client.get(reverse("api:region_summary", args=["ch-4115"]))
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"html", "level"}
    assert "Martigny" in data["html"]


@pytest.mark.django_db
def test_region_summary_includes_geographic_breadcrumb() -> None:
    """The tooltip HTML includes three breadcrumb labels (country › L1 › L2) in order.

    The region name moved to the header row alongside the chip and is no
    longer the trailing breadcrumb entry.
    """
    major = MajorRegionFactory.create(
        prefix="CH-4", country="CH", name_native="Wallis", name_en="Valais"
    )
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Bas-Valais", name_en="Lower Valais"
    )
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Martigny – Verbier",
        slug="ch-4115",
        subregion=sub,
    )
    client = Client()
    response = client.get(reverse("api:region_summary", args=["ch-4115"]))
    assert response.status_code == 200
    html = response.json()["html"]
    # Country name is the English form from COUNTRY_NAMES, not the ISO code.
    assert "Switzerland" in html
    # English names for L1 and L2.
    assert "Valais" in html
    assert "Lower Valais" in html
    # Region name still present in the header <h2>.
    assert "Martigny" in html
    # French/German native names must not appear — the template prefers name_en.
    assert "Wallis" not in html
    assert "Bas-Valais" not in html
    # Chevron separator.
    assert "›" in html
    # The breadcrumb paragraph carries three labels; check order within it.
    breadcrumb_start = html.index("region-tooltip-breadcrumb")
    breadcrumb = html[breadcrumb_start:]
    assert breadcrumb.index("Switzerland") < breadcrumb.index("Valais")
    assert breadcrumb.index("Valais") < breadcrumb.index("Lower Valais")
    # Region name must not be inside the breadcrumb paragraph.
    bc_para_end = breadcrumb.index("</p>")
    bc_para = breadcrumb[:bc_para_end]
    assert "Martigny" not in bc_para


@pytest.mark.django_db
def test_region_summary_unknown_region_returns_404() -> None:
    """An unknown region_id returns 404."""
    client = Client()
    response = client.get(reverse("api:region_summary", args=["xx-9999"]))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# resort_popup (SNOW-499)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resort_popup_returns_html_for_known_resort() -> None:
    """200 response with a single html key containing the resort/region names."""
    region = MicroRegionFactory.create(name="Martigny – Verbier")
    resort = ResortFactory.create(
        name="Verbier", region=region, latitude=46.1, longitude=7.4
    )

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"html"}
    assert "Verbier" in data["html"]
    assert "Martigny" in data["html"]


@pytest.mark.django_db
def test_resort_popup_cta_links_to_resort_page() -> None:
    """SNOW-504: the "View resort →" CTA links to the resort's own page."""
    region = MicroRegionFactory.create(name="Martigny – Verbier")
    resort = ResortFactory.create(
        name="Verbier", region=region, latitude=46.1, longitude=7.4
    )

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert "View resort" in html
    assert resort.get_absolute_url() in html
    assert region.get_absolute_url() not in html


@pytest.mark.django_db
def test_resort_popup_unknown_resort_returns_404() -> None:
    """An unknown resort_id returns 404."""
    client = Client()
    response = client.get(reverse("api:resort_popup", args=[999999]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_resort_popup_anonymous_shows_signin_cta_no_star() -> None:
    """An anonymous visitor sees a sign-in CTA and no favourite star."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert "data-resort-star" not in html
    assert "Sign in" in html


@pytest.mark.django_db
def test_resort_popup_authenticated_not_favourited_shows_star() -> None:
    """An authenticated, not-yet-favouriting user sees the star, unfavourited."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)
    user = UserFactory.create()

    client = Client()
    client.force_login(user)
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert "data-resort-star" in html
    assert 'data-favourited="false"' in html


@pytest.mark.django_db
def test_resort_popup_authenticated_already_favourited_shows_saved_state() -> None:
    """An already-favourited resort shows data-favourited=true + the favourite's uuid."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)
    user = UserFactory.create()
    favourite = FavouriteFactory.create(
        user=user,
        resort=resort,
        latitude=46.1,
        longitude=7.4,
    )

    client = Client()
    client.force_login(user)
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert 'data-favourited="true"' in html
    assert str(favourite.uuid) in html


@pytest.mark.django_db
def test_resort_popup_not_cached() -> None:
    """The response never carries a public/shared Cache-Control (per-user star)."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert "public" not in response.get("Cache-Control", "")


_PLACEHOLDER_CLASS = "border-dashed"


@pytest.mark.django_db
def test_resort_popup_populated_metadata_renders_values() -> None:
    """SNOW-501: a fully-populated resort renders every metadata value, no placeholders."""
    resort = ResortFactory.create(
        name="Verbier",
        latitude=46.1,
        longitude=7.4,
        operator_name="Téléverbier SA",
        website="https://www.televerbier.ch",
        num_lifts=25,
        num_runs=90,
        total_piste_km=88.5,
        base_elevation_m=1500,
        top_elevation_m=3330,
        typical_season_open="12-01",
        typical_season_close="04-30",
    )

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert "Téléverbier SA" in html
    assert "https://www.televerbier.ch" in html
    assert "25" in html
    assert "90" in html
    assert "88.5" in html
    assert "1500" in html
    assert "3330" in html
    assert "1 Dec" in html
    assert "30 Apr" in html
    assert _PLACEHOLDER_CLASS not in html


@pytest.mark.django_db
def test_resort_popup_blank_metadata_anonymous_shows_em_dash_placeholder() -> None:
    """Blank fields for an anonymous visitor render an em-dash placeholder, not a hint."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert _PLACEHOLDER_CLASS in html
    assert "&mdash;" in html
    assert "Add operator name" not in html
    assert "Add website" not in html


@pytest.mark.django_db
def test_resort_popup_blank_metadata_staff_shows_curation_hints() -> None:
    """Blank fields for a staff user render explicit "Add <field>" curation hints."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)
    staff_user = UserFactory.create(is_staff=True)

    client = Client()
    client.force_login(staff_user)
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert "Add operator name" in html
    assert "Add website" in html
    assert "Add lift count" in html
    assert "Add run count" in html
    assert "Add piste length" in html
    assert "Add elevation range" in html
    assert "Add typical season" in html


@pytest.mark.django_db
def test_resort_popup_blank_metadata_non_staff_shows_em_dash_placeholder() -> None:
    """Blank fields for a non-staff authenticated user show em-dash, not a staff hint."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)
    user = UserFactory.create(is_staff=False)

    client = Client()
    client.force_login(user)
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert _PLACEHOLDER_CLASS in html
    assert "&mdash;" in html
    assert "Add operator name" not in html


@pytest.mark.django_db
def test_resort_popup_zero_lifts_renders_zero_not_placeholder() -> None:
    """SNOW-501 review: a real stored 0 is a value, not a blank field."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4, num_lifts=0)

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert ">0<" in html
    assert "Add lift count" not in html


@pytest.mark.django_db
def test_resort_popup_unparseable_season_half_shows_placeholder() -> None:
    """SNOW-501 review: an unparseable season half falls to the placeholder.

    ``MONTH_DAY_VALIDATOR`` permits "02-29" (there is no 30-Feb check at the
    model layer) but ``month_day`` can't resolve it against the fixed
    non-leap anchor year — the season row must show the placeholder rather
    than a degenerate half-range like "-30 Apr".
    """
    resort = ResortFactory.create(
        latitude=46.1,
        longitude=7.4,
        typical_season_open="02-29",
        typical_season_close="04-30",
    )

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    assert response.status_code == 200
    html = response.json()["html"]
    assert "30 Apr" not in html
    assert _PLACEHOLDER_CLASS in html


@pytest.mark.django_db
def test_region_summary_emits_freshness_headers() -> None:
    """SNOW-370: tooltip response carries the three freshness headers."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    sub = SubRegionFactory.create(prefix="CH-41", major=major)
    region = MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=sub
    )
    RegionDayRatingFactory.create(
        region=region,
        date=_RATINGS_DAY_ONE,
        max_rating=RegionDayRating.Rating.CONSIDERABLE,
    )
    response = Client().get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    assert "X-Data-Generated-At" in response
    assert "X-Data-Max-Age" in response
    assert "X-Data-Unsafe-After" in response


@pytest.mark.django_db
def test_region_summary_query_count() -> None:
    """The tooltip view issues at most 2 DB queries when the coverage cache is warm.

    SNOW-54: covered_region_ids() adds one query on a cold cache, so we
    pre-warm it before the measured block to keep the assertion deterministic.
    The warm-cache path issues zero additional queries.
    """
    from apps.bulletins.services.coverage import covered_region_ids

    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", subregion=sub)

    # Pre-warm the coverage cache so the view's covered_region_ids() call is free.
    cache.clear()
    covered_region_ids()

    client = Client()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("api:region_summary", args=["ch-4115"]))
    assert response.status_code == 200
    # Queries: region + subregion + major join (1), RegionDayRating lookup (1).
    # The resorts prefetch was dropped in SNOW-174 when the resort list was removed.
    # Coverage cache is pre-warmed, so covered_region_ids() adds 0 queries.
    assert len(ctx.captured_queries) <= 2


@pytest.mark.django_db
def test_region_summary_accepts_date_query_param() -> None:
    """?d=YYYY-MM-DD is honoured and the chip reflects that day's rating."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    region = MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=sub
    )
    target_date = dt.date(2026, 1, 15)
    RegionDayRatingFactory.create(
        region=region,
        date=target_date,
        max_rating=RegionDayRating.Rating.CONSIDERABLE,
    )

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # The chip should carry the considerable rating.
    assert 'data-level="considerable"' in html
    # Digit inside the chip.
    assert ">3<" in html


@pytest.mark.django_db
def test_region_summary_rejects_bad_date() -> None:
    """A malformed ?d= value returns 400 with {"error": "bad_date"}."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", subregion=sub)

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=not-a-date"
    )
    assert response.status_code == 400
    assert response.json() == {"error": "bad_date"}


@pytest.mark.django_db
def test_region_summary_includes_headline_rating_chip() -> None:
    """The chip is a presentational .danger-tile[data-level] span — no link wrapper."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    region = MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=sub
    )
    target_date = dt.date(2026, 1, 15)
    RegionDayRatingFactory.create(
        region=region,
        date=target_date,
        max_rating=RegionDayRating.Rating.HIGH,
    )

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # The chip carries the test id.
    assert 'data-testid="region-tooltip-rating-chip"' in html
    # The chip carries the expected data-level.
    assert 'data-level="high"' in html
    # The digit for high is 4.
    assert ">4<" in html
    # The chip is not wrapped in an anchor — only the bulletin CTA below
    # is a link (and it points at the dated bulletin URL).
    assert 'data-testid="region-tooltip-rating-link"' not in html
    assert "/ch-4115/" in html
    assert "2026-01-15" in html


@pytest.mark.django_db
def test_region_summary_no_rating_shows_fallback_icon() -> None:
    """A region with no RegionDayRating row shows the favicon icon, not a chip."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", subregion=sub)

    client = Client()
    # Use a date far in the past where no rating exists.
    response = client.get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2000-01-01"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # No danger-tile chip rendered when there is no bulletin.
    assert "danger-tile" not in html
    assert 'data-level="no_rating"' not in html
    # Fallback icon present instead.
    assert "favicon.svg" in html


@pytest.mark.django_db
def test_region_summary_breadcrumb_uses_english_names() -> None:
    """Breadcrumb renders three levels (country › L1 › L2) in English.

    The region name is no longer the trailing breadcrumb entry — it moved
    to the inline header row alongside the chip.
    """
    major = MajorRegionFactory.create(
        prefix="CH-4", country="CH", name_native="Wallis", name_en="Valais"
    )
    sub = SubRegionFactory.create(
        prefix="CH-41",
        major=major,
        name_native="Bas-Valais",
        name_en="Lower Valais",
    )
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Martigny-Verbier",
        slug="ch-4115",
        subregion=sub,
    )

    client = Client()
    response = client.get(reverse("api:region_summary", args=["ch-4115"]))
    assert response.status_code == 200
    html = response.json()["html"]

    # Three breadcrumb labels present.
    assert "Switzerland" in html
    assert "Valais" in html
    assert "Lower Valais" in html
    # Region name appears in the header row, not the breadcrumb.
    assert "Martigny-Verbier" in html

    # Native-language names must not appear.
    assert "Wallis" not in html
    assert "Bas-Valais" not in html

    # Breadcrumb order: Switzerland › Valais › Lower Valais (no trailing region).
    breadcrumb_start = html.index("region-tooltip-breadcrumb")
    # The breadcrumb paragraph ends before the header div ends; extract just
    # the breadcrumb paragraph text for ordering assertions.
    bc = html[breadcrumb_start:]
    assert bc.index("Switzerland") < bc.index("Valais")
    assert bc.index("Valais") < bc.index("Lower Valais")
    # The region name must NOT appear inside the breadcrumb paragraph.
    # It lives in the header row (before the breadcrumb), so extract the
    # breadcrumb up to the closing </p> and verify the region name is absent.
    bc_para_end = bc.index("</p>")
    bc_para = bc[:bc_para_end]
    assert "Martigny-Verbier" not in bc_para


@pytest.mark.django_db
def test_region_summary_includes_level_key() -> None:
    """JSON response carries a ``level`` key matching the day's max_rating."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    region = MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=sub
    )
    target_date = dt.date(2026, 1, 15)
    RegionDayRatingFactory.create(
        region=region,
        date=target_date,
        max_rating=RegionDayRating.Rating.CONSIDERABLE,
    )

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    data = response.json()
    assert "level" in data
    assert data["level"] == "considerable"


@pytest.mark.django_db
def test_region_summary_level_key_falls_back_to_no_rating() -> None:
    """When no RegionDayRating exists, ``level`` is ``'no_rating'``."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", subregion=sub)

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2000-01-01"
    )
    assert response.status_code == 200
    assert response.json()["level"] == "no_rating"


@pytest.mark.django_db
def test_region_summary_cta_label_includes_date() -> None:
    """The bulletin CTA carries the displayed date in its label when a bulletin exists."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    region = MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=sub
    )
    target_date = dt.date(2026, 1, 15)
    RegionDayRatingFactory.create(
        region=region,
        date=target_date,
        max_rating=RegionDayRating.Rating.MODERATE,
    )

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # CTA link is present with the expected test id.
    assert 'data-testid="region-tooltip-bulletin-link"' in html
    # CTA label carries the date — single source of truth for the displayed
    # day; no separate caption line.
    assert "Open bulletin for" in html
    # Date formatted as "j M Y" → "15 Jan 2026" (M = 3-letter month, no period;
    # SNOW-318 switched from "j N Y" so the format matches the JS scrub relabel).
    assert "15 Jan" in html
    assert "2026" in html
    # The earlier separate "Showing …" caption is gone.
    assert "Showing" not in html


# ---------------------------------------------------------------------------
# Content type
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoints_return_json_content_type() -> None:
    """All map-page endpoints advertise application/json."""
    client = Client()
    for name in (
        "api:ratings",
        "api:resorts_by_region",
    ):
        response = client.get(reverse(name))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        json.loads(response.content)

    # GeoJSON endpoints require ?country= param.
    for name in (
        "api:regions_geojson",
        "api:major_regions_geojson",
        "api:sub_regions_geojson",
    ):
        response = client.get(reverse(name) + "?country=ch")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        json.loads(response.content)


# ---------------------------------------------------------------------------
# French regions (SNOW-179)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_regions_geojson_includes_fr_regions() -> None:
    """French L4 micro-regions appear in /api/regions.geojson?country=fr.

    The factory's auto-derived SubRegion prefix from ``region_id[:5]`` would
    produce ``"FR-68"`` which is not a valid SubRegion prefix. We pass
    ``subregion=`` explicitly so the FK points to a properly-prefixed row.
    """
    boundary = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0.5, 42.8], [0.7, 42.8], [0.7, 43.0], [0.5, 43.0], [0.5, 42.8]]]
        ],
    }
    major = MajorRegionFactory.create(prefix="FR-3", country="FR", name_en="Pyrenees")
    sub = SubRegionFactory.create(prefix="FR-3A", major=major, name_en="Pyrenees")
    MicroRegionFactory.create(
        region_id="FR-68",
        name="Louchonnais",
        slug="fr-68",
        subregion=sub,
        boundary=boundary,
    )

    client = Client()
    response = client.get(reverse("api:regions_geojson") + "?country=fr")
    assert response.status_code == 200
    data = response.json()

    ids = {f["properties"]["id"] for f in data["features"]}
    assert "FR-68" in ids

    fr_feature = next(f for f in data["features"] if f["properties"]["id"] == "FR-68")
    assert fr_feature["properties"]["name"] == "Louchonnais"
    assert fr_feature["properties"]["country"] == "FR"
    assert fr_feature["geometry"] == boundary


# ---------------------------------------------------------------------------
# display_on_map filtering (SNOW-196)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_regions_geojson_excludes_hidden_major_regions() -> None:
    """L4 micro-regions under a hidden major region are excluded from the L4 endpoint.

    A MajorRegion with display_on_map=False must not appear in
    /api/regions.geojson even when its child MicroRegion has a non-null boundary.
    A visible MajorRegion (display_on_map=True) with the same country must
    still be included.
    """
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[0.5, 42.8], [0.7, 42.8], [0.7, 43.0], [0.5, 43.0], [0.5, 42.8]]
        ],
    }

    # Hidden major region (simulates FR-3 Pyrenees).
    hidden_major = MajorRegionFactory.create(
        prefix="FR-3", country="FR", name_en="Pyrenees", display_on_map=False
    )
    hidden_sub = SubRegionFactory.create(
        prefix="FR-3A", major=hidden_major, name_en="Pyrenees"
    )
    MicroRegionFactory.create(
        region_id="FR-68",
        name="Louchonnais",
        slug="fr-68",
        subregion=hidden_sub,
        boundary=boundary,
    )

    # Visible major region (simulates FR-1 Alpes du Nord).
    visible_major = MajorRegionFactory.create(
        prefix="FR-1", country="FR", name_en="Alpes du Nord", display_on_map=True
    )
    visible_sub = SubRegionFactory.create(
        prefix="FR-1A", major=visible_major, name_en="Alpes du Nord"
    )
    MicroRegionFactory.create(
        region_id="FR-01",
        name="Chartreuse",
        slug="fr-01",
        subregion=visible_sub,
        boundary=boundary,
    )

    client = Client()
    response = client.get(reverse("api:regions_geojson") + "?country=fr")
    assert response.status_code == 200
    data = response.json()

    ids = {f["properties"]["id"] for f in data["features"]}
    assert "FR-01" in ids, "Visible micro-region must be included"
    assert "FR-68" not in ids, "Micro-region under hidden major must be excluded"


@pytest.mark.django_db
def test_major_regions_geojson_excludes_hidden_major_regions() -> None:
    """A MajorRegion with display_on_map=False is excluded from the L1 endpoint.

    display_on_map=True with a boundary must be included; display_on_map=False
    must not appear even when boundary is set.
    """
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[0.5, 42.8], [0.7, 42.8], [0.7, 43.0], [0.5, 43.0], [0.5, 42.8]]
        ],
    }

    MajorRegionFactory.create(
        prefix="FR-3",
        country="FR",
        name_en="Pyrenees",
        boundary=boundary,
        display_on_map=False,
    )
    MajorRegionFactory.create(
        prefix="FR-1",
        country="FR",
        name_en="Alpes du Nord",
        boundary=boundary,
        display_on_map=True,
    )

    client = Client()
    response = client.get(reverse("api:major_regions_geojson") + "?country=fr")
    assert response.status_code == 200
    data = response.json()

    by_prefix = {f["properties"]["prefix"]: f for f in data["features"]}
    assert "FR-1" in by_prefix, "Visible major region must be included"
    assert "FR-3" not in by_prefix, "Hidden major region must be excluded"


@pytest.mark.django_db
def test_sub_regions_geojson_excludes_hidden_major_regions() -> None:
    """A SubRegion whose parent MajorRegion has display_on_map=False is excluded.

    The L2 endpoint filters via major__display_on_map=True, so sub-regions
    under a hidden major must not appear even with a non-null boundary.
    """
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[0.5, 42.8], [0.7, 42.8], [0.7, 43.0], [0.5, 43.0], [0.5, 42.8]]
        ],
    }

    hidden_major = MajorRegionFactory.create(
        prefix="FR-3", country="FR", name_en="Pyrenees", display_on_map=False
    )
    SubRegionFactory.create(
        prefix="FR-3A", major=hidden_major, name_en="Pyrenees", boundary=boundary
    )

    visible_major = MajorRegionFactory.create(
        prefix="FR-1", country="FR", name_en="Alpes du Nord", display_on_map=True
    )
    SubRegionFactory.create(
        prefix="FR-1A", major=visible_major, name_en="Alpes du Nord", boundary=boundary
    )

    client = Client()
    response = client.get(reverse("api:sub_regions_geojson") + "?country=fr")
    assert response.status_code == 200
    data = response.json()

    by_prefix = {f["properties"]["prefix"]: f for f in data["features"]}
    assert "FR-1A" in by_prefix, "Sub-region under visible major must be included"
    assert "FR-3A" not in by_prefix, "Sub-region under hidden major must be excluded"


# ---------------------------------------------------------------------------
# Country-aware GeoJSON endpoints (SNOW-172)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_regions_geojson_rejects_missing_country() -> None:
    """Omitting ?country= returns 400."""
    client = Client()
    response = client.get(reverse("api:regions_geojson"))
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_country"


@pytest.mark.django_db
def test_regions_geojson_rejects_unknown_country() -> None:
    """An unrecognised ?country= value returns 400."""
    client = Client()
    response = client.get(reverse("api:regions_geojson") + "?country=zz")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_country"


@pytest.mark.django_db
def test_regions_geojson_filters_by_country() -> None:
    """?country=ch returns only CH features; FR features are excluded."""
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    ch_major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    ch_sub = SubRegionFactory.create(prefix="CH-41", major=ch_major)
    MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=ch_sub, boundary=boundary
    )

    fr_major = MajorRegionFactory.create(prefix="FR-3", country="FR")
    fr_sub = SubRegionFactory.create(prefix="FR-3A", major=fr_major)
    MicroRegionFactory.create(
        region_id="FR-68", slug="fr-68", subregion=fr_sub, boundary=boundary
    )

    client = Client()
    ch_data = client.get(reverse("api:regions_geojson") + "?country=ch").json()
    ch_ids = {f["properties"]["id"] for f in ch_data["features"]}
    assert "CH-4115" in ch_ids
    assert "FR-68" not in ch_ids

    fr_data = client.get(reverse("api:regions_geojson") + "?country=fr").json()
    fr_ids = {f["properties"]["id"] for f in fr_data["features"]}
    assert "FR-68" in fr_ids
    assert "CH-4115" not in fr_ids


@pytest.mark.django_db
def test_major_regions_geojson_rejects_unknown_country() -> None:
    """An unrecognised ?country= value returns 400 for major regions."""
    client = Client()
    response = client.get(reverse("api:major_regions_geojson") + "?country=zz")
    assert response.status_code == 400


@pytest.mark.django_db
def test_sub_regions_geojson_rejects_unknown_country() -> None:
    """An unrecognised ?country= value returns 400 for sub-regions."""
    client = Client()
    response = client.get(reverse("api:sub_regions_geojson") + "?country=zz")
    assert response.status_code == 400


@pytest.mark.django_db
def test_regions_geojson_accepts_country_case_insensitive() -> None:
    """?country=CH and ?country=ch are treated identically."""
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", boundary=boundary)
    client = Client()
    r_lower = client.get(reverse("api:regions_geojson") + "?country=ch")
    r_upper = client.get(reverse("api:regions_geojson") + "?country=CH")
    assert r_lower.status_code == 200
    assert r_upper.status_code == 200
    assert r_lower.json() == r_upper.json()


@pytest.mark.django_db
def test_regions_geojson_sets_cache_control() -> None:
    """The Cache-Control header is set on region GeoJSON responses."""
    client = Client()
    response = client.get(reverse("api:regions_geojson") + "?country=ch")
    assert response.status_code == 200
    assert "max-age=86400" in response.get("Cache-Control", "")


@pytest.mark.django_db
@pytest.mark.parametrize(
    "url_name",
    ["api:regions_geojson", "api:major_regions_geojson", "api:sub_regions_geojson"],
)
def test_geojson_endpoints_have_public_cache_headers(url_name: str) -> None:
    """GeoJSON endpoints carry public Cache-Control and Vary: Accept-Encoding.

    ``public`` makes the response eligible for shared caches (CDNs, proxies).
    ``Vary: Accept-Encoding`` prevents Django's SessionMiddleware from
    appending ``Vary: Cookie``, which would prevent cross-navigation caching
    in most browsers even for identical requests.
    """
    client = Client()
    url = reverse(url_name) + "?country=ch"
    response = client.get(url)
    assert response.status_code == 200
    cache_control = response.get("Cache-Control", "")
    assert "public" in cache_control, (
        f"Expected 'public' in Cache-Control; got: {cache_control!r}"
    )
    assert "max-age=86400" in cache_control, (
        f"Expected 'max-age=86400' in Cache-Control; got: {cache_control!r}"
    )
    vary = response.get("Vary", "")
    assert "Cookie" not in vary, (
        f"Vary: Cookie must not be set on GeoJSON endpoints; got Vary: {vary!r}"
    )
    assert "Accept-Encoding" in vary, (
        f"Expected 'Accept-Encoding' in Vary; got: {vary!r}"
    )


@pytest.mark.django_db
def test_regions_geojson_query_count() -> None:
    """Regions GeoJSON for a country issues a bounded number of DB queries."""
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    ch_major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    ch_sub = SubRegionFactory.create(prefix="CH-41", major=ch_major)
    MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=ch_sub, boundary=boundary
    )
    MicroRegionFactory.create(
        region_id="CH-4116", slug="ch-4116", subregion=ch_sub, boundary=boundary
    )

    # SNOW-54: pre-warm the coverage cache so the view's covered_region_ids()
    # call is served from cache and the captured query count reflects only the
    # region SELECT (the autouse clear_ratings_cache fixture empties it first).
    from apps.bulletins.services.coverage import covered_region_ids

    covered_region_ids()

    client = Client()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("api:regions_geojson") + "?country=ch")
    assert response.status_code == 200
    # One SELECT with select_related joins — single query regardless of feature
    # count; coverage is pre-warmed above so it adds none.
    assert len(ctx.captured_queries) <= 1


# ---------------------------------------------------------------------------
# region-summary — no-bulletin branch (SNOW-172)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_region_summary_no_bulletin_shows_favicon_icon() -> None:
    """When no RegionDayRating exists, the tooltip shows the favicon icon instead of a chip."""
    major = MajorRegionFactory.create(prefix="FR-3", country="FR", name_en="Pyrenees")
    sub = SubRegionFactory.create(prefix="FR-3A", major=major, name_en="Pyrenees")
    MicroRegionFactory.create(
        region_id="FR-68",
        name="Haute-Tarentaise",
        slug="fr-68",
        subregion=sub,
    )

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["fr-68"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # Favicon icon is present.
    assert "favicon.svg" in html
    # No danger-tile chip rendered.
    assert "danger-tile" not in html
    assert (
        "<span"
        not in html.split("region-tooltip-header")[1].split("region-tooltip-name")[0]
    )


@pytest.mark.django_db
def test_region_summary_no_bulletin_shows_no_bulletin_text() -> None:
    """A covered region with no rating on the queried date shows "No bulletin available" text.

    SNOW-54: the "no bulletin" path requires the region to be covered (at least
    one RegionDayRating exists for it).  A rating on a different date makes
    the region covered; querying an unrated date then exercises the
    "covered but no rating today" branch.
    """
    major = MajorRegionFactory.create(prefix="FR-3", country="FR", name_en="Pyrenees")
    sub = SubRegionFactory.create(prefix="FR-3A", major=major, name_en="Pyrenees")
    region = MicroRegionFactory.create(
        region_id="FR-68",
        name="Haute-Tarentaise",
        slug="fr-68",
        subregion=sub,
    )
    # Create a rating on a different date so the region is covered.
    cache.clear()
    RegionDayRatingFactory.create(
        region=region,
        date=dt.date(2026, 1, 10),
        max_rating=RegionDayRating.Rating.LOW,
    )

    client = Client()
    response = client.get(
        # Query a date with no rating → "no bulletin available" branch.
        reverse("api:region_summary", args=["fr-68"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # "No bulletin available" text is shown with the date.
    assert "No bulletin available for" in html
    assert "15 Jan" in html
    assert "2026" in html
    # The bulletin link is absent.
    assert "Open bulletin for" not in html


@pytest.mark.django_db
def test_region_summary_no_bulletin_testid_present() -> None:
    """A covered region with no rating on the queried date carries the no-bulletin test id.

    SNOW-54: making the region covered (a rating exists on another date) ensures
    the "no bulletin available" branch is taken, not the uncovered branch.
    """
    major = MajorRegionFactory.create(prefix="FR-3", country="FR", name_en="Pyrenees")
    sub = SubRegionFactory.create(prefix="FR-3A", major=major, name_en="Pyrenees")
    region = MicroRegionFactory.create(
        region_id="FR-68",
        name="Haute-Tarentaise",
        slug="fr-68",
        subregion=sub,
    )
    # Make the region covered so the "no bulletin" path applies.
    cache.clear()
    RegionDayRatingFactory.create(
        region=region,
        date=dt.date(2026, 1, 10),
        max_rating=RegionDayRating.Rating.LOW,
    )

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["fr-68"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    assert 'data-testid="region-tooltip-no-bulletin"' in html
    # The bulletin link test id must not be present.
    assert 'data-testid="region-tooltip-bulletin-link"' not in html


@pytest.mark.django_db
def test_region_summary_with_bulletin_renders_chip_and_link() -> None:
    """The existing bulletin path still renders the chip + CTA link (regression guard)."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    region = MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=sub
    )
    target_date = dt.date(2026, 1, 15)
    RegionDayRatingFactory.create(
        region=region,
        date=target_date,
        max_rating=RegionDayRating.Rating.CONSIDERABLE,
    )

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # Chip is present with the correct rating.
    assert "danger-tile" in html
    assert 'data-level="considerable"' in html
    # Bulletin CTA link is present.
    assert 'data-testid="region-tooltip-bulletin-link"' in html
    assert "Open bulletin for" in html
    # Fallback elements must not appear.
    assert "favicon.svg" not in html
    assert "No bulletin available" not in html


# ---------------------------------------------------------------------------
# SNOW-252 — peak semantics regression
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_ratings_choropleth_emits_peak_for_split_day() -> None:
    """
    SNOW-252 regression: choropleth (api:ratings) emits the peak (max_rating)
    for a two-period escalating day.

    Fixture: morning=moderate (2), afternoon=considerable (3) → peak=3.
    The choropleth must return 3, not 2.
    """
    ch_major = MajorRegionFactory.create(prefix="CH-4", country="CH")
    ch_sub = SubRegionFactory.create(prefix="CH-41", major=ch_major)
    region = MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=ch_sub
    )
    target_date = dt.date(2026, 3, 20)
    # Split day: morning=moderate, afternoon=considerable.
    RegionDayRatingFactory.create(
        region=region,
        date=target_date,
        min_rating=RegionDayRating.Rating.MODERATE,
        max_rating=RegionDayRating.Rating.CONSIDERABLE,
    )

    response = Client().get(reverse("api:ratings") + "?d=2026-03-20&country=ch")
    assert response.status_code == 200
    data = response.json()

    # considerable = 3 in _RATING_TO_INT; peak, not morning (moderate=2).
    assert data["2026-03-20"]["CH-4115"] == 3


@pytest.mark.django_db
def test_region_summary_tooltip_chip_shows_peak_on_split_day() -> None:
    """
    SNOW-252 regression: region_summary tooltip chip reflects max_rating (peak)
    for a two-period escalating day, not the morning (min_rating) level.

    Fixture: morning=moderate (2), afternoon=considerable (3) → chip shows 3.
    """
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    region = MicroRegionFactory.create(
        region_id="CH-4115", slug="ch-4115", subregion=sub
    )
    target_date = dt.date(2026, 3, 20)
    # Split day: morning=moderate, afternoon=considerable.
    RegionDayRatingFactory.create(
        region=region,
        date=target_date,
        min_rating=RegionDayRating.Rating.MODERATE,
        max_rating=RegionDayRating.Rating.CONSIDERABLE,
    )

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2026-03-20"
    )
    assert response.status_code == 200
    data = response.json()

    # ``level`` key in the JSON response must be the peak (considerable).
    assert data["level"] == "considerable"

    html = data["html"]
    # The chip data-level attribute must reflect the peak (considerable = 3).
    assert 'data-level="considerable"' in html
    # Digit 3 in the chip (considerable).
    assert ">3<" in html
    # Morning-only level (moderate = 2) must NOT appear as the chip's data-level.
    assert 'data-level="moderate"' not in html


# ---------------------------------------------------------------------------
# bulletin_groupings_geojson (SNOW-323)
# ---------------------------------------------------------------------------


def _make_groupings_fixture() -> None:
    """Create two BulletinGrouping rows spanning two dates and two countries.

    Row 1: date=2026-01-15, countries=['CH']
    Row 2: date=2026-01-16, countries=['AT', 'IT']

    Each row is paired with a RegionDayRating naming its bulletin as the
    ``source_bulletin`` for the same day. That pairing is not decoration: the
    endpoint only serves groupings whose bulletin won at least one region that
    day, which is how it drops the duplicate outline produced by the
    morning-of-X and previous-evening issues both targeting X. Ingest always
    writes both rows together (``upsert_bulletin`` calls
    ``apply_bulletin_day_ratings`` then ``compute_bulletin_grouping_boundary``),
    so a grouping with no matching rating is a state production cannot reach.
    """
    for target_date, countries, boundary in (
        (
            dt.date(2026, 1, 15),
            ["CH"],
            {
                "type": "Polygon",
                "coordinates": [
                    [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
                ],
            },
        ),
        (
            dt.date(2026, 1, 16),
            ["AT", "IT"],
            {
                "type": "Polygon",
                "coordinates": [
                    [
                        [10.0, 46.0],
                        [11.0, 46.0],
                        [11.0, 47.0],
                        [10.0, 47.0],
                        [10.0, 46.0],
                    ]
                ],
            },
        ),
    ):
        grouping = BulletinGroupingFactory.create(
            target_date=target_date,
            countries=countries,
            boundary=boundary,
        )
        RegionDayRatingFactory.create(
            date=target_date,
            source_bulletin=grouping.bulletin,
        )


def _groupings_url(date_key: str, country: str | None = None) -> str:
    """Build the single-date groupings URL for ``date_key`` (+ optional country)."""
    url = reverse("api:bulletin_groupings_geojson") + f"?d={date_key}"
    if country is not None:
        url += f"&country={country}"
    return url


@pytest.mark.django_db
def test_groupings_requires_date() -> None:
    """A request with no ?d= returns 400 — the whole-season scan is gone."""
    response = Client().get(reverse("api:bulletin_groupings_geojson"))
    assert response.status_code == 400
    assert response.json()["error"] == "date_required"


@pytest.mark.django_db
@pytest.mark.parametrize("bad_date", ["2026-13-01", "not-a-date", "20260115"])
def test_groupings_malformed_date_returns_400(bad_date: str) -> None:
    """A malformed ?d= value (incl. unhyphenated ISO) returns 400."""
    response = Client().get(
        reverse("api:bulletin_groupings_geojson") + f"?d={bad_date}"
    )
    assert response.status_code == 400
    assert "error" in response.json()


@pytest.mark.django_db
def test_groupings_empty_when_no_rows() -> None:
    """A date with no BulletinGrouping rows → an empty FeatureCollection."""
    response = Client().get(_groupings_url("2026-01-15"))
    assert response.status_code == 200
    assert response.json() == {"type": "FeatureCollection", "features": []}


@pytest.mark.django_db
def test_groupings_payload_shape() -> None:
    """Response is a single-date FeatureCollection with valid feature props."""
    _make_groupings_fixture()
    response = Client().get(_groupings_url("2026-01-15"))
    assert response.status_code == 200
    fc = response.json()

    assert fc["type"] == "FeatureCollection"
    assert isinstance(fc["features"], list)
    assert len(fc["features"]) == 1
    feature = fc["features"][0]
    assert feature["type"] == "Feature"
    props = feature["properties"]
    assert "bulletin_id" in props
    assert props["date"] == "2026-01-15"
    assert props["countries"] == ["CH"]


@pytest.mark.django_db
def test_groupings_returns_only_requested_date() -> None:
    """?d= scopes the response to that day — other dates' rows never appear."""
    _make_groupings_fixture()
    response = Client().get(_groupings_url("2026-01-16"))
    assert response.status_code == 200
    fc = response.json()
    assert len(fc["features"]) == 1
    assert fc["features"][0]["properties"]["date"] == "2026-01-16"


@pytest.mark.django_db
def test_groupings_excludes_the_superseded_issue_for_the_day() -> None:
    """Only the bulletin that won the day is drawn, not every one targeting it.

    SLF publishes twice daily, and both the morning-of-X issue and the
    previous evening's satisfy ``target_date == X``, so both hold a
    BulletinGrouping row for X. ``recompute_region_day`` picks one winner per
    region; the endpoint must follow that verdict rather than returning two
    overlapping outlines for the same terrain.
    """
    winner = BulletinGroupingFactory.create(
        target_date=dt.date(2026, 2, 10),
        countries=["CH"],
    )
    superseded = BulletinGroupingFactory.create(
        target_date=dt.date(2026, 2, 10),
        countries=["CH"],
    )
    # Only the winner is named as a source_bulletin for the day — exactly what
    # the day-rating arbitration leaves behind.
    RegionDayRatingFactory.create(
        date=dt.date(2026, 2, 10),
        source_bulletin=winner.bulletin,
    )

    response = Client().get(_groupings_url("2026-02-10"))
    assert response.status_code == 200
    features = response.json()["features"]

    assert len(features) == 1
    assert features[0]["properties"]["bulletin_id"] == winner.bulletin.bulletin_id
    served = {f["properties"]["bulletin_id"] for f in features}
    assert superseded.bulletin.bulletin_id not in served


@pytest.mark.django_db
def test_groupings_country_filter_ch() -> None:
    """?country=ch filters within the date — the AT/IT day yields no features."""
    _make_groupings_fixture()
    response = Client().get(_groupings_url("2026-01-16", country="ch"))
    assert response.status_code == 200
    assert response.json()["features"] == []


@pytest.mark.django_db
def test_groupings_country_filter_case_insensitive() -> None:
    """?country=CH and ?country=ch are treated identically."""
    _make_groupings_fixture()
    r_lower = Client().get(_groupings_url("2026-01-15", country="ch"))
    r_upper = Client().get(_groupings_url("2026-01-15", country="CH"))
    assert r_lower.status_code == 200
    assert r_upper.status_code == 200
    assert r_lower.json() == r_upper.json()


@pytest.mark.django_db
def test_groupings_country_filter_multi_country_row() -> None:
    """A grouping with ['AT', 'IT'] appears when ?country=at is requested."""
    _make_groupings_fixture()
    response = Client().get(_groupings_url("2026-01-16", country="at"))
    assert response.status_code == 200
    fc = response.json()
    assert len(fc["features"]) == 1
    assert fc["features"][0]["properties"]["countries"] == ["AT", "IT"]


@pytest.mark.django_db
def test_groupings_invalid_country_returns_400() -> None:
    """An unrecognised ?country= value returns 400."""
    response = Client().get(_groupings_url("2026-01-15", country="zz"))
    assert response.status_code == 400
    assert "error" in response.json()


@pytest.mark.django_db
def test_groupings_cache_control_header() -> None:
    """Response carries a public Cache-Control header."""
    response = Client().get(_groupings_url("2026-01-15"))
    assert response.status_code == 200
    cc = response.get("Cache-Control", "")
    assert "public" in cc
    assert "max-age=" in cc


def _stub_get_sources(
    latest: dt.date,
) -> Callable[[], dict[str, BulletinSource]]:
    """Build a ``get_sources``-shaped callable with one source's latest date fixed.

    Used to control ``apps.bulletins.services.settled.earliest_mutable_date()``'s
    result deterministically (see the SNOW-526 plan's sparse-fixture-DB risk
    note) without depending on real ``Bulletin`` rows or seed data.
    """

    def _get_sources() -> dict[str, BulletinSource]:
        return {
            "STUB": BulletinSource(
                name="STUB",
                pipeline_fn=lambda **kwargs: None,  # type: ignore[arg-type]
                latest_date_fn=lambda: latest,
                live_url_setting="SLF_API_BASE_URL",
                mirror_url_setting="SLF_API_LOCAL_MIRROR_URL",
                archive_path_setting="SLF_ARCHIVE_PATH",
                stash_writer=lambda records, path: 0,
            )
        }

    return _get_sources


@pytest.mark.django_db
def test_groupings_settled_date_gets_immutable_long_cache() -> None:
    """A date before the earliest-mutable threshold is public/7d/immutable (SNOW-526)."""
    with patch(
        "apps.bulletins.services.settled.get_sources",
        _stub_get_sources(dt.date(2026, 1, 20)),
    ):
        response = Client().get(_groupings_url("2026-01-15"))
    assert response.status_code == 200
    cc = response.get("Cache-Control", "")
    assert "public" in cc
    assert "max-age=604800" in cc
    assert "immutable" in cc


@pytest.mark.django_db
def test_groupings_unsettled_date_keeps_short_cache_no_immutable() -> None:
    """A date on/after the threshold keeps the existing public/300s, no immutable."""
    with patch(
        "apps.bulletins.services.settled.get_sources",
        _stub_get_sources(dt.date(2026, 1, 10)),
    ):
        response = Client().get(_groupings_url("2026-01-15"))
    assert response.status_code == 200
    cc = response.get("Cache-Control", "")
    assert "public" in cc
    assert "max-age=300" in cc
    assert "immutable" not in cc


@pytest.mark.django_db
@freeze_time("2026-01-15")
def test_groupings_today_keeps_short_cache_no_immutable() -> None:
    """Today's date is never settled — earliest_mutable_date() is capped at today."""
    response = Client().get(_groupings_url("2026-01-15"))
    assert response.status_code == 200
    cc = response.get("Cache-Control", "")
    assert "public" in cc
    assert "max-age=300" in cc
    assert "immutable" not in cc


@pytest.mark.django_db
def test_groupings_boundary_date_is_unsettled() -> None:
    """The date equal to earliest_mutable_date() itself is not yet settled.

    The regression that matters: an off-by-one in ``is_settled``'s ``<``
    comparison would make the boundary day cacheable-forever the moment a
    provider's next ingest run could still rewrite it.
    """
    with patch(
        "apps.bulletins.services.settled.get_sources",
        _stub_get_sources(dt.date(2026, 1, 15)),
    ):
        response = Client().get(_groupings_url("2026-01-15"))
    assert response.status_code == 200
    cc = response.get("Cache-Control", "")
    assert "max-age=300" in cc
    assert "immutable" not in cc


@pytest.mark.django_db
def test_groupings_cache_hit_avoids_db_on_second_call() -> None:
    """After the first call the DB is not queried again for the same cache key."""
    _make_groupings_fixture()
    url = _groupings_url("2026-01-15")

    # Prime the cache.
    resp1 = Client().get(url)
    assert resp1.status_code == 200

    with patch("apps.public.api._build_groupings_payload") as mock_build:
        resp2 = Client().get(url)
        assert resp2.status_code == 200
        assert resp2.json() == resp1.json()
        mock_build.assert_not_called()


@pytest.mark.django_db
def test_groupings_query_count() -> None:
    """Endpoint uses a bounded number of DB queries (no N+1).

    Two queries build the payload: the ``RegionDayRating`` filter and the
    ``select_related`` grouping query. SNOW-526's settled-threshold check
    (``apps.bulletins.services.settled.earliest_mutable_date()``, one aggregate
    per registered ``BulletinSource``) is memoised at the call site
    (``apps.public.api._cached_earliest_mutable_date``) for 60s, so it must NOT
    show up here as extra queries on every request — only on a cold memo.
    Warmed explicitly below (a direct call, not an HTTP round trip, so it
    can't also warm the payload cache key this test measures).
    """
    _make_groupings_fixture()
    url = _groupings_url("2026-01-15", country="ch")

    # Clear everything, then warm ONLY the settled-threshold memo — real
    # traffic keeps it warm too, since it's shared across every date and
    # country for its 60s window.
    cache.clear()
    public_api._cached_earliest_mutable_date()

    with CaptureQueriesContext(connection) as ctx:
        response = Client().get(url)
    assert response.status_code == 200
    assert len(ctx.captured_queries) <= 3


# ---------------------------------------------------------------------------
# SNOW-54 — coverage property on regions_geojson / region_summary
# ---------------------------------------------------------------------------


def _make_ch_region(
    region_id: str = "CH-4115",
) -> tuple[object, object]:
    """Return (major, region) for a minimal CH micro-region."""
    major = MajorRegionFactory.create(
        prefix="CH-4",
        country="CH",
        name_native="Wallis",
        name_en="Valais",
    )
    sub = SubRegionFactory.create(
        prefix="CH-41",
        major=major,
        name_native="Bas-Valais",
        name_en="Lower Valais",
    )
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    region = MicroRegionFactory.create(
        region_id=region_id,
        name="Test Region",
        slug=region_id.lower(),
        boundary=boundary,
        subregion=sub,
    )
    return major, region


@pytest.mark.django_db
def test_regions_geojson_covered_property_true_for_rated_region() -> None:
    """SNOW-54: a feature for a region with a RegionDayRating has covered=True."""
    cache.clear()
    _, region = _make_ch_region("CH-4115")
    RegionDayRatingFactory.create(
        region=region,
        date=dt.date(2026, 1, 15),
        max_rating=RegionDayRating.Rating.LOW,
    )

    client = Client()
    response = client.get(reverse("api:regions_geojson") + "?country=ch")
    assert response.status_code == 200
    by_id = {f["properties"]["id"]: f for f in response.json()["features"]}
    assert by_id["CH-4115"]["properties"]["covered"] is True


@pytest.mark.django_db
def test_regions_geojson_covered_property_false_for_unrated_region() -> None:
    """SNOW-54: a feature for a region with no RegionDayRating has covered=False."""
    cache.clear()
    _make_ch_region("CH-9311")  # no rating created

    client = Client()
    response = client.get(reverse("api:regions_geojson") + "?country=ch")
    assert response.status_code == 200
    by_id = {f["properties"]["id"]: f for f in response.json()["features"]}
    assert by_id["CH-9311"]["properties"]["covered"] is False


@pytest.mark.django_db
def test_region_summary_uncovered_region_shows_gap_tooltip() -> None:
    """SNOW-54: an uncovered region renders the provider-gap tooltip copy."""
    cache.clear()
    _, region = _make_ch_region("CH-9311")
    # No RegionDayRating for CH-9311 → uncovered.

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["ch-9311"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    assert 'data-testid="region-tooltip-uncovered"' in html
    assert "SLF" in html
    assert 'data-testid="region-tooltip-no-bulletin"' not in html


@pytest.mark.django_db
def test_region_summary_covered_no_rating_shows_no_bulletin_tooltip() -> None:
    """SNOW-54: a covered region with no rating on the queried date shows 'no bulletin'."""
    cache.clear()
    _, region = _make_ch_region("CH-4115")
    # Create a rating on a different date so the region is covered, but not today.
    RegionDayRatingFactory.create(
        region=region,
        date=dt.date(2026, 1, 10),
        max_rating=RegionDayRating.Rating.LOW,
    )

    client = Client()
    response = client.get(
        # Query a date with no rating.
        reverse("api:region_summary", args=["ch-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    assert 'data-testid="region-tooltip-no-bulletin"' in html
    assert 'data-testid="region-tooltip-uncovered"' not in html


# ---------------------------------------------------------------------------
# community_reports_geojson (SNOW-419)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCommunityReportsGeojson:
    """community_reports_geojson — anonymised 48h FieldObservation overlay."""

    def test_feature_collection_shape(self) -> None:
        """Returns a FeatureCollection of Point features."""
        region = MicroRegionFactory.create(name="Martigny-Verbier")
        FieldObservationFactory.create(
            region=region,
            latitude=46.123456,
            longitude=7.654321,
            observation_type=FieldObservation.OBSERVATION_TYPE.WHUMPFING,
            observed_at=timezone.now(),
        )
        response = Client().get(reverse("api:community_reports_geojson"))
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 1
        feature = data["features"][0]
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "Point"
        assert feature["properties"]["type"] == "WHUMPFING"
        assert feature["properties"]["type_label"] == "Whumpfing"
        assert feature["properties"]["region_name"] == "Martigny-Verbier"

    def test_region_name_is_null_when_region_unset(self) -> None:
        """A report with no resolved region carries region_name: null."""
        FieldObservationFactory.create(region=None, observed_at=timezone.now())
        response = Client().get(reverse("api:community_reports_geojson"))
        data = response.json()
        assert data["features"][0]["properties"]["region_name"] is None

    def test_includes_report_just_inside_48h_window(self) -> None:
        """A report 47h59m old is included."""
        FieldObservationFactory.create(
            observed_at=timezone.now() - dt.timedelta(hours=47, minutes=59),
        )
        response = Client().get(reverse("api:community_reports_geojson"))
        assert len(response.json()["features"]) == 1

    def test_excludes_report_just_outside_48h_window(self) -> None:
        """A report 48h01m old is excluded."""
        FieldObservationFactory.create(
            observed_at=timezone.now() - dt.timedelta(hours=48, minutes=1),
        )
        response = Client().get(reverse("api:community_reports_geojson"))
        assert response.json()["features"] == []

    def test_coordinates_rounded_to_three_decimal_places(self) -> None:
        """Coordinates are [lon, lat] rounded to 3 dp — never full precision."""
        FieldObservationFactory.create(
            latitude=46.123456789,
            longitude=7.987654321,
            observed_at=timezone.now(),
        )
        response = Client().get(reverse("api:community_reports_geojson"))
        coords = response.json()["features"][0]["geometry"]["coordinates"]
        assert coords == [7.988, 46.123]

    def test_observed_at_is_sent_at_full_precision(self) -> None:
        """observed_at crosses the wire as recorded, to the second.

        It was floored to the preceding quarter hour as part of the SNOW-419
        anonymisation. Nothing in this payload names a reporter, so the floor
        identified nobody — it only made the map's own popup read up to
        fifteen minutes older than the same report in the reporter's panel,
        and always in that direction. Precision is the thing that keeps the
        two surfaces agreeing; the anonymity is carried by the rounded
        coordinates and the absent identity fields, which the tests either
        side of this one cover.
        """
        observed_at = dt.datetime(2026, 7, 19, 10, 37, 42, tzinfo=dt.UTC)
        # Freeze "now" alongside the fixed observed_at so the report stays
        # inside the endpoint's 48h window regardless of the wall-clock date
        # the suite runs on (otherwise this assertion rots and features empties).
        with freeze_time("2026-07-19T11:00:00Z"):
            FieldObservationFactory.create(observed_at=observed_at)
            response = Client().get(reverse("api:community_reports_geojson"))
        properties = response.json()["features"][0]["properties"]
        assert properties["observed_at"] == observed_at.isoformat()

    def test_response_never_exposes_raw_coordinates_or_identity(self) -> None:
        """No full-precision coord, gps_*, user, or pk fields cross the wire."""
        FieldObservationFactory.create(
            latitude=46.123456789,
            longitude=7.987654321,
            gps_latitude=46.111111,
            gps_longitude=7.222222,
            accuracy_radius_km=0.05,
            observed_at=timezone.now(),
        )
        response = Client().get(reverse("api:community_reports_geojson"))
        body = response.content.decode()
        # Full-precision coordinates never appear, only the 3dp-rounded pair.
        assert "46.123456789" not in body
        assert "7.987654321" not in body
        assert "46.111111" not in body
        assert "7.222222" not in body
        # Forbidden keys never appear in the raw body — a stronger check than
        # parsing, since it also rules out the key showing up unexpectedly
        # nested inside "properties".
        for forbidden_key in (
            "gps_latitude",
            "gps_longitude",
            "accuracy_radius_km",
            "user",
            '"id"',
            '"pk"',
        ):
            assert forbidden_key not in body
        data = response.json()
        properties = data["features"][0]["properties"]
        assert set(properties) == {"type", "type_label", "observed_at", "region_name"}

    def test_freshness_headers_present(self) -> None:
        """X-Data-Generated-At / X-Data-Max-Age headers are set from the newest row."""
        observed_at = timezone.now() - dt.timedelta(hours=1)
        FieldObservationFactory.create(observed_at=observed_at)
        response = Client().get(reverse("api:community_reports_geojson"))
        assert response.has_header("X-Data-Generated-At")
        assert response.has_header("X-Data-Max-Age")
        # apply_freshness_headers truncates to whole seconds.
        assert response["X-Data-Generated-At"] == observed_at.isoformat(
            timespec="seconds"
        )

    def test_freshness_max_age_is_the_community_window(self) -> None:
        """X-Data-Max-Age is the 120s community window.

        Regression coverage: apply_freshness_headers defaults max_age to
        24h, which would silently diverge from the intended 120s freshness
        window unless passed explicitly. The window is independent of
        Cache-Control now that the response is no-store (SNOW-459) and no
        longer carries a max-age.
        """
        FieldObservationFactory.create(observed_at=timezone.now())
        response = Client().get(reverse("api:community_reports_geojson"))
        assert response["X-Data-Max-Age"] == "120"
        # unsafe_after is deliberately omitted for non-safety-critical data.
        assert "X-Data-Unsafe-After" not in response

    def test_freshness_falls_back_to_now_when_empty(self) -> None:
        """With no rows in the window, freshness falls back to 'now'."""
        before = timezone.now()
        response = Client().get(reverse("api:community_reports_geojson"))
        after = timezone.now()
        generated_at = dt.datetime.fromisoformat(response["X-Data-Generated-At"])
        # apply_freshness_headers truncates to whole seconds, so widen the
        # window by one second on each side rather than asserting exact
        # microsecond containment.
        assert before - dt.timedelta(seconds=1) <= generated_at
        assert generated_at <= after + dt.timedelta(seconds=1)

    def test_cache_control_prevents_shared_caching(self) -> None:
        """SNOW-459: response is private/no-store, never public.

        The overlay is per-user (each viewer's own reports are surfaced
        distinctly), so a shared cache must not be able to replay one user's
        response to another. Making it publicly cacheable is deferred to
        SNOW-469. ``public`` must be absent; ``no-store`` present.
        """
        response = Client().get(reverse("api:community_reports_geojson"))
        cache_control = response.get("Cache-Control", "")
        assert "no-store" in cache_control
        assert "private" in cache_control
        assert "public" not in cache_control

    def test_not_shared_cacheable_with_analytics_enabled(self) -> None:
        """SNOW-459: no-store holds even when PostHog would add Vary: Cookie.

        Unlike the SNOW-299 public endpoints, this one must never be
        shared-cached regardless of Vary — so the bypass is closed by
        no-store, not by suppressing Vary: Cookie.
        """
        with override_settings(POSTHOG_API_KEY="phc_test"):
            response = Client().get(reverse("api:community_reports_geojson"))
        assert response.status_code == 200
        assert "no-store" in response.get("Cache-Control", "")


# ---------------------------------------------------------------------------
# kind=RESORT filtering (SNOW-544)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resorts_by_region_excludes_touring_terrain() -> None:
    """Lift-less touring terrain is not a resort, so it is not listed."""
    region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
    ResortFactory.create(region=region, name="Verbier")
    ResortFactory.create(
        region=region, name="Grimsel", kind=Resort.Kind.TOURING_TERRAIN
    )

    data = Client().get(reverse("api:resorts_by_region")).json()
    assert data == {"CH-4115": ["Verbier"]}


@pytest.mark.django_db
def test_resorts_by_region_omits_a_region_left_with_no_resorts() -> None:
    """A region holding only touring terrain drops out entirely.

    The endpoint already omits regions with no resorts; filtering must
    not leave one behind with an empty list.
    """
    region = MicroRegionFactory.create(region_id="CH-1247", slug="ch-1247")
    ResortFactory.create(
        region=region, name="Grimsel", kind=Resort.Kind.TOURING_TERRAIN
    )

    assert Client().get(reverse("api:resorts_by_region")).json() == {}


@pytest.mark.django_db
def test_resorts_geojson_excludes_touring_terrain() -> None:
    """Drawing touring terrain as a resort pin would claim lifts that don't exist."""
    region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
    ResortFactory.create(region=region, name="Verbier", latitude=46.1, longitude=7.2)
    ResortFactory.create(
        region=region,
        name="Grimsel",
        latitude=46.57,
        longitude=8.33,
        kind=Resort.Kind.TOURING_TERRAIN,
    )

    data = Client().get(reverse("api:resorts_geojson")).json()
    names = [f["properties"]["name"] for f in data["features"]]
    assert names == ["Verbier"]


# ---------------------------------------------------------------------------
# resort_popup — "why it matters" line (SNOW-542)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resort_popup_renders_why_it_matters_line() -> None:
    """A curated line renders as prose above the metadata rows."""
    resort = ResortFactory.create(
        latitude=46.1,
        longitude=7.4,
        why_it_matters="Four lifts, national freeride reputation.",
    )

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    html = response.json()["html"]
    assert 'data-testid="resort-why-it-matters"' in html
    assert "Four lifts, national freeride reputation." in html
    # A populated line never shows either blank-state branch.
    assert "resort-why-it-matters-hint" not in html
    assert "resort-why-it-matters-signup" not in html


@pytest.mark.django_db
def test_resort_popup_blank_why_it_matters_anonymous_prompts_register() -> None:
    """The blank line is the register prompt for an anonymous visitor."""
    resort = ResortFactory.create(name="Haldigrat", latitude=46.1, longitude=7.4)

    client = Client()
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    html = response.json()["html"]
    assert 'data-testid="resort-why-it-matters-signup"' in html
    assert reverse("accounts:register") in html
    assert "Haldigrat" in html


@pytest.mark.django_db
def test_resort_popup_blank_why_it_matters_staff_shows_curation_hint() -> None:
    """Staff see a curation hint rather than the register prompt."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)

    client = Client()
    client.force_login(UserFactory.create(is_staff=True))
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    html = response.json()["html"]
    assert 'data-testid="resort-why-it-matters-hint"' in html
    assert "Add a why-it-matters line" in html
    assert "resort-why-it-matters-signup" not in html


@pytest.mark.django_db
def test_resort_popup_blank_why_it_matters_signed_in_renders_nothing() -> None:
    """A signed-in reader has no way to contribute copy, so gets no prompt."""
    resort = ResortFactory.create(latitude=46.1, longitude=7.4)

    client = Client()
    client.force_login(UserFactory.create(is_staff=False))
    response = client.get(reverse("api:resort_popup", args=[resort.pk]))

    html = response.json()["html"]
    assert "resort-why-it-matters" not in html
