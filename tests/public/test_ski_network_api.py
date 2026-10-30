"""
tests/public/test_ski_network_api.py — Tests for /api/ski-network.geojson (SNOW-1276).

Covers:
  selection          — the skiable segments whose box touches the rectangle,
                      snowshoe-only and far-off ones left out.
  bbox validation    — a missing or malformed box, and one wider than the
                      cap, are 400s.
  the cap            — past ``SKI_NETWORK_MAX_FEATURES`` the answer says
                      ``truncated``.
  response policy    — the ETag and the revalidatable cache policy.
  the overview       — ``?overview=1`` answers the whole skiable network,
                      thinned, with no ids, in place of a bbox (SNOW-1322).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.public import api
from tests.factories import SkiSegmentFactory

pytestmark = pytest.mark.django_db

BOX = "10.0,46.8,10.3,46.95"


def _get(client: Client, bbox: str | None = BOX, etag: str = "") -> Any:
    """GET the endpoint with ``?bbox=``, and ``If-None-Match`` when given."""
    url = reverse("api:ski_network_geojson")
    headers = {"If-None-Match": etag} if etag else {}
    return client.get(url, {"bbox": bbox} if bbox is not None else {}, headers=headers)


def test_the_skiable_segments_in_the_box_are_served(client: Client) -> None:
    """A segment inside is a feature keyed by its service uuid; others are not."""
    inside = SkiSegmentFactory.create(segment_id=7)
    SkiSegmentFactory.create(discipline="ski_snowshoe_tour")
    SkiSegmentFactory.create(west=7.0, south=46.0, east=7.1, north=46.1)
    response = _get(client)
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "FeatureCollection"
    assert body["truncated"] is False
    (feature,) = body["features"]
    assert feature["id"] == str(inside.source_uuid)
    assert feature["properties"] == {"segment_id": 7}
    assert feature["geometry"] == inside.geometry


@pytest.mark.parametrize(
    ("bbox", "error"),
    [
        (None, "invalid_bbox"),
        ("10,46,10.1", "invalid_bbox"),
        ("10.3,46.8,10.0,46.9", "invalid_bbox"),
        ("9.0,46.0,10.0,46.2", "bbox_too_large"),
        ("10.0,46.0,10.2,47.0", "bbox_too_large"),
    ],
)
def test_a_bad_or_too_wide_box_is_refused(
    client: Client, bbox: str | None, error: str
) -> None:
    """The network is only drawn zoomed in; a country's worth is refused."""
    response = _get(client, bbox)
    assert response.status_code == 400
    assert response.json() == {"error": error}


def test_past_the_cap_the_answer_says_truncated(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap holds and the client is told."""
    monkeypatch.setattr(api, "SKI_NETWORK_MAX_FEATURES", 2)
    SkiSegmentFactory.create_batch(3)
    body = _get(client).json()
    assert body["truncated"] is True
    assert len(body["features"]) == 2


def test_the_answer_is_revalidatable(client: Client) -> None:
    """An ETag, a stale window when non-empty, and a 304 on a match."""
    SkiSegmentFactory.create()
    first = _get(client)
    assert "stale-while-revalidate" in first["Cache-Control"]
    again = _get(client, etag=first["ETag"])
    assert again.status_code == 304
    empty = _get(client, "1.0,1.0,1.1,1.1")
    assert empty.json()["features"] == []
    assert "stale-while-revalidate" not in empty["Cache-Control"]


def test_the_overview_is_the_whole_network_without_ids(client: Client) -> None:
    """Far-apart skiable lines in one answer, no bbox needed, with an ETag."""
    cache.clear()
    SkiSegmentFactory.create()
    SkiSegmentFactory.create(
        geometry={
            "type": "MultiLineString",
            "coordinates": [[[7.0, 46.0], [7.1, 46.1]]],
        },
        west=7.0,
        south=46.0,
        east=7.1,
        north=46.1,
    )
    SkiSegmentFactory.create(discipline="ski_snowshoe_tour")
    url = reverse("api:ski_network_geojson")
    response = client.get(url, {"overview": "1"})
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is False
    assert len(body["features"]) == 2
    assert all("id" not in feature for feature in body["features"])
    assert all(feature["properties"] == {} for feature in body["features"])
    again = client.get(
        url, {"overview": "1"}, headers={"If-None-Match": response["ETag"]}
    )
    assert again.status_code == 304
    cache.clear()
