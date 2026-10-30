"""
track/sac.py — the order an SAC tour's line runs in, and its walk (SNOW-1316).

Pure of Django: a tour is anything with the fields of ``TourLike`` — the
instance's ``SkiTour`` row, or the ingest service's view of a
``SourceSkiTour`` document — so the service renders a tour exactly as an
instance would. ``apps.routes.services.ski_tours`` is the instance's
caller: it caches ``build_summits`` and stores what ``walk_tour`` returns.

A tour's parts are chained into one line (``tour_lines``, SNOW-1282). A
way up ends at its summit and a way down starts there (SNOW-1304/1306):
which end that is comes from the walk's own heights once walked, and from
the summit's position before (``runs_reversed``). The full account is in
``apps/routes/services/ski_tours.py``'s header.
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class TourLike(Protocol):
    """The fields of a tour this module reads."""

    geometry: Any
    name: str
    names: Any
    ascent_m: Any
    descent_m: Any
    target_name: str
    target_altitude_m: Any
    slope_samples: dict[str, Any] | None


# The walk a caller passes in: ``(points, label) -> record | None``.
Sampler = Callable[[list[list[float | None]], str], dict[str, Any] | None]


def slugify(value: str) -> str:
    """Return ``value`` as Django's ``slugify`` would, without Django.

    The same steps in the same order, so a summit's key is the same on
    either side: NFKD to ASCII, lower case, drop what is not a word
    character, a space or a hyphen, then runs of spaces and hyphens to one
    hyphen, trimmed of hyphens and underscores.
    """
    value = (
        unicodedata.normalize("NFKD", str(value))
        .encode("ascii", "ignore")
        .decode("ascii")
    )
    value = re.sub(r"[^\w\s-]", "", value.lower())
    return re.sub(r"[-\s]+", "-", value).strip("-_")


# A target named like this is a hut, drawn with a hut glyph, not a summit's.
_HUT_RE = re.compile(
    r"h(ü|ue|u)tte|cabane|capanna|rifugio|chamanna|chamonna|camona|biwak|"
    r"bivacco|bivouac|refuge|\bhut\b",
    re.IGNORECASE,
)

# Metres in a degree of latitude, for the short distances compared here.
_M_PER_DEG = 111_320.0

# The mark SNOW-1286 put on a record walked there and back (SNOW-1287).
THERE_AND_BACK = "there_and_back"
# SNOW-1306: on a terrain record, whether its walk ran SAC's line in
# reverse. A record without it was walked in SAC's own order. Its heights
# then say which way SAC's line really runs (``runs_reversed``).
WALKED_REVERSED = "walked_reversed"

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


def is_descent(tour: TourLike) -> bool:
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


def tour_key(tour: TourLike) -> str:
    """Return the key of the summit ``tour`` goes to."""
    return summit_key(tour.target_name, tour.target_altitude_m)


def tour_lines(tour: TourLike) -> list[list[list[float]]]:
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


def ends(tour: TourLike) -> tuple[Point, Point]:
    """Return the tour's ``(first, last)`` points.

    Raises:
        ValueError: The tour has no line.

    """
    lines = tour_lines(tour)
    if not lines:
        raise ValueError(f"tour {getattr(tour, 'pk', None)} has no line")
    first, last = lines[0][0], lines[-1][-1]
    return (float(first[0]), float(first[1])), (float(last[0]), float(last[1]))


def _metres(a: Point, b: Point) -> float:
    """Return the flat-earth distance between two nearby points, in metres."""
    kx = math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot((a[0] - b[0]) * kx, a[1] - b[1]) * _M_PER_DEG


def target_end(tour: TourLike, near: Point | None = None) -> Point:
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


def far_end(tour: TourLike, summit: Point) -> Point:
    """Return the end of ``tour`` away from its summit: where it starts or finishes."""
    first, last = ends(tour)
    return last if _metres(first, summit) < _metres(last, summit) else first


def _median(points: list[Point]) -> Point:
    """Return the per-axis median of ``points``."""
    return (
        statistics.median(p[0] for p in points),
        statistics.median(p[1] for p in points),
    )


def build_summits(tours: Iterable[TourLike]) -> list[Summit]:
    """Return every target the tours go to, placed at its tours' target ends.

    Args:
        tours: The tours, with their geometry loaded.

    Returns:
        One summit per target, in key order.

    """
    groups: dict[str, list[TourLike]] = defaultdict(list)
    for tour in tours:
        if tour.target_name and tour_lines(tour):
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


def usable(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return ``record`` unless it was walked there and back (SNOW-1287)."""
    if record is None or record.get(THERE_AND_BACK):
        return None
    return record


def runs_up(record: dict[str, Any]) -> bool | None:
    """Return whether a record's walk climbed, by its first and last heights.

    None when it holds fewer than two heights, or they are level.
    """
    heights = [h for h in record.get("heights") or [] if h is not None]
    if len(heights) < 2 or heights[0] == heights[-1]:
        return None
    return bool(heights[-1] > heights[0])


def _guess_reversed(tour: TourLike, summits: Mapping[str, Summit]) -> bool:
    """Return whether to turn SAC's line round, from the summit alone.

    The top is the end nearer the summit, when other tours to it place it.
    A summit with one tour is placed at that tour's own last point, so it
    says nothing: SAC's line is then taken to run up, as 2,743 of its
    2,941 do, and a way down is turned round.
    """
    _, last = ends(tour)
    summit = summits.get(tour_key(tour))
    near = (summit.lon, summit.lat) if summit and summit.up + summit.down > 1 else None
    top_is_last = target_end(tour, near) == last
    return top_is_last if is_descent(tour) else not top_is_last


def runs_reversed(tour: TourLike, summits: Mapping[str, Summit]) -> bool:
    """Return whether ``tour_points`` turns SAC's line round (SNOW-1306).

    A way down starts at its summit and a way up ends there, whichever way
    SAC drew it. The terrain says which way that is once the tour has been
    walked: its record's first and last heights, in the order it was
    walked. Before that, the summit's position is the guess
    (``_guess_reversed``), and the first walk corrects it
    (``tour_slope_samples``).

    Args:
        tour: The tour, with its geometry loaded.
        summits: Every summit by key (``build_summits``), for the guess.

    Returns:
        True when the line's last point is where it should start.

    """
    if not tour_lines(tour):
        return False
    record = usable(tour.slope_samples)
    up = runs_up(record) if record is not None else None
    if record is not None and up is not None:
        sac_runs_up = up != bool(record.get(WALKED_REVERSED, False))
        return sac_runs_up == is_descent(tour)
    return _guess_reversed(tour, summits)


def tour_points(
    tour: TourLike,
    summits: Mapping[str, Summit],
    *,
    reverse: bool | None = None,
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
        summits: Every summit by key, for ``runs_reversed``.
        reverse: Turn SAC's line round or not, in place of
            ``runs_reversed``: the walk that settles it asks for both.

    Returns:
        The points.

    """
    points: list[list[float | None]] = []
    for line in tour_lines(tour):
        for lon, lat, *_ in line:
            point: list[float | None] = [float(lon), float(lat), None]
            if points and points[-1][:2] == point[:2]:
                continue
            points.append(point)
    if runs_reversed(tour, summits) if reverse is None else reverse:
        points.reverse()
    return points


def walk_tour(
    tour: TourLike,
    summits: Mapping[str, Summit],
    sample: Sampler,
    label: str,
) -> dict[str, Any] | None:
    """Walk the terrain under ``tour``'s line, in the order the tour goes.

    The record is walked in the order ``tour_points`` gives, and says which
    (``walked_reversed``). Its heights then settle the order: when they show
    the walk ran the wrong way for the tour — a guess from a summit with
    one tour — the line is walked again the other way round (SNOW-1306).

    Args:
        tour: The tour.
        summits: Every summit by key, for the first guess at the order.
        sample: The walk itself, ``(points, label) -> record | None`` —
            ``track.slope_samples.build_slope_samples`` with its grid
            bound. Passed in, so each caller says where the tiles are.
        label: How a log line names the tour.

    Returns:
        The record, marked ``walked_reversed``, or None when the walk
        stored nothing (see ``build_slope_samples``).

    """
    reverse = runs_reversed(tour, summits)
    record = sample(tour_points(tour, summits, reverse=reverse), label)
    up = runs_up(record) if record is not None else None
    if up is not None and up == is_descent(tour):
        reverse = not reverse
        record = sample(tour_points(tour, summits, reverse=reverse), label)
    if record is None:
        return None
    return {**record, WALKED_REVERSED: reverse}
