"""
tests/public/test_ski_tours_api.py — /api/ski-tours.geojson (SNOW-1278).

The tours whose box touches the rectangle, each with what its list row
says, and a point per summit inside it; the bbox rules ski-network's
endpoint has; and the cap.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import Client
from django.urls import reverse

from apps.public import api
from apps.routes.models import SkiTour
from tests.factories import SkiTourFactory
from track.render import TRACK_RENDER_VERSION

pytestmark = pytest.mark.django_db

BOX = "10.0,46.8,10.3,46.95"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """No cached summits between tests."""
    cache.clear()


def _get(client: Client, bbox: str | None = BOX) -> Any:
    """GET the endpoint with ``?bbox=``."""
    url = reverse("api:ski_tours_geojson")
    return client.get(url, {"bbox": bbox} if bbox is not None else {})


def test_the_tours_and_their_summits_in_the_box_are_served(client: Client) -> None:
    """A tour line and its summit; a far tour and its summit are not."""
    inside = SkiTourFactory.create(ascent_time_label="03:30 h")
    SkiTourFactory.create(
        target_name="Far",
        west=7.0,
        south=46.0,
        east=7.1,
        north=46.1,
        geometry={
            "type": "MultiLineString",
            "coordinates": [[[7.0, 46.0], [7.1, 46.1]]],
        },
    )
    response = _get(client)
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is False
    tours = [f for f in body["features"] if f["properties"]["kind"] == "tour"]
    marks = [f for f in body["features"] if f["properties"]["kind"] != "tour"]
    (tour,) = tours
    assert tour["properties"]["uuid"] == str(inside.source_uuid)
    assert tour["properties"]["summit"] == "piz-buin-3312"
    assert tour["properties"]["time"] == "3 h 30"
    # No terrain yet: one line, in no direction (SNOW-1310).
    assert tour["properties"]["climbing"] is None
    assert tour["geometry"]["type"] == "LineString"
    (summit,) = marks
    assert summit["id"] == "summit:piz-buin-3312"
    assert summit["properties"]["kind"] == "summit"
    assert (summit["properties"]["up"], summit["properties"]["down"]) == (1, 0)


@pytest.mark.parametrize(
    ("bbox", "error"),
    [
        (None, "invalid_bbox"),
        ("10,46,10.1", "invalid_bbox"),
        ("9.0,46.0,10.0,46.2", "bbox_too_large"),
    ],
)
def test_a_bad_box_is_400(client: Client, bbox: str | None, error: str) -> None:
    """As ski-network's."""
    response = _get(client, bbox)
    assert response.status_code == 400
    assert response.json() == {"error": error}


def test_past_the_cap_the_answer_says_truncated(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first ``SKI_TOURS_MAX_FEATURES`` tours, flagged."""
    monkeypatch.setattr(api, "SKI_TOURS_MAX_FEATURES", 1)
    SkiTourFactory.create()
    SkiTourFactory.create()
    body = _get(client).json()
    assert body["truncated"] is True
    assert sum(1 for f in body["features"] if f["properties"]["kind"] == "tour") == 1


def test_the_overview_serves_every_tour_thinned(client: Client) -> None:
    """SNOW-1285: no box, every tour and summit, each line with what a tap reads."""
    near = SkiTourFactory.create()
    far = SkiTourFactory.create(
        target_name="Far",
        descent_m=900,
        ascent_m=None,
        west=7.0,
        south=46.0,
        east=7.1,
        north=46.1,
        geometry={
            "type": "LineString",
            "coordinates": [[7.0, 46.0, 2000.0], [7.05, 46.05000001], [7.1, 46.1]],
        },
    )
    url = reverse("api:ski_tours_geojson")
    response = client.get(url, {"overview": "1"})
    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is False
    tours = {
        f["properties"]["uuid"]: f
        for f in body["features"]
        if f["properties"]["kind"] == "tour"
    }
    assert set(tours) == {str(near.source_uuid), str(far.source_uuid)}
    line = tours[str(far.source_uuid)]
    assert line["properties"] == {
        "kind": "tour",
        "uuid": str(far.source_uuid),
        "summit": "far-3312",
        "climbing": None,
    }
    # The middle point is on the line, so the thinning drops it. No legs
    # yet: SAC's line as it is, in no direction (SNOW-1310).
    assert line["geometry"] == {
        "type": "MultiLineString",
        "coordinates": [[[7.0, 46.0], [7.1, 46.1]]],
    }
    summits = {f["id"] for f in body["features"] if f["properties"]["kind"] != "tour"}
    assert summits == {"summit:piz-buin-3312", "summit:far-3312"}


def test_a_tour_with_terrain_is_served_as_its_legs(client: Client) -> None:
    """SNOW-1310/1309: one Feature per stored leg, climbing or not, as a
    route is drawn — sliced from the stored render, never cut per request.
    """
    tour = SkiTourFactory.create()
    SkiTour.objects.filter(pk=tour.pk).update(
        track_render={
            "v": TRACK_RENDER_VERSION,
            "geometry": [[10.0, 46.0, 1800], [10.01, 46.01, 2400], [10.02, 46.0, 2000]],
            "legs": [
                {"i": 1, "climbing": True, "point_from": 0, "point_to": 1},
                {"i": 2, "climbing": False, "point_from": 1, "point_to": 2},
            ],
            "rows": [],
        }
    )
    with patch(
        "apps.routes.services.track_render.build_track_render",
        side_effect=AssertionError("built per request"),
    ):
        tours = [
            f
            for f in _get(client).json()["features"]
            if f["properties"]["kind"] == "tour"
        ]
    assert [f["properties"]["climbing"] for f in tours] == [True, False]
    assert {f["properties"]["uuid"] for f in tours} == {str(tour.source_uuid)}
    assert [f["geometry"]["coordinates"] for f in tours] == [
        [[10.0, 46.0], [10.01, 46.01]],
        [[10.01, 46.01], [10.02, 46.0]],
    ]
