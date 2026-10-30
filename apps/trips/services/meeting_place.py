"""
apps/trips/services/meeting_place.py — a meeting point in words (SNOW-1214).

A meeting point is never shown as a latitude and longitude. When it has a
three word address that is shown; beside it, and in its place when the
conversion fails, goes one sentence placing the pin against the route:

    At the route's start
    At the route's end
    On the route, 9 km in
    2 km north-east of the route's start

**One rule, two implementations.** The Plan page writes the sentence live
as the map moves under its pin, so it is computed in the browser by
``static/js/meeting_place_core.js``; the trip page and the trip list
render it here. Both read ``tests/fixtures/meeting_place_vectors.json``,
so a change to one that the other does not make fails a test on each
side.

``describe`` returns the parts — a kind, a distance label and a compass
octant — and ``sentence`` words them. The split is what lets the vectors
be language-free: the browser words the same parts from its strings
template, and neither side compares translated text.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

from django.utils.translation import gettext as _

from track.geo import (
    EARTH_RADIUS_M,
    haversine_m,
    initial_bearing_deg,
    octant_for,
)

logger = logging.getLogger(__name__)

# How close a pin must be to a place to be AT it. A meeting point is
# chosen by eye on a phone, and a car park or a lift station is tens of
# metres across: a pin 40 m from the first track point is at the start
# for anyone standing there.
AT_PLACE_M = 75.0

# The query string a maps link opens a coordinate with. Google's
# cross-platform "search" URL: it opens the Maps app where one is
# installed, on any phone, and the web map everywhere else.
MAPS_SEARCH_URL = "https://www.google.com/maps/search/"

Kind = Literal["start", "end", "on_route", "from_start", "from_end"]


@dataclass(frozen=True)
class MeetingPlace:
    """The parts of a meeting point's sentence.

    Attributes:
        kind: Which sentence applies.
        distance: The distance label (``"9 km"``, ``"300 m"``) for
            ``on_route`` and the two ``from_*`` kinds; ``""`` otherwise.
        octant: The compass octant from the route's end to the pin
            (``"NE"``) for the two ``from_*`` kinds; ``""`` otherwise.

    """

    kind: Kind
    distance: str = ""
    octant: str = ""

    def to_string(self) -> str:
        """Return the parts as one line, for logs and test failures."""
        return f"{self.kind} {self.distance} {self.octant}".strip()

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


def distance_label(metres: float) -> str:
    """Word a distance the way a person gives directions.

    Under a kilometre, to the nearest 50 m (never below 50 m). Under ten
    kilometres, to one decimal place with a trailing ``.0`` dropped. From
    ten kilometres, to the whole kilometre. Rounded half up, as
    ``Math.round`` does in the browser, so both sides agree on a half.

    Args:
        metres: A distance in metres.

    Returns:
        The label, such as ``"300 m"``, ``"2.5 km"`` or ``"12 km"``.

    """
    if metres < 975:
        return f"{max(50, math.floor(metres / 50 + 0.5) * 50)} m"
    if metres < 9950:
        tenths = math.floor(metres / 100 + 0.5)
        return f"{tenths // 10} km" if tenths % 10 == 0 else f"{tenths / 10} km"
    return f"{math.floor(metres / 1000 + 0.5)} km"


def _nearest_on_track(
    points: list[list[float]], latitude: float, longitude: float
) -> tuple[float, float]:
    """Find how far the pin is from the track, and how far along it.

    Each segment is projected onto a flat plane centred on the pin — an
    equirectangular projection, exact enough at the scale of a hillside —
    and the pin's foot on it found. The browser does the same arithmetic
    in the same order.

    Args:
        points: The track as ``[[lon, lat, …], …]``.
        latitude: The pin's latitude.
        longitude: The pin's longitude.

    Returns:
        ``(off_track_m, along_m)`` for the nearest foot.

    """
    scale = math.radians(1) * EARTH_RADIUS_M
    cos_lat = math.cos(math.radians(latitude))

    def plane(point: list[float]) -> tuple[float, float]:
        return (
            (point[0] - longitude) * cos_lat * scale,
            (point[1] - latitude) * scale,
        )

    best_off = math.inf
    best_along = 0.0
    walked = 0.0
    ax, ay = plane(points[0])
    for point in points[1:]:
        bx, by = plane(point)
        dx, dy = bx - ax, by - ay
        length_sq = dx * dx + dy * dy
        t = (
            0.0
            if length_sq == 0
            else max(0.0, min(1.0, -(ax * dx + ay * dy) / length_sq))
        )
        fx, fy = ax + t * dx, ay + t * dy
        off = math.hypot(fx, fy)
        length = math.sqrt(length_sq)
        if off < best_off:
            best_off = off
            best_along = walked + t * length
        walked += length
        ax, ay = bx, by
    return best_off, best_along


def describe(
    points: list[list[float]], latitude: float, longitude: float
) -> MeetingPlace | None:
    """Place a meeting point against its route.

    In order: at the start, at the end, on the route (and how far in), or
    off it — then measured from whichever end is nearer, with the compass
    direction from that end to the pin. The start is tested first, so a
    loop whose ends meet says "start".

    Args:
        points: The track as ``[[lon, lat, …], …]``, GeoJSON axis order.
        latitude: The pin's latitude.
        longitude: The pin's longitude.

    Returns:
        The parts, or None for a track with no points to measure from.

    """
    if not points:
        return None
    start, end = points[0], points[-1]
    from_start = haversine_m(start[1], start[0], latitude, longitude)
    from_end = haversine_m(end[1], end[0], latitude, longitude)
    if from_start <= AT_PLACE_M:
        return MeetingPlace("start")
    if from_end <= AT_PLACE_M:
        return MeetingPlace("end")
    if len(points) > 1:
        off, along = _nearest_on_track(points, latitude, longitude)
        if off <= AT_PLACE_M:
            return MeetingPlace("on_route", distance_label(along))
    near_start = from_start <= from_end
    origin = start if near_start else end
    bearing = initial_bearing_deg(origin[1], origin[0], latitude, longitude)
    return MeetingPlace(
        "from_start" if near_start else "from_end",
        distance_label(from_start if near_start else from_end),
        octant_for(bearing) or "",
    )


def _octant_words() -> dict[str, str]:
    """Return each compass octant as it reads in a sentence."""
    return {
        "N": _("north"),
        "NE": _("north-east"),
        "E": _("east"),
        "SE": _("south-east"),
        "S": _("south"),
        "SW": _("south-west"),
        "W": _("west"),
        "NW": _("north-west"),
    }


def sentence(place: MeetingPlace | None) -> str:
    """Word a meeting place in the reader's language.

    The same five sentences ``_trip_meeting_picker.html``'s strings
    template gives the browser.

    Args:
        place: The parts from ``describe``, or None.

    Returns:
        The sentence, or ``""`` for no place.

    """
    if place is None:
        return ""
    direction = _octant_words().get(place.octant, "")
    if place.kind == "start":
        return _("At the route's start")
    if place.kind == "end":
        return _("At the route's end")
    if place.kind == "on_route":
        return _("On the route, %(distance)s in") % {"distance": place.distance}
    if place.kind == "from_start":
        return _("%(distance)s %(direction)s of the route's start") % {
            "distance": place.distance,
            "direction": direction,
        }
    return _("%(distance)s %(direction)s of the route's end") % {
        "distance": place.distance,
        "direction": direction,
    }


def maps_url(latitude: float, longitude: float) -> str:
    """Return a link that opens a coordinate in the reader's maps app.

    The coordinate travels in the link and is never printed on the page:
    this is what someone taps to navigate to a meeting point that has no
    three word address.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.

    Returns:
        The URL.

    """
    query = urlencode({"api": 1, "query": f"{latitude:.6f},{longitude:.6f}"})
    return f"{MAPS_SEARCH_URL}?{query}"
