"""
tests/public/test_map_api.py — Tests for the /api/ JSON endpoints.

Covers the endpoints consumed by the /map/ page:

* ``api:today_summaries``     — today's danger summaries per region.
* ``api:season_ratings``      — whole-season ``{date: {region_id: int}}``.
* ``api:resorts_by_region``   — resort list per region.
* ``api:regions_geojson``     — FeatureCollection of region polygons.
"""

from __future__ import annotations

import datetime as dt
import json
from datetime import UTC, datetime, timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from pipeline.models import RegionDayRating
from tests.factories import (
    BulletinFactory,
    RegionBulletinFactory,
    RegionDayRatingFactory,
    RegionFactory,
    ResortFactory,
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
        "fallback_key_message": None,
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


def _make_today_bulletin(region, render_model: dict):
    """Create a bulletin valid today in ``region`` with the given render_model."""
    vf, vt = _today_window()
    bulletin = BulletinFactory.create(
        issued_at=vf - timedelta(minutes=30),
        valid_from=vf,
        valid_to=vt,
        render_model=render_model,
        render_model_version=render_model.get("version", 3),
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
    region = RegionFactory.create(
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
    included_region = RegionFactory.create(
        region_id="CH-4115", name="Martigny", slug="ch-4115"
    )
    excluded_region = RegionFactory.create(
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
    region = RegionFactory.create(region_id="CH-4115", slug="ch-4115")
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
    region = RegionFactory.create(region_id="CH-4115", slug="ch-4115")
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
    region = RegionFactory.create(region_id="CH-4115", slug="ch-4115")
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
    region = RegionFactory.create(region_id="CH-4115", slug="ch-4115")
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
    region = RegionFactory.create(region_id="CH-4115", slug="ch-4115")
    ResortFactory.create(region=region, name="Verbier")
    ResortFactory.create(region=region, name="La Chaux")

    # A region with no resorts should be absent from the response.
    RegionFactory.create(region_id="CH-9999", slug="ch-9999")

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
    RegionFactory.create(
        region_id="CH-4115",
        name="Valais",
        slug="ch-4115",
        boundary=boundary,
    )
    # Region without boundary — should be skipped.
    RegionFactory.create(
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
    region_a = RegionFactory.create(region_id="CH-4115", slug="ch-4115")
    region_b = RegionFactory.create(region_id="CH-4116", slug="ch-4116")
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
    ):
        response = client.get(reverse(name))
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/json")
        # Body parses as JSON without raising.
        json.loads(response.content)
