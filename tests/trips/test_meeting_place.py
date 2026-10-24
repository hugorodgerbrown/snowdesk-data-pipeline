"""
tests/trips/test_meeting_place.py — the meeting point's sentence (SNOW-1214).

The shared vectors in tests/fixtures/meeting_place_vectors.json are the
contract: tests/js/test_meeting_place_core.js asserts the browser's copy of
the rule gives the same answers, so the Plan page and the trip page cannot
describe one pin two ways.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from django.utils import translation

from apps.trips.services.meeting_place import (
    MeetingPlace,
    describe,
    distance_label,
    maps_url,
    sentence,
)

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "meeting_place_vectors.json").read_text()
)


@pytest.mark.parametrize(
    "vector", VECTORS["vectors"], ids=[v["name"] for v in VECTORS["vectors"]]
)
def test_describe_matches_the_shared_vectors(vector: dict[str, Any]) -> None:
    """Every vector's pin is placed as the vectors file says."""
    place = describe(
        VECTORS["tracks"][vector["track"]], vector["pin"][1], vector["pin"][0]
    )

    got = (
        None
        if place is None
        else {
            "kind": place.kind,
            "distance": place.distance,
            "octant": place.octant,
        }
    )
    assert got == vector["expected"]


@pytest.mark.parametrize(("metres", "label"), VECTORS["labels"])
def test_distance_label_matches_the_shared_vectors(metres: float, label: str) -> None:
    """Distances are worded as the vectors file says."""
    assert distance_label(metres) == label


class TestSentence:
    """Wording the parts."""

    @pytest.mark.parametrize(
        ("place", "expected"),
        [
            (MeetingPlace("start"), "At the route's start"),
            (MeetingPlace("end"), "At the route's end"),
            (MeetingPlace("on_route", "9 km"), "On the route, 9 km in"),
            (
                MeetingPlace("from_start", "2 km", "NE"),
                "2 km north-east of the route's start",
            ),
            (
                MeetingPlace("from_end", "300 m", "SW"),
                "300 m south-west of the route's end",
            ),
        ],
    )
    def test_each_kind_reads_as_a_sentence(
        self, place: MeetingPlace, expected: str
    ) -> None:
        """Each of the five kinds has its own sentence."""
        with translation.override("en"):
            assert sentence(place) == expected

    def test_no_place_is_no_sentence(self) -> None:
        """A track with nothing to measure from says nothing."""
        assert sentence(None) == ""

    def test_the_parts_read_as_one_line(self) -> None:
        """``str`` gives the parts for a log line or a failure message."""
        assert str(MeetingPlace("from_start", "2 km", "NE")) == "from_start 2 km NE"


def test_maps_url_carries_the_coordinate_in_the_link() -> None:
    """The coordinate travels in the link, at stored precision."""
    assert maps_url(46.08, 7.3181974) == (
        "https://www.google.com/maps/search/?api=1&query=46.080000%2C7.318197"
    )
