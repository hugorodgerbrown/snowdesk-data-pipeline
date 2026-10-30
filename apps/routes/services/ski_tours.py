"""
apps/routes/services/ski_tours.py — finding SAC's ski tours on the map (SNOW-1278).

A ``SkiTour`` is reference data: one of SAC's named tours as swisstopo
publishes it (SNOW-1276). Nobody owns it. It becomes a ``Route`` only when
someone saves it, as a copy (``save_ski_tour``) through the same
``write_route_copy`` a claimed share and a saved trip go through.

EACH SUMMIT IS A LABEL. A tour is chosen by its line (SNOW-1306), and each
tour's target — the summit or hut it climbs to, or comes down from — is a
labelled marker that says where the lines go, and that orders a line from
its top (``runs_reversed``). swisstopo names the target
(``target_name``, ``target_altitude_m``) but gives it no coordinate, so one
is found: a tour's TARGET END is the end of its line at the target — the
last point of a way up, the first of a way down — and a summit stands at
the median of its tours' target ends. A tour drawn the other way round is
caught by taking, for each tour, whichever end lies nearer that median.
Measured on the 2,941 tours: half the summits with two or more tours have
their target ends within 30 m of each other.

A TOUR IS A WAY UP OR A WAY DOWN (Hugo, SNOW-1304): SAC publishes the
ways up and the ways down to a summit separately, so a reader can climb one and ski
another. A WAY DOWN is a tour SAC itself calls one: named "Abfahrt …",
"Descente …", "Discesa …" or "Descent …" in any of its languages, or given
a descent figure and no ascent — 403 of the 2,941. The figures alone are
not enough: SAC gives some named descents an ascent too (the re-climb on
the way down, "Descente à la cabane de Chanrion", ↑420 ↓1,660). The
order of SAC's points is no guide at all: it draws 2,743 of its lines
from the valley to the summit, whichever way the tour goes. The panel infers
nothing beyond that (Hugo, SNOW-1302): it says the line shows the ascent
only or the descent only, and points at SAC's guide for the rest. SAC's own
height figures are not shown; the panel's figures are the line's own.
That label orders the line and names the panel's half; it is never drawn.
EACH TOUR IS DRAWN AS ITS LEGS (Hugo, SNOW-1310: "The SAC routes should
behave the same as user routes, but in a blue colour scheme"): cut from
its terrain where the ground turns, on the map at rest (``leg_parts``) and
in the panel alike. A tour with no terrain yet is one line in no direction.

SAC'S POINTS ARE NEVER EDITED (Hugo, SNOW-1287). A tour that stops at the
top stops there here too: nothing is added for the way back down, though
SNOW-1286 briefly drew such a tour there and back.

THE TERRAIN IS SAMPLED ONCE PER TOUR, AHEAD OF TIME. Walking the terrain
grid under a 10 km line takes seconds, too long for a tap. So every tour is
walked in advance by ``backfill_ski_tour_slope_samples`` (SNOW-1288, the
nightly ``reconcile_slope_samples`` job; ``tours_to_sample`` names the
rows) and the record is kept on the tour, for every reader and every saved
copy, until a sync rewrites the tour and clears it. A tour opened before
the job reaches it falls back to the first reader's open: the panel shows
the line and SAC's figures at once and the profile follows
(``tour_slope_samples`` with ``sample``).

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
from django.db.models import Count, F, Max, Q, QuerySet
from django.db.models.fields.json import KT
from django.utils.text import slugify
from django.utils.translation import get_language

from apps.routes.models import Route, SkiTour
from apps.routes.services.drawn import draft_route
from apps.routes.services.shares import write_route_copy
from apps.routes.services.slope_segments import (
    build_slope_samples,
    enqueue_route_slope_sampling,
)
from apps.routes.services.track_render import (
    is_current,
    leg_lines,
    stale_q,
    store_terrain,
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
_SUMMITS_CACHE_KEY = "routes:ski-summits:v3:{count}:{stamp}"
_SUMMITS_CACHE_SECONDS = 7 * 24 * 3600

# SNOW-1285: every tour at once, for a map zoomed out past the per-view
# load. Thinned to about 20 m (Douglas-Peucker in degrees) and rounded to
# five places (about a metre): at z9-z11 a pixel is 20-150 m, so nothing
# drawn moves, and the whole set is ~2.3 MB, ~0.6 MB gzipped.
# v3: SNOW-1310 cuts each tour into its legs.
_OVERVIEW_CACHE_KEY = "routes:ski-overview:v3:{count}:{stamp}"
# The mark SNOW-1286 put on a record walked there and back (SNOW-1287).
_THERE_AND_BACK = "there_and_back"
# SNOW-1306: on a terrain record, whether its walk ran SAC's line in
# reverse. A record without it was walked in SAC's own order. Its heights
# then say which way SAC's line really runs (``runs_reversed``).
_WALKED_REVERSED = "walked_reversed"
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


# SAC's own word for a way down, at the start of its name in any language.
_DESCENT_NAME_RE = re.compile(
    r"^\s*(abfahrt|descente|discesa|descent)\b", re.IGNORECASE
)


def is_descent(tour: SkiTour) -> bool:
    """Return whether a tour is a way down, as SAC itself says (SNOW-1304).

    SAC names it one in any of its languages, or gives it a descent figure
    and no ascent. Reads ``name``, ``names``, ``ascent_m`` and ``descent_m``.
    """
    if tour.ascent_m is None and tour.descent_m is not None:
        return True
    names = [tour.name, *(tour.names or {}).values()]
    return any(_DESCENT_NAME_RE.match(name or "") for name in names)


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
            Without it, the last point: SAC draws 2,743 of its 2,941 lines
            from the valley to the summit, ways down included (SNOW-1304).

    Returns:
        ``(lon, lat)``.

    """
    first, last = ends(tour)
    if near is not None:
        return first if _metres(first, near) < _metres(last, near) else last
    return last


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
                        "name",
                        "names",
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
    tapped on: the line, cut into its legs (``leg_parts``, SNOW-1310), and
    only what a tap reads (its uuid, its summit's key) and the leg's
    ``climbing``. The panel and the
    list ask the server for the rest. Not localised, so one copy serves
    every language.

    Returns:
        The Features, in SAC id order.

    """
    key = _tours_stamp(_OVERVIEW_CACHE_KEY)
    cached: list[dict[str, Any]] | None = cache.get(key)
    if cached is None:
        cached = []
        tours = drawn_tours(SkiTour.objects.order_by("sac_id")).only(
            "source_uuid",
            "geometry",
            "name",
            "names",
            "target_name",
            "target_altitude_m",
            "ascent_m",
            "descent_m",
        )
        for tour in tours.iterator(chunk_size=200):
            # SNOW-1310: a Feature per leg, each thinned on its own.
            for climbing, line in leg_parts(layer_render(tour), tour.geometry):
                geometry = overview_geometry(
                    {"type": "LineString", "coordinates": line}
                )
                if geometry is None:
                    continue
                cached.append(
                    {
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": {
                            "kind": "tour",
                            "uuid": str(tour.source_uuid),
                            "summit": tour_key(tour),
                            "climbing": climbing,
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


def _usable(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return ``record`` unless it was walked there and back (SNOW-1287)."""
    if record is None or record.get(_THERE_AND_BACK):
        return None
    return record


def _runs_up(record: dict[str, Any]) -> bool | None:
    """Return whether a record's walk climbed, by its first and last heights.

    None when it holds fewer than two heights, or they are level.
    """
    heights = [h for h in record.get("heights") or [] if h is not None]
    if len(heights) < 2 or heights[0] == heights[-1]:
        return None
    return bool(heights[-1] > heights[0])


def _guess_reversed(tour: SkiTour) -> bool:
    """Return whether to turn SAC's line round, from the summit alone.

    The top is the end nearer the summit, when other tours to it place it.
    A summit with one tour is placed at that tour's own last point, so it
    says nothing: SAC's line is then taken to run up, as 2,743 of its
    2,941 do, and a way down is turned round.
    """
    _, last = ends(tour)
    summit = summits().get(tour_key(tour))
    near = (summit.lon, summit.lat) if summit and summit.up + summit.down > 1 else None
    top_is_last = target_end(tour, near) == last
    return top_is_last if is_descent(tour) else not top_is_last


def runs_reversed(tour: SkiTour) -> bool:
    """Return whether ``tour_points`` turns SAC's line round (SNOW-1306).

    A way down starts at its summit and a way up ends there, whichever way
    SAC drew it. The terrain says which way that is once the tour has been
    walked: its record's first and last heights, in the order it was
    walked. Before that, the summit's position is the guess
    (``_guess_reversed``), and the first walk corrects it
    (``tour_slope_samples``).

    Args:
        tour: The tour, with its geometry loaded.

    Returns:
        True when the line's last point is where it should start.

    """
    if not _lines(tour):
        return False
    record = _usable(tour.slope_samples)
    up = _runs_up(record) if record is not None else None
    if record is not None and up is not None:
        sac_runs_up = up != bool(record.get(_WALKED_REVERSED, False))
        return sac_runs_up == is_descent(tour)
    return _guess_reversed(tour)


def tour_points(
    tour: SkiTour, *, reverse: bool | None = None
) -> list[list[float | None]]:
    """Return the tour's line as stored route points, ``[[lon, lat, None], …]``.

    A tour in several parts (swisstopo breaks one where it crosses a gap in
    its own data) is joined end to end; a point repeating the last is
    dropped. The line then runs the way the tour goes: a way down from its
    summit, a way up to it (Hugo, SNOW-1306, ``runs_reversed``). Nothing
    else: no point is added or moved (SNOW-1287), so a tour that stops at
    the top stops there.

    Args:
        tour: The tour.
        reverse: Turn SAC's line round or not, in place of
            ``runs_reversed``: the walk that settles it asks for both.

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
    if runs_reversed(tour) if reverse is None else reverse:
        points.reverse()
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


def tours_to_sample() -> QuerySet[SkiTour]:
    """Return the tours with no current terrain record (SNOW-1288).

    Never walked, walked over SNOW-1286's doubled line (the record
    ``tour_slope_samples`` does not hold), or walked before SNOW-1306 and
    not yet known to match the line's order, or with no current render
    (SNOW-1309).
    """
    return SkiTour.objects.filter(
        Q(slope_samples__isnull=True)
        | Q(slope_samples__has_key=_THERE_AND_BACK)
        | ~Q(slope_samples__has_key=_WALKED_REVERSED)
        | stale_q()
    )


def needs_only_marking(tour: SkiTour) -> bool:
    """Return whether ``tour``'s record is current but for a mark or its render.

    Walked in SAC's order before a way down ran from its top, on a tour
    whose order does not change, or with no current render (SNOW-1309):
    ``tour_slope_samples(sample=True)`` marks it and builds its render
    without walking again, so the nightly backfill need not pause for it.
    """
    held = _usable(tour.slope_samples)
    return (
        held is not None
        and bool(held.get(_WALKED_REVERSED, False)) == runs_reversed(tour)
        and (_WALKED_REVERSED not in held or not is_current(tour.track_render))
    )


def tour_slope_samples(tour: SkiTour, *, sample: bool) -> dict[str, Any] | None:
    """Return the terrain under ``tour``'s line, sampling it when asked to.

    The record is walked in the order ``tour_points`` gives, and says which
    (``walked_reversed``). Its heights then settle the order: when they show
    the walk ran the wrong way for the tour — a guess from a summit with
    one tour — the line is walked again the other way round (SNOW-1306).

    Args:
        tour: The tour.
        sample: Walk the terrain grid when the tour holds no record for its
            line's order, and keep the record on the tour. Otherwise answer
            only what is held.

    Returns:
        The record, or None when it is not held (or the line lies outside
        the terrain model).

    """
    # A record SNOW-1286 walked over the line there and back no longer
    # matches the line, which is SAC's own again (SNOW-1287): not held.
    held = _usable(tour.slope_samples)
    if held is not None:
        reverse = runs_reversed(tour)
        if bool(held.get(_WALKED_REVERSED, False)) != reverse:
            # Walked in the other order: its indices are not the line's.
            held = None
        elif sample and (
            _WALKED_REVERSED not in held or not is_current(tour.track_render)
        ):
            # Marked, and its render built (SNOW-1309), without a walk.
            held = {**held, _WALKED_REVERSED: reverse}
            _keep(tour, held)
    if held is not None or not sample:
        return held
    reverse = runs_reversed(tour)
    label = f"ski tour {tour.sac_id}"
    record = build_slope_samples(tour_points(tour, reverse=reverse), label)
    up = _runs_up(record) if record is not None else None
    if up is not None and up == is_descent(tour):
        reverse = not reverse
        record = build_slope_samples(tour_points(tour, reverse=reverse), label)
    if record is not None:
        record = {**record, _WALKED_REVERSED: reverse}
        _keep(tour, record)
    return record


def _keep(tour: SkiTour, record: dict[str, Any]) -> None:
    """Store ``record`` on ``tour``, in memory and in the row, with its render.

    SNOW-1309: through ``store_terrain``, so the tour holds its line drawn
    as a route's is, cut into legs along the line in the order the tour
    goes. ``updated_at`` moves with it, which the overview's cache key reads.
    """
    tour.slope_samples = record
    store_terrain(tour, record, points=tour_points(tour))


def rebuild_tour_render(tour: SkiTour) -> bool:
    """Rebuild ``tour``'s render from the record it holds, walking nothing.

    For ``rebuild_track_renders``: a tour holding no record for its line's
    order has no render to build — the tours layer draws SAC's line — and
    is left for the panel or the nightly backfill to walk.

    Args:
        tour: The tour.

    Returns:
        Whether a render was stored.

    """
    record = tour_slope_samples(tour, sample=False)
    if record is None:
        return False
    _keep(tour, record)
    return True


def leg_parts(
    render: dict[str, Any] | None, geometry: dict[str, Any] | None
) -> list[tuple[bool | None, list[list[float]]]]:
    """Return what the tours layer draws of one tour: its legs (SNOW-1310).

    Hugo, 2026-10-30: "The SAC routes should behave the same as user
    routes, but in a blue colour scheme". So the layer draws the legs the
    open panel shows, read from the tour's stored render (``leg_lines``,
    SNOW-1309). A tour with none — its terrain not walked yet — is SAC's
    own stored line, ``climbing`` None: no direction is claimed for it.
    Both are reads; nothing is cut per request.

    Args:
        render: The tour's ``track_render``, or None.
        geometry: The tour's stored GeoJSON line.

    Returns:
        ``[(climbing, [[lon, lat], …]), …]``.

    """
    legs: list[tuple[bool | None, list[list[float]]]] = list(leg_lines(render))
    if legs:
        return legs
    # SAC's stored parts as published, spurs included: not ``_lines``,
    # which chains them into one route and drops a branch.
    coordinates = (geometry or {}).get("coordinates") or []
    if (geometry or {}).get("type") == "LineString":
        coordinates = [coordinates]
    return [
        (None, [[float(lon), float(lat)] for lon, lat, *_ in line])
        for line in coordinates
        if isinstance(line, list) and len(line) >= 2
    ]


def drawn_tours(tours: QuerySet[SkiTour]) -> QuerySet[SkiTour]:
    """Return ``tours`` carrying only what the tours layer draws of their render.

    The render's ``v``, ``geometry`` and ``legs`` as ``render_v``,
    ``render_geometry`` and ``render_legs``, and the full render deferred:
    its slope record and point-card rows are most of its size, and the
    layer reads neither. Read back with ``layer_render``.
    """
    return tours.defer("track_render", "slope_samples").annotate(
        render_v=KT("track_render__v"),
        render_geometry=F("track_render__geometry"),
        render_legs=F("track_render__legs"),
    )


def layer_render(tour: SkiTour) -> dict[str, Any] | None:
    """Return the slice of a ``drawn_tours`` row's render that the layer reads."""
    version = getattr(tour, "render_v", None)
    if version is None:
        return None
    return {
        "v": int(version),
        "geometry": getattr(tour, "render_geometry", None) or [],
        "legs": getattr(tour, "render_legs", None) or [],
    }


def tour_properties(tour: SkiTour) -> dict[str, Any]:
    """Return what the tour panel says about a tour beyond its line's figures.

    SAC's grade and time are kept apart from the profile's figures: they
    are the guide's, and the panel names them so. SAC's height figures are
    not passed on (SNOW-1302): the panel shows the line's own, and ``down``
    says which half of the tour the line is.
    """
    return {
        "tour_uuid": str(tour.source_uuid),
        "tour_name": localised(tour.names, tour.name),
        "target_name": tour.target_name,
        "target_altitude_m": tour.target_altitude_m,
        "grade": localised(tour.grades, tour.grade) or tour.grade,
        "sac_time": sac_time(tour.ascent_time_label),
        "sac_url": localised(tour.urls, tour.urls.get("de", "")),
        "down": is_descent(tour),
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
    # What the tour holds for its line: a record walked there and back is
    # not held (SNOW-1287), so the copy is sampled after the commit.
    fields["slope_samples"] = tour_slope_samples(tour, sample=False)
    route = write_route_copy(user, fields)
    logger.info(
        "Ski tour saved: user=%s uuid=%s tour=%s", user.pk, route.uuid, tour.source_uuid
    )
    if route.slope_samples is None:
        enqueue_route_slope_sampling(route)
    return route
