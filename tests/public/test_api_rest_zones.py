"""
tests/public/test_api_rest_zones.py — /api/rest-zones.geojson (SNOW-1384).

The zones whose box touches the rectangle, each with what the sheet reads
and whether it is in force on the asked day; the flag gate; the bbox and
date rules; and the cap.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from waffle.testutils import override_flag

from apps.public import api
from tests.factories import RestZoneFactory, UserFactory

pytestmark = pytest.mark.django_db

BOX = "10.0,46.8,10.3,46.95"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """No cached answers between tests."""
    cache.clear()


@pytest.fixture
def signed_in(client: Client) -> Client:
    """A signed-in client."""
    client.force_login(UserFactory.create())
    return client


def _get(client: Client, **params: str) -> Any:
    """GET the endpoint with the box and any other parameters."""
    query = {"bbox": BOX, **params}
    return client.get(reverse("api:rest_zones_geojson"), query)


def _far_zone() -> Any:
    """A zone well outside ``BOX``."""
    ring = [[7.0, 46.0], [7.1, 46.0], [7.1, 46.1], [7.0, 46.0]]
    return RestZoneFactory.create(
        geometry={"type": "MultiPolygon", "coordinates": [[ring]]},
        west=7.0,
        south=46.0,
        east=7.1,
        north=46.1,
    )


@override_flag("route_drawing", active=False)
def test_the_flag_off_is_404(signed_in: Client) -> None:
    """Outside the flag the layer does not exist."""
    RestZoneFactory.create()
    assert _get(signed_in).status_code == 404


@override_flag("route_drawing", active=True)
def test_the_zones_in_the_box_are_served(signed_in: Client) -> None:
    """A zone in the box, with its properties; a far zone is not."""
    inside = RestZoneFactory.create(
        name="Silvretta",
        restriction={"de": "Betretungsverbot"},
        url="https://www.gr.ch/wildruhe",
    )
    _far_zone()
    response = _get(signed_in, d="2027-01-10")
    assert response.status_code == 200
    assert "Accept-Language" in response["Vary"]
    body = response.json()
    assert body["truncated"] is False
    assert body["attribution"] == "© BAFU"
    (feature,) = body["features"]
    assert feature["id"] == str(inside.source_uuid)
    assert feature["geometry"] == inside.geometry
    assert feature["properties"] == {
        "name": "Silvretta",
        "binding": True,
        "restriction": "Betretungsverbot",
        "period": "21.12. - 30.04.",
        "in_force": True,
        "canton": "GR",
        "url": "https://www.gr.ch/wildruhe",
    }


@override_flag("route_drawing", active=True)
def test_the_day_decides_in_force(signed_in: Client) -> None:
    """Out of season the zone is still drawn, marked not in force."""
    RestZoneFactory.create()
    (feature,) = _get(signed_in, d="2026-08-01").json()["features"]
    assert feature["properties"]["in_force"] is False


@override_flag("route_drawing", active=True)
def test_no_day_is_today(signed_in: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``d`` the answer is for ``timezone.localdate()``."""
    monkeypatch.setattr(api.timezone, "localdate", lambda: dt.date(2027, 2, 1))
    RestZoneFactory.create()
    (feature,) = _get(signed_in).json()["features"]
    assert feature["properties"]["in_force"] is True


@override_flag("route_drawing", active=True)
def test_an_empty_box_is_an_empty_collection(signed_in: Client) -> None:
    """No zones: an empty FeatureCollection, not an error."""
    body = _get(signed_in).json()
    assert body["features"] == []
    assert body["truncated"] is False


@override_flag("route_drawing", active=True)
@pytest.mark.parametrize("bad", ["2026-13-01", "20260801", "tomorrow"])
def test_a_bad_day_is_400(signed_in: Client, bad: str) -> None:
    """Strict YYYY-MM-DD, a real day."""
    response = _get(signed_in, d=bad)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_date"}


@override_flag("route_drawing", active=True)
@pytest.mark.parametrize(
    ("bbox", "error"),
    [
        ("", "invalid_bbox"),
        ("10,46,10.1", "invalid_bbox"),
        ("9.0,46.0,10.5,46.2", "bbox_too_large"),
        ("9.0,46.0,9.5,47.5", "bbox_too_large"),
    ],
)
def test_a_bad_box_is_400(signed_in: Client, bbox: str, error: str) -> None:
    """Missing, malformed, or wider than a degree either way."""
    response = _get(signed_in, bbox=bbox)
    assert response.status_code == 400
    assert response.json() == {"error": error}


@override_flag("route_drawing", active=True)
def test_past_the_cap_the_answer_says_truncated(
    signed_in: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first ``REST_ZONES_MAX_FEATURES`` zones, by feature id, flagged."""
    monkeypatch.setattr(api, "REST_ZONES_MAX_FEATURES", 1)
    first = RestZoneFactory.create(feature_id=1)
    RestZoneFactory.create(feature_id=2)
    body = _get(signed_in).json()
    assert body["truncated"] is True
    assert [f["id"] for f in body["features"]] == [str(first.source_uuid)]
