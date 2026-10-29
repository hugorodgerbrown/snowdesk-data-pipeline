"""
apps/routes/services/ski_tours.py — finding SAC's ski tours on the map (SNOW-1278).

A ``SkiTour`` is reference data: one of SAC's named tours as swisstopo
publishes it (SNOW-1276). Nobody owns it. It becomes a ``Route`` only when
someone saves it, as a copy (``save_ski_tour``) through the same
``write_route_copy`` a claimed share and a saved trip go through.

THE SUMMIT IS THE HANDLE. Drawn together the tours are one web, and a tap
on a line near a hut can mean six of them. So each tour's target — the
summit or hut it climbs to, or comes down from — is a labelled marker, and
a reader chooses a tour by where it goes. swisstopo names the target
(``target_name``, ``target_altitude_m``) but gives it no coordinate, so one
is found: a tour's TARGET END is the end of its line at the target — the
last point of a way up, the first of a way down — and a summit stands at
the median of its tours' target ends. A tour drawn the other way round is
caught by taking, for each tour, whichever end lies nearer that median.
Measured on the 2,941 tours: half the summits with two or more tours have
their target ends within 30 m of each other.

A WAY DOWN is a tour with a descent and no ascent: SAC's "Abfahrt …",
"Descente …" and "Discesa …" lines, about one in ten. An UP-AND-BACK tour
is a way up with a descent figure too, skied back down the way it climbed:
SAC's "as ascent", 2,503 of the 2,941 (SNOW-1286). Its line is published
one way only, so ``tour_points`` returns it there and back — the panel's
profile, legs, distance and estimate then include the descent, as the
tour does.

THE TERRAIN IS SAMPLED ONCE PER TOUR. Walking the terrain grid under a
10 km line takes seconds, too long for a tap. So the panel opens at once on
the line and SAC's figures, and the profile follows: the first reader to
open a tour samples it (``tour_slope_samples``) and the record is kept on
the tour, for every later reader and for every saved copy, until a sync
rewrites the tour and clears it.

The pure helpers take plain values; ``summits`` and ``tour_overview`` each
read every tour once and are cached until the tours change (a weekly
import at most).
"""

from __future__ import annotations

import logging
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from django.db.models import Count, Max
from django.utils.text import slugify
from django.utils.translation import get_language

from apps.routes.models import Route, SkiTour
from apps.routes.services.drawn import draft_route
from apps.routes.services.shares import write_route_copy
from apps.routes.services.slope_segments import (
    build_slope_samples,
    enqueue_route_slope_sampling,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# A target named like this is a hut, drawn with a hut glyph, not a summit's.
_HUT_RE = re.compile(
    r"h(ü|ue|u)tte|cabane|capanna|rifugio|chamanna|chamonna|camona|biwak|"
    r"bivacco|bivouac|refuge|\bhut\b",
    re.IGNORECASE,
)

# Metres in a degree of latitude, for the short distances compared here.
_M_PER_DEG = 111_320.0

# v2: SNOW-1282 chains a tour's parts, which moves some summits.
_SUMMITS_CACHE_KEY = "routes:ski-summits:v2:{count}:{stamp}"
_SUMMITS_CACHE_SECONDS = 7 * 24 * 3600

# SNOW-1285: every tour at once, for a map zoomed out past the per-view
# load. Thinned to about 20 m (Douglas-Peucker in degrees) and rounded to
# five places (about a metre): at z9-z11 a pixel is 20-150 m, so nothing
# drawn moves, and the whole set is ~2.3 MB, ~0.6 MB gzipped.
_OVERVIEW_CACHE_KEY = "routes:ski-overview:v1:{count}:{stamp}"
_THERE_AND_BACK = "there_and_back"
_OVERVIEW_TOLERANCE_DEG = 0.0002
_OVERVIEW_PLACES = 5

Point = tuple[float, float]


@dataclass(frozen=True)
class Summit:
    """Where tours go: one target, with the tours up to it and down from it."""

    key: str
    name: str
    altitude_m: int | None
    hut: bool
    lon: float
    lat: float
    up: int
    down: int

    def as_properties(self) -> dict[str, Any]:
        """Return the summit as GeoJSON properties."""
        return {
            "key": self.key,
            "name": self.name,
            "altitude_m": self.altitude_m,
            "kind": "hut" if self.hut else "summit",
            "up": self.up,
            "down": self.down,
        }


def is_hut(name: str) -> bool:
    """Return whether a target is a hut rather than a summit, by its name."""
    return bool(_HUT_RE.search(name or ""))


def is_descent(tour: SkiTour) -> bool:
    """Return whether a tour is a way down: a descent and no ascent."""
    return tour.ascent_m is None and tour.descent_m is not None


def is_up_and_back(tour: SkiTour) -> bool:
    """Return whether a tour is skied back down the way it climbed (SNOW-1286).

    SAC gives such a tour both figures and one line: a way up with a
    descent, which its guide describes as "as ascent".
    """
    return tour.ascent_m is not None and tour.descent_m is not None


def summit_key(name: str, altitude_m: int | None) -> str:
    """Return a target's key: its name and height, so two Piz Buins stay two."""
    return f"{slugify(name) or 'target'}-{altitude_m or 0}"


def tour_key(tour: SkiTour) -> str:
    """Return the key of the summit ``tour`` goes to."""
    return summit_key(tour.target_name, tour.target_altitude_m)


def _lines(tour: SkiTour) -> list[list[list[float]]]:
    """Return the tour's lines, skipping any too short to draw, chained.

    A tour in several parts is read as one line (``_chained``), so its ends,
    its points and the route saved from it run from start to finish.
    """
    geometry = tour.geometry or {}
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") == "LineString":
        coordinates = [coordinates]
    lines = [line for line in coordinates if isinstance(line, list) and len(line) >= 2]
    return _chained(lines)


# The most parts a tour is chained from; the archive's most is 7. Past it
# the stored order stands rather than an exhaustive search.
_MAX_CHAIN_PARTS = 12


_Node = tuple[float, float]
# One part of a tour as the chaining sees it: its two ends and its length.
_Edge = tuple[_Node, _Node, float]
# A path through the parts: each part's index, and whether it is walked the
# way it was drawn.
_Path = list[tuple[int, bool]]


def _node(point: list[float]) -> _Node:
    """Return a part end as a graph node: the point to the micro-degree."""
    return (round(float(point[0]), 6), round(float(point[1]), 6))


def _line_metres(line: list[list[float]]) -> float:
    """Return a line's length in metres."""
    return sum(_metres((a[0], a[1]), (b[0], b[1])) for a, b in zip(line, line[1:]))


def _all_joined(edges: list[_Edge], graph: dict[_Node, list[int]]) -> bool:
    """Return whether every part can be reached from the first."""
    reached, stack = {edges[0][0]}, [edges[0][0]]
    while stack:
        node = stack.pop()
        for index in graph[node]:
            for other in edges[index][:2]:
                if other not in reached:
                    reached.add(other)
                    stack.append(other)
    return len(reached) == len(graph)


def _longest_path(edges: list[_Edge], graph: dict[_Node, list[int]]) -> _Path:
    """Return the longest path through the parts, starting at a loose end."""
    best: tuple[float, _Path] = (-1.0, [])

    def walk(node: _Node, used: _Path, metres: float) -> None:
        nonlocal best
        if metres > best[0]:
            best = (metres, list(used))
        for index in graph[node]:
            if any(index == u for u, _ in used):
                continue
            start, end, part_metres = edges[index]
            forward = start == node
            used.append((index, forward))
            walk(end if forward else start, used, metres + part_metres)
            used.pop()

    leaves = [node for node, parts in graph.items() if len(parts) == 1]
    for node in leaves or list(graph):
        walk(node, [], 0.0)
    return best[1]


def _chained(lines: list[list[list[float]]]) -> list[list[list[float]]]:
    """Return a tour's parts as one line from start to finish, when they join.

    SNOW-1282. swisstopo stores 258 of the SAC tours in parts, out of order,
    and as a tree: the way itself plus a short spur (a variant, or the last
    metres to a ski depot) meeting it at a shared point. Joined in stored
    order they made a line that doubled back on itself. The way is taken
    to be the longest path between two loose ends, and it runs in the
    direction swisstopo drew most of it, each part being drawn the way the
    tour goes.

    Parts that do not all join (two tours cross a gap in swisstopo's own
    data) and one-part tours pass through as stored.

    Args:
        lines: The tour's parts, each two points or more.

    Returns:
        ``[line]``, or ``lines`` unchanged when they do not form one tree.

    """
    if len(lines) < 2 or len(lines) > _MAX_CHAIN_PARTS:
        return lines
    edges = [(_node(line[0]), _node(line[-1]), _line_metres(line)) for line in lines]
    graph: dict[_Node, list[int]] = defaultdict(list)
    for index, (start, end, _part_metres) in enumerate(edges):
        graph[start].append(index)
        graph[end].append(index)
    if not _all_joined(edges, graph):
        return lines

    path = _longest_path(edges, graph)
    total = sum(edges[i][2] for i, _ in path)
    with_the_drawing = sum(edges[i][2] for i, forward in path if forward)
    if with_the_drawing * 2 < total:
        path = [(i, not forward) for i, forward in reversed(path)]
    line: list[list[float]] = []
    for index, forward in path:
        part = lines[index] if forward else lines[index][::-1]
        line.extend(part[1:] if line else part)
    return [line]


def ends(tour: SkiTour) -> tuple[Point, Point]:
    """Return the tour's ``(first, last)`` points.

    Raises:
        ValueError: The tour has no line.

    """
    lines = _lines(tour)
    if not lines:
        raise ValueError(f"tour {tour.pk} has no line")
    first, last = lines[0][0], lines[-1][-1]
    return (float(first[0]), float(first[1])), (float(last[0]), float(last[1]))


def _metres(a: Point, b: Point) -> float:
    """Return the flat-earth distance between two nearby points, in metres."""
    kx = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * kx, a[1] - b[1]) * _M_PER_DEG


def target_end(tour: SkiTour, near: Point | None = None) -> Point:
    """Return the end of ``tour`` at its target.

    Args:
        tour: The tour.
        near: Where its summit stands, when known: the end nearer it wins,
            so a tour drawn the other way round is still read right.

    Returns:
        ``(lon, lat)``.

    """
    first, last = ends(tour)
    if near is not None:
        return first if _metres(first, near) < _metres(last, near) else last
    return first if is_descent(tour) else last


def far_end(tour: SkiTour, summit: Point) -> Point:
    """Return the end of ``tour`` away from its summit: where it starts or finishes."""
    first, last = ends(tour)
    return last if _metres(first, summit) < _metres(last, summit) else first


def _median(points: list[Point]) -> Point:
    """Return the per-axis median of ``points``."""
    return (
        statistics.median(p[0] for p in points),
        statistics.median(p[1] for p in points),
    )


def build_summits(tours: list[SkiTour]) -> list[Summit]:
    """Return every target the tours go to, placed at its tours' target ends.

    Args:
        tours: The tours, with their geometry loaded.

    Returns:
        One summit per target, in key order.

    """
    groups: dict[str, list[SkiTour]] = defaultdict(list)
    for tour in tours:
        if tour.target_name and _lines(tour):
            groups[tour_key(tour)].append(tour)
    summits = []
    for key in sorted(groups):
        members = groups[key]
        first_guess = _median([target_end(tour) for tour in members])
        lon, lat = _median([target_end(tour, first_guess) for tour in members])
        down = sum(1 for tour in members if is_descent(tour))
        sample = members[0]
        summits.append(
            Summit(
                key=key,
                name=sample.target_name,
                altitude_m=sample.target_altitude_m,
                hut=is_hut(sample.target_name),
                lon=round(lon, 6),
                lat=round(lat, 6),
                up=len(members) - down,
                down=down,
            )
        )
    return summits


def _tours_stamp(template: str) -> str:
    """Return a cache key from ``template`` that changes when the tours do.

    It carries the tours' count and newest ``updated_at``, so an import
    that adds, rewrites or removes a tour reads afresh.
    """
    stamp = SkiTour.objects.aggregate(count=Count("id"), newest=Max("updated_at"))
    return template.format(
        count=stamp["count"],
        stamp=stamp["newest"].timestamp() if stamp["newest"] else 0,
    )


def summits() -> dict[str, Summit]:
    """Return every summit, by key, cached until the tours change."""
    key = _tours_stamp(_SUMMITS_CACHE_KEY)
    cached: dict[str, Summit] | None = cache.get(key)
    if cached is None:
        cached = {
            summit.key: summit
            for summit in build_summits(
                list(
                    SkiTour.objects.only(
                        "id",
                        "geometry",
                        "target_name",
                        "target_altitude_m",
                        "ascent_m",
                        "descent_m",
                    )
                )
            )
        }
        cache.set(key, cached, _SUMMITS_CACHE_SECONDS)
    return cached


def overview_geometry(geometry: dict[str, Any]) -> dict[str, Any] | None:
    """Return a tour's line thinned for a zoomed-out map, or None.

    Args:
        geometry: The tour's stored GeoJSON LineString or MultiLineString.

    Returns:
        A MultiLineString of (lon, lat) pairs, or None when nothing in it
        is long enough to draw.

    """
    from shapely.geometry import shape  # noqa: PLC0415 — see gpx._simplify

    coordinates = (geometry or {}).get("coordinates") or []
    if (geometry or {}).get("type") == "LineString":
        coordinates = [coordinates]
    lines = []
    for line in coordinates:
        if not isinstance(line, list) or len(line) < 2:
            continue
        thinned = shape(
            {"type": "LineString", "coordinates": [point[:2] for point in line]}
        ).simplify(_OVERVIEW_TOLERANCE_DEG, preserve_topology=False)
        lines.append(
            [
                [round(lon, _OVERVIEW_PLACES), round(lat, _OVERVIEW_PLACES)]
                for lon, lat, *_ in thinned.coords
            ]
        )
    if not lines:
        return None
    return {"type": "MultiLineString", "coordinates": lines}


def tour_overview() -> list[dict[str, Any]]:
    """Return every tour as a thinned line Feature, cached until they change.

    SNOW-1285. What a map zoomed out past the per-view load draws, and is
    tapped on: the line and only what a tap and the highlight read (its
    uuid, its summit's key, whether it is a way down). The panel and the
    list ask the server for the rest. Not localised, so one copy serves
    every language.

    Returns:
        The Features, in SAC id order.

    """
    key = _tours_stamp(_OVERVIEW_CACHE_KEY)
    cached: list[dict[str, Any]] | None = cache.get(key)
    if cached is None:
        cached = []
        tours = SkiTour.objects.order_by("sac_id").only(
            "source_uuid",
            "geometry",
            "target_name",
            "target_altitude_m",
            "ascent_m",
            "descent_m",
        )
        for tour in tours.iterator():
            geometry = overview_geometry(tour.geometry)
            if geometry is None:
                continue
            cached.append(
                {
                    "type": "Feature",
                    "id": str(tour.source_uuid),
                    "geometry": geometry,
                    "properties": {
                        "kind": "tour",
                        "uuid": str(tour.source_uuid),
                        "summit": tour_key(tour),
                        "down": is_descent(tour),
                    },
                }
            )
        cache.set(key, cached, _SUMMITS_CACHE_SECONDS)
    return cached


def localised(values: dict[str, str], fallback: str) -> str:
    """Return the value for the active language, else ``fallback``.

    Args:
        values: A ``{"de": …, "fr": …}`` mapping from the tour.
        fallback: The tour's own value, in the language SAC wrote it in.

    Returns:
        The text to show.

    """
    language = (get_language() or "en")[:2]
    return (values or {}).get(language) or fallback


def tour_title(tour: SkiTour) -> str:
    """Return a tour's title: its target, then SAC's name for the way.

    "Signalhorn (GR) · Von der Chamonna Tuoi CAS". A tour with no target is
    its name alone.
    """
    name = localised(tour.names, tour.name)
    return f"{tour.target_name} · {name}" if tour.target_name else name


def tour_points(tour: SkiTour) -> list[list[float | None]]:
    """Return the tour's line as stored route points, ``[[lon, lat, None], …]``.

    A tour in several parts (swisstopo breaks one where it crosses a gap in
    its own data) is joined end to end; a point repeating the last is
    dropped. An up-and-back tour comes back down its own line, so the line
    is followed by itself reversed (SNOW-1286).

    Args:
        tour: The tour.

    Returns:
        The points.

    """
    points: list[list[float | None]] = []
    for line in _lines(tour):
        for lon, lat, *_ in line:
            point: list[float | None] = [float(lon), float(lat), None]
            if points and points[-1][:2] == point[:2]:
                continue
            points.append(point)
    if is_up_and_back(tour) and len(points) > 1:
        points += [list(point) for point in reversed(points[:-1])]
    return points


def draft_tour_route(tour: SkiTour, user: User | None = None) -> Route:
    """Return an UNSAVED Route of one tour, as the panel describes and Save stores.

    Args:
        tour: The tour.
        user: The owner, when it is about to be saved.

    Returns:
        A Route with the tour's line and title, ``source = SKI_TOUR``.

    """
    route = draft_route(tour_points(tour), name=tour_title(tour)[:100], user=user)
    route.source = Route.Source.SKI_TOUR
    route.ski_tour_uuid = tour.source_uuid
    return route


def sac_time(label: str) -> str:
    """Return SAC's guide time as the app writes a duration: "03:30 h" → "3 h 30".

    A label that is not ``HH:MM h`` passes through as SAC wrote it.
    """
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*h?\s*", label or "")
    if not match:
        return (label or "").strip()
    hours, minutes = int(match.group(1)), match.group(2)
    if minutes == "00":
        return f"{hours} h"
    return f"{hours} h {minutes}" if hours else f"{int(minutes)} min"


def tour_slope_samples(tour: SkiTour, *, sample: bool) -> dict[str, Any] | None:
    """Return the terrain under ``tour``'s line, sampling it when asked to.

    Args:
        tour: The tour.
        sample: Walk the terrain grid when the tour has never been sampled,
            and keep the record on the tour. Otherwise answer only what is
            held.

    Returns:
        The record, or None when it is not held (or the line lies outside
        the terrain model).

    """
    held: dict[str, Any] | None = tour.slope_samples
    # A record walked before SNOW-1286 covers an up-and-back tour's way up
    # alone, so it no longer matches the line: it is not held.
    if held is not None and is_up_and_back(tour) and not held.get(_THERE_AND_BACK):
        held = None
    if held is not None or not sample:
        return held
    record = build_slope_samples(tour_points(tour), f"ski tour {tour.sac_id}")
    if record is not None:
        if is_up_and_back(tour):
            record[_THERE_AND_BACK] = True
        tour.slope_samples = record
        SkiTour.objects.filter(pk=tour.pk).update(slope_samples=record)
    return record


def tour_properties(tour: SkiTour) -> dict[str, Any]:
    """Return what the tour panel says about a tour beyond its line's figures.

    SAC's own figures — its grade, height gain and time — are kept apart
    from the profile's: they are the guide's, and the panel names them so.
    """
    return {
        "tour_uuid": str(tour.source_uuid),
        "tour_name": localised(tour.names, tour.name),
        "target_name": tour.target_name,
        "target_altitude_m": tour.target_altitude_m,
        "grade": localised(tour.grades, tour.grade) or tour.grade,
        "sac_ascent_m": tour.ascent_m,
        "sac_descent_m": tour.descent_m,
        "sac_time": sac_time(tour.ascent_time_label),
        "sac_url": localised(tour.urls, tour.urls.get("de", "")),
        "down": is_descent(tour),
        "same_way": is_up_and_back(tour),
    }


def save_ski_tour(user: User, tour: SkiTour) -> Route:
    """Copy one tour onto ``user``'s routes.

    Through ``write_route_copy``, so the per-account cap is checked exactly
    as a claimed share's is. The terrain the tour holds is copied with it;
    a tour never opened is sampled after the commit, as a drawn route is.

    Args:
        user: The account saving it.
        tour: The tour.

    Returns:
        The new Route.

    Raises:
        apps.routes.services.routes.RouteLimitReached: ``user`` is at the cap.

    """
    draft = draft_tour_route(tour)
    fields = {
        field: getattr(draft, field)
        for field in (
            "name",
            "source",
            "ski_tour_uuid",
            "points",
            "distance_m",
            "point_count",
            "bounds",
        )
    }
    fields["slope_samples"] = tour.slope_samples
    route = write_route_copy(user, fields)
    logger.info(
        "Ski tour saved: user=%s uuid=%s tour=%s", user.pk, route.uuid, tour.source_uuid
    )
    if route.slope_samples is None:
        enqueue_route_slope_sampling(route)
    return route
