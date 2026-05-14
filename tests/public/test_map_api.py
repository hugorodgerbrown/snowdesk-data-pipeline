"""
tests/public/test_map_api.py — Tests for the /api/ JSON endpoints.

Covers the endpoints consumed by the /map/ page:

* ``api:today_summaries``       — today's danger summaries per region.
* ``api:season_ratings``        — whole-season ``{date: {region_id: int}}``.
* ``api:resorts_by_region``     — resort list per region.
* ``api:regions_geojson``       — FeatureCollection of L4 region polygons.
* ``api:major_regions_geojson`` — FeatureCollection of L1 region polygons (SNOW-59).
* ``api:sub_regions_geojson``   — FeatureCollection of L2 region polygons (SNOW-59).
* ``api:region_summary``        — tooltip with danger-rating chip (?d= aware),
                                  English breadcrumb, date caption, and bulletin
                                  CTA (SNOW-174). Resort list removed.
"""

from __future__ import annotations

import datetime as dt
import json
from datetime import UTC, datetime, timedelta

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from bulletins.models import RegionDayRating
from tests.factories import (
    BulletinFactory,
    MajorRegionFactory,
    MicroRegionFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    ResortFactory,
    SubRegionFactory,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _today_window() -> tuple[datetime, datetime]:
    """Return a (valid_from, valid_to) pair that covers today in UTC."""
    today = timezone.localdate()
    vf = datetime(today.year, today.month, today.day, 6, 0, tzinfo=UTC)
    vt = datetime(today.year, today.month, today.day, 17, 0, tzinfo=UTC)
    return vf, vt


def _render_model(
    rating: str = "considerable",
    subdivision: str | None = "plus",
    problem_type: str = "persistent_weak_layers",
    elevation: dict | None = None,
    aspects: list[str] | None = None,
) -> dict:
    """Build a minimal v3 render_model dict shaped like the builder output."""
    return {
        "version": 3,
        "danger": {
            "key": rating,
            "number": "3",
            "subdivision": subdivision,
        },
        "traits": [
            {
                "category": "dry",
                "time_period": "all_day",
                "title": "Dry avalanches",
                "geography": {"source": "problems"},
                "problems": [
                    {
                        "problem_type": problem_type,
                        "time_period": "all_day",
                        "elevation": elevation
                        or {
                            "lower": 2200,
                            "upper": None,
                            "treeline": False,
                        },
                        "aspects": aspects
                        or ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
                        "comment_html": "",
                        "core_zone_text": None,
                        "danger_rating_value": rating,
                    }
                ],
                "prose": None,
                "danger_level": 3,
            }
        ],
        "snowpack_structure": None,
        "metadata": {
            "publication_time": None,
            "valid_from": None,
            "valid_until": None,
            "next_update": None,
            "unscheduled": False,
            "lang": "en",
        },
        "prose": {
            "snowpack_structure": None,
            "weather_review": None,
            "weather_forecast": None,
            "tendency": [],
        },
    }


def _make_today_bulletin(region, render_model: dict, raw_data: dict | None = None):
    """Create a bulletin valid today in ``region`` with the given render_model."""
    vf, vt = _today_window()
    extra = {"raw_data": raw_data} if raw_data is not None else {}
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        render_model=render_model,
        render_model_version=render_model.get("version", 3),
        **extra,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )
    return bulletin


# ---------------------------------------------------------------------------
# today-summaries
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_today_summaries_empty_when_no_bulletins():
    """No bulletins → empty dict."""
    client = Client()
    response = client.get(reverse("api:today_summaries"))
    assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.django_db
def test_today_summaries_returns_expected_shape():
    """A single bulletin today produces a correctly-shaped summary."""
    region = MicroRegionFactory.create(
        region_id="CH-4115", name="Martigny – Verbier", slug="ch-4115"
    )
    _make_today_bulletin(region, _render_model())

    client = Client()
    response = client.get(reverse("api:today_summaries"))
    assert response.status_code == 200
    data = response.json()

    assert "CH-4115" in data
    summary = data["CH-4115"]
    assert summary["rating"] == "considerable"
    assert summary["subdivision"] == "plus"
    assert summary["problem"] == "Persistent weak layers"
    assert summary["elevation"] == "above 2200 m"
    assert summary["aspects"] == "all aspects"
    assert summary["name"] == "Martigny – Verbier"
    # ISO-8601 timestamps with timezone offset.
    assert "T" in summary["valid_from"]
    assert "+" in summary["valid_from"] or summary["valid_from"].endswith("Z")


@pytest.mark.django_db
def test_today_summaries_skips_regions_without_bulletins():
    """Regions whose only bulletin lies outside today's window are omitted."""
    included_region = MicroRegionFactory.create(
        region_id="CH-4115", name="Martigny", slug="ch-4115"
    )
    excluded_region = MicroRegionFactory.create(
        region_id="CH-9999", name="Empty", slug="ch-9999"
    )
    _make_today_bulletin(included_region, _render_model())

    # A bulletin from a week ago — should not appear in today's summaries.
    vf = timezone.now() - timedelta(days=7)
    vt = vf + timedelta(hours=9)
    stale_bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        render_model=_render_model(),
        render_model_version=3,
    )
    RegionBulletinFactory.create(
        bulletin=stale_bulletin,
        region=excluded_region,
        region_name_at_time=excluded_region.name,
    )

    client = Client()
    data = client.get(reverse("api:today_summaries")).json()
    assert "CH-4115" in data
    assert "CH-9999" not in data


@pytest.mark.django_db
def test_today_summaries_elevation_below():
    """An upper-bound-only elevation renders as ``below N m``."""
    region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
    rm = _render_model(
        elevation={"lower": None, "upper": 1800, "treeline": False},
    )
    _make_today_bulletin(region, rm)

    client = Client()
    summary = client.get(reverse("api:today_summaries")).json()["CH-4115"]
    assert summary["elevation"] == "below 1800 m"


@pytest.mark.django_db
def test_today_summaries_partial_aspects():
    """A subset of aspects renders as a comma-joined list."""
    region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
    rm = _render_model(aspects=["N", "NE", "E", "NW"])
    _make_today_bulletin(region, rm)

    client = Client()
    summary = client.get(reverse("api:today_summaries")).json()["CH-4115"]
    assert summary["aspects"] == "N, NE, E, NW"


@pytest.mark.django_db
def test_today_summaries_prefers_morning_update_over_previous_evening():
    """
    When a region has two issues covering today — a previous-day evening
    bulletin (valid from 17:00 yesterday) and a same-day morning update
    (valid from 08:00 today) — the morning update wins for queries made
    after it takes over, because it is the later refresh of the forecast.
    """
    region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
    now = timezone.now()
    today = now.date()

    # Previous-day evening issue, valid from 17:00 yesterday through 17:00 today.
    evening_vf = datetime(
        today.year, today.month, today.day, 17, 0, tzinfo=UTC
    ) - timedelta(days=1)
    evening_vt = datetime(today.year, today.month, today.day, 17, 0, tzinfo=UTC)
    evening = BulletinFactory.create(
        issued_at=evening_vf - timedelta(minutes=30),
        valid_from=evening_vf,
        valid_to=evening_vt,
        render_model=_render_model(rating="moderate", subdivision=None),
        render_model_version=3,
    )
    RegionBulletinFactory.create(
        bulletin=evening,
        region=region,
        region_name_at_time=region.name,
    )

    # Same-day morning update, valid from 30 minutes ago through 17:00 today,
    # so its window contains "now" regardless of when the test runs.
    morning_vf = now - timedelta(minutes=30)
    morning_vt = datetime(today.year, today.month, today.day, 17, 0, tzinfo=UTC)
    # Guard: if the test happens to run after 17:00 UTC, push valid_to out.
    if morning_vt <= now:
        morning_vt = now + timedelta(hours=1)
    morning = BulletinFactory.create(
        issued_at=morning_vf - timedelta(minutes=30),
        valid_from=morning_vf,
        valid_to=morning_vt,
        render_model=_render_model(rating="considerable", subdivision="plus"),
        render_model_version=3,
    )
    RegionBulletinFactory.create(
        bulletin=morning,
        region=region,
        region_name_at_time=region.name,
    )

    client = Client()
    summary = client.get(reverse("api:today_summaries")).json()["CH-4115"]
    # The morning update's rating wins — the selection helper picks the
    # issue whose window contains ``now`` (the morning one).
    assert summary["rating"] == "considerable"
    assert summary["subdivision"] == "plus"


@pytest.mark.django_db
def test_today_summaries_handles_error_sentinel_render_model():
    """
    A bulletin stored with the validation-failure sentinel
    (``{"version": 0, "error": ...}``) must not 500 the endpoint — the
    summary degrades gracefully to ``no_rating`` with empty fields.
    """
    region = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
    vf, vt = _today_window()
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        render_model={"version": 0, "error": "bad data", "error_type": "TypeError"},
        render_model_version=0,
    )
    RegionBulletinFactory.create(
        bulletin=bulletin,
        region=region,
        region_name_at_time=region.name,
    )

    client = Client()
    response = client.get(reverse("api:today_summaries"))
    assert response.status_code == 200
    summary = response.json()["CH-4115"]
    assert summary["rating"] == "no_rating"
    assert summary["subdivision"] is None
    assert summary["problem"] == ""


# ---------------------------------------------------------------------------
# resorts-by-region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resorts_by_region_empty_when_no_resorts():
    """No Resort rows → empty dict."""
    client = Client()
    response = client.get(reverse("api:resorts_by_region"))
    assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.django_db
def test_resorts_by_region_groups_names_alphabetically():
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
def test_regions_geojson_returns_feature_collection():
    """Every Region with a non-null boundary becomes a Feature."""
    boundary = {
        "type": "Polygon",
        "coordinates": [
            [[6.9, 46.4], [7.0, 46.4], [7.0, 46.5], [6.9, 46.5], [6.9, 46.4]]
        ],
    }
    MicroRegionFactory.create(
        region_id="CH-4115",
        name="Valais",
        slug="ch-4115",
        boundary=boundary,
    )
    # Region without boundary — should be skipped.
    MicroRegionFactory.create(
        region_id="CH-9999",
        name="No geometry",
        slug="ch-9999",
        boundary=None,
    )

    client = Client()
    response = client.get(reverse("api:regions_geojson"))
    assert response.status_code == 200
    data = response.json()

    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 1
    feature = data["features"][0]
    assert feature["type"] == "Feature"
    assert feature["properties"] == {"id": "CH-4115", "name": "Valais"}
    assert feature["geometry"] == boundary


# ---------------------------------------------------------------------------
# major-regions.geojson / sub-regions.geojson  (SNOW-59)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_major_regions_geojson_returns_feature_collection():
    """L1 majors with a non-null boundary become Features; null boundary skipped.

    Note: migration 0012 pre-loads the real CH-1..CH-9 fixtures, so this
    test works with non-CH prefixes to keep its assertions independent
    of fixture state.
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
    response = client.get(reverse("api:major_regions_geojson"))
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "FeatureCollection"

    # Find our fixture among any pre-loaded CH-* features.
    by_prefix = {f["properties"]["prefix"]: f for f in data["features"]}
    assert "AT-1" in by_prefix
    assert "AT-2" not in by_prefix  # boundary=None → skipped

    feature = by_prefix["AT-1"]
    assert feature["type"] == "Feature"
    assert feature["properties"] == {"prefix": "AT-1", "name_en": "Vorarlberg"}
    assert feature["geometry"] == boundary


@pytest.mark.django_db
def test_sub_regions_geojson_returns_feature_collection():
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
    response = client.get(reverse("api:sub_regions_geojson"))
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "FeatureCollection"

    by_prefix = {f["properties"]["prefix"]: f for f in data["features"]}
    assert "AT-11" in by_prefix
    assert "AT-12" not in by_prefix  # boundary=None → skipped

    feature = by_prefix["AT-11"]
    assert feature["properties"] == {
        "prefix": "AT-11",
        "name_en": "Vorarlberg North",
    }
    assert feature["geometry"] == boundary


# ---------------------------------------------------------------------------
# season-ratings
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_season_ratings_empty_when_no_day_ratings():
    """No RegionDayRating rows → empty dict."""
    client = Client()
    response = client.get(reverse("api:season_ratings"))
    assert response.status_code == 200
    assert response.json() == {}


@pytest.mark.django_db
def test_season_ratings_returns_expected_shape():
    """Top-level keys are ISO dates; inner dicts map region_id → rating int."""
    region_a = MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115")
    region_b = MicroRegionFactory.create(region_id="CH-4116", slug="ch-4116")
    day_one = dt.date(2026, 1, 15)
    day_two = dt.date(2026, 1, 16)

    RegionDayRatingFactory.create(
        region=region_a,
        date=day_one,
        max_rating=RegionDayRating.Rating.CONSIDERABLE,
    )
    RegionDayRatingFactory.create(
        region=region_b,
        date=day_one,
        max_rating=RegionDayRating.Rating.MODERATE,
    )
    RegionDayRatingFactory.create(
        region=region_a,
        date=day_two,
        max_rating=RegionDayRating.Rating.HIGH,
    )

    response = Client().get(reverse("api:season_ratings"))
    assert response.status_code == 200
    data = response.json()

    assert set(data.keys()) == {"2026-01-15", "2026-01-16"}
    # Considerable=3, moderate=2, high=4 — see _RATING_TO_INT.
    assert data["2026-01-15"] == {"CH-4115": 3, "CH-4116": 2}
    assert data["2026-01-16"] == {"CH-4115": 4}


# ---------------------------------------------------------------------------
# region-summary  (SNOW-174 pivot: tooltip, no bulletin dependency)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_region_summary_returns_html_for_known_region():
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
    response = client.get(reverse("api:region_summary", args=["CH-4115"]))
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"html", "level"}
    assert "Martigny" in data["html"]


@pytest.mark.django_db
def test_region_summary_includes_geographic_breadcrumb():
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
    response = client.get(reverse("api:region_summary", args=["CH-4115"]))
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
def test_region_summary_unknown_region_returns_404():
    """An unknown region_id returns 404."""
    client = Client()
    response = client.get(reverse("api:region_summary", args=["CH-UNKNOWN"]))
    assert response.status_code == 404


@pytest.mark.django_db
def test_region_summary_query_count():
    """The tooltip view issues at most 2 DB queries."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", subregion=sub)
    client = Client()
    with CaptureQueriesContext(connection) as ctx:
        response = client.get(reverse("api:region_summary", args=["CH-4115"]))
    assert response.status_code == 200
    # Queries: region + subregion + major join (1), RegionDayRating lookup (1).
    # The resorts prefetch was dropped in SNOW-174 when the resort list was removed.
    assert len(ctx.captured_queries) <= 2


@pytest.mark.django_db
def test_region_summary_accepts_date_query_param():
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
        reverse("api:region_summary", args=["CH-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # The chip should carry the considerable rating.
    assert 'data-level="considerable"' in html
    # Digit inside the chip.
    assert ">3<" in html


@pytest.mark.django_db
def test_region_summary_rejects_bad_date():
    """A malformed ?d= value returns 400 with {"error": "bad_date"}."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", subregion=sub)

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["CH-4115"]) + "?d=not-a-date"
    )
    assert response.status_code == 400
    assert response.json() == {"error": "bad_date"}


@pytest.mark.django_db
def test_region_summary_includes_headline_rating_chip():
    """The chip is an <a> wrapping .danger-tile[data-level] linked to the bulletin URL."""
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
        reverse("api:region_summary", args=["CH-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # The chip anchor carries the test id.
    assert 'data-testid="region-tooltip-rating-link"' in html
    # The chip spans carry the expected data-level.
    assert 'data-level="high"' in html
    # The digit for high is 4.
    assert ">4<" in html
    # The anchor href points at the dated bulletin URL.
    assert "/ch-4115/" in html
    assert "2026-01-15" in html


@pytest.mark.django_db
def test_region_summary_chip_falls_back_to_no_rating():
    """A region with no RegionDayRating row renders the chip in no_rating state."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", subregion=sub)

    client = Client()
    # Use a date far in the past where no rating exists.
    response = client.get(
        reverse("api:region_summary", args=["CH-4115"]) + "?d=2000-01-01"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    assert 'data-level="no_rating"' in html


@pytest.mark.django_db
def test_region_summary_breadcrumb_uses_english_names():
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
    response = client.get(reverse("api:region_summary", args=["CH-4115"]))
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
def test_region_summary_includes_level_key():
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
        reverse("api:region_summary", args=["CH-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    data = response.json()
    assert "level" in data
    assert data["level"] == "considerable"


@pytest.mark.django_db
def test_region_summary_level_key_falls_back_to_no_rating():
    """When no RegionDayRating exists, ``level`` is ``'no_rating'``."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", subregion=sub)

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["CH-4115"]) + "?d=2000-01-01"
    )
    assert response.status_code == 200
    assert response.json()["level"] == "no_rating"


@pytest.mark.django_db
def test_region_summary_includes_date_caption_and_cta():
    """Rendered HTML contains the formatted date string and the bulletin CTA."""
    major = MajorRegionFactory.create(prefix="CH-4", country="CH", name_native="Wallis")
    sub = SubRegionFactory.create(
        prefix="CH-41", major=major, name_native="Lower Valais"
    )
    MicroRegionFactory.create(region_id="CH-4115", slug="ch-4115", subregion=sub)

    client = Client()
    response = client.get(
        reverse("api:region_summary", args=["CH-4115"]) + "?d=2026-01-15"
    )
    assert response.status_code == 200
    html = response.json()["html"]
    # Date formatted as "j N Y" → "15 Jan. 2026" (N = abbreviated month name).
    assert "15 Jan" in html
    assert "2026" in html
    # CTA link is present with the expected test id.
    assert 'data-testid="region-tooltip-bulletin-link"' in html
    # CTA text includes the date.
    assert "Open bulletin for" in html
    # Date caption line.
    assert "Showing" in html


# ---------------------------------------------------------------------------
# Content type
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoints_return_json_content_type():
    """All map-page endpoints advertise application/json."""
    client = Client()
    for name in (
        "api:today_summaries",
        "api:season_ratings",
        "api:resorts_by_region",
        "api:regions_geojson",
        "api:major_regions_geojson",
        "api:sub_regions_geojson",
    ):
        response = client.get(reverse(name))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        # Body parses as JSON without raising.
        json.loads(response.content)
