"""
apps/routes/services/drawn.py — a route drawn on the map (SNOW-1255).

A drawn route is the waypoints the owner placed, joined by straight lines.
It is stored as an ordinary ``Route`` row (``source = DRAWN``) so that every
surface a GPX upload reaches — the legs, the slope classes, the point card,
the day's bulletin, sharing and trips — reaches it unchanged. See
docs/decisions/a-drawn-route-is-a-route.md.

This module owns the two things a drawn route needs that an upload does not:

* ``clean_waypoints`` — the validation of what the browser sends. A GPX
  file is parsed by ``gpx.parse_gpx``, which enforces its own shape; a list
  of coordinates in a JSON body has none, so it is checked here, and every
  caller (the preview and the save) goes through the same check.
* ``draft_route`` — an UNSAVED ``Route`` built from clean waypoints, carrying
  the derived figures (distance, bounds, point count) an upload would carry.
  The preview endpoint samples the terrain under it and describes it with
  the same Feature builder the map's routes layer uses, so a draft is drawn
  and profiled by exactly the code that will draw it once saved.

WHAT A DRAWN ROW LEAVES NULL, AND WHY. Every RECORDED field: each point's
``ele``, ``ascent_m``, ``descent_m``, ``started_at``, ``finished_at`` and
``source_point_count``. Nothing was recorded — there was no device and no
file — and the project's rule is that null means "we don't know", never
zero. The heights and the climb come from the terrain model through
``slope_samples`` (``terrain_heights.climb_figures`` already prefers those),
which is the same path an upload with no ``<ele>`` takes. Outside the terrain
model's coverage a drawn route therefore has no profile and no climb: the
line is drawn and nothing about the ground is claimed (decided 27 Oct 2026).

The mutating entry point, ``create_drawn_route``, lives beside
``create_route`` in ``routes.py`` because it shares that module's cap.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any

from apps.routes.models import Route
from apps.routes.services.gpx import MAX_POINTS
from track.slope_samples import cumulative_distances

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# The longest line a draft may be. A ski tour is rarely over 30 km, and a
# multi-day traverse drawn as one route still fits; the bound is what caps
# the terrain walk a single preview request can cost — the sampler visits
# the grid every 25 m, so 100 km is 4,000 samples, already a second or two.
MAX_DRAWN_DISTANCE_M = 100_000.0

# Two waypoints closer than this are one waypoint. A double tap on the Add
# button, or a desktop double click, lands the same point twice; a
# zero-length segment is noise to every consumer downstream (the legs, the
# slope walk), so it is dropped here rather than rejected.
_COINCIDENT_M = 0.5

# Coordinates are kept to six decimal places — about 0.1 m, far finer than
# a finger on a map — which is also the precision the slope sampler writes.
_COORDINATE_PRECISION = 6

_NAME_MAX_LENGTH = Route._meta.get_field("name").max_length or 100


class DrawnRouteError(ValueError):
    """Raised when the waypoints a browser sent cannot make a route.

    The message is fixed and safe to log; the view answers with its own
    fixed string rather than reflecting it.
    """


def _coordinate(raw: Any) -> tuple[float, float]:
    """Read one ``[lon, lat]`` pair, or raise.

    ``bool`` is refused explicitly: it is an ``int`` subclass in Python, so
    ``[true, false]`` would otherwise read as the coordinate (1, 0).

    Args:
        raw: One element of the posted list.

    Returns:
        ``(longitude, latitude)`` as floats.

    Raises:
        DrawnRouteError: When the element is not a finite, in-range pair.

    """
    if not isinstance(raw, list | tuple) or len(raw) < 2:
        raise DrawnRouteError("a waypoint must be a [lon, lat] pair")
    lon, lat = raw[0], raw[1]
    for value in (lon, lat):
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise DrawnRouteError("a waypoint ordinate must be a number")
        if not math.isfinite(value):
            raise DrawnRouteError("a waypoint ordinate must be finite")
    if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
        raise DrawnRouteError("a waypoint is out of range")
    return float(lon), float(lat)


def clean_waypoints(raw: Any) -> list[list[float | None]]:
    """Validate posted waypoints and return them as stored points.

    Accepts the browser's ``[[lon, lat], …]`` and returns
    ``[[lon, lat, None], …]`` — the ``Route.points`` shape, in GeoJSON axis
    order, with a null height on every point because none was recorded.
    Consecutive waypoints within ``_COINCIDENT_M`` of each other collapse to
    one.

    Args:
        raw: The decoded ``points`` value of the request body.

    Returns:
        At least two stored points.

    Raises:
        DrawnRouteError: When the value is not a list of valid pairs, holds
            more than ``gpx.MAX_POINTS`` of them, has fewer than two distinct
            ones, or is longer than ``MAX_DRAWN_DISTANCE_M``.

    """
    if not isinstance(raw, list):
        raise DrawnRouteError("points must be a list")
    if len(raw) > MAX_POINTS:
        raise DrawnRouteError("too many waypoints")

    points: list[list[float | None]] = []
    for element in raw:
        lon, lat = _coordinate(element)
        point: list[float | None] = [
            round(lon, _COORDINATE_PRECISION),
            round(lat, _COORDINATE_PRECISION),
            None,
        ]
        if points and cumulative_distances([points[-1], point])[-1] < _COINCIDENT_M:
            continue
        points.append(point)

    if len(points) < 2:
        raise DrawnRouteError("a route needs two distinct waypoints")
    if cumulative_distances(points)[-1] > MAX_DRAWN_DISTANCE_M:
        raise DrawnRouteError("the route is too long")
    return points


def clean_name(raw: Any) -> str:
    """Return the posted name, stripped and cut to the column's length.

    Args:
        raw: The decoded ``name`` value of the request body, if any.

    Returns:
        The name, or "" when none was given.

    """
    if not isinstance(raw, str):
        return ""
    return raw.strip()[:_NAME_MAX_LENGTH]


def _bounds(points: list[list[float | None]]) -> list[float]:
    """Return the GeoJSON bbox over ``points``.

    Args:
        points: Stored points, every ordinate set.

    Returns:
        ``[min_lon, min_lat, max_lon, max_lat]``.

    """
    longitudes = [float(point[0]) for point in points if point[0] is not None]
    latitudes = [float(point[1]) for point in points if point[1] is not None]
    return [min(longitudes), min(latitudes), max(longitudes), max(latitudes)]


def draft_route(
    points: list[list[float | None]], name: str = "", user: User | None = None
) -> Route:
    """Build an UNSAVED Route from clean waypoints.

    Carries every derived figure an upload's row carries, so the Feature
    builder and ``Route.climb`` read it exactly as they read a saved row.
    ``slope_samples`` is left null for the caller to fill.

    Args:
        points: The output of ``clean_waypoints``.
        name: The route's name, already cleaned.
        user: The owner, when the draft is about to be saved.

    Returns:
        A Route instance that has not been written to the database.

    """
    route = Route(
        name=name,
        source=Route.Source.DRAWN,
        points=points,
        distance_m=cumulative_distances(points)[-1],
        point_count=len(points),
        bounds=_bounds(points),
    )
    if user is not None:
        route.user = user
    return route
