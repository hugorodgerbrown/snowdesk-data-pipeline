"""
apps/routes/services/gpx.py — Parse an uploaded GPX file into a stored track.

One public entry point, ``parse_gpx``: raw ``.gpx`` bytes in, a
``ParsedRoute`` out. Nothing here touches the database, and nothing here
writes the uploaded file anywhere — the bytes are parsed and dropped (see
``docs/decisions/gpx-uploads-are-parsed-not-stored.md``).

What the parser accepts, and why:

* **``<trk>`` and ``<rte>`` both count.** A recorded track and a planned
  route are the same thing to us — an ordered list of coordinates — and a
  planning tool that exports one rather than the other should not be a
  failed upload. ``<trk>`` wins when a file carries both.
* **Multiple ``<trkseg>`` are concatenated in document order.** A segment
  break marks a GPS signal gap, not a separate journey; joining them
  yields the one polyline the user recorded.
* **``<wpt>`` is ignored.** A waypoint is a standalone marker, not part of
  any path — a file holding only waypoints has no route in it and is
  rejected.
* **The first track wins** when a file holds several. The caller is told
  how many were present (``track_count``) so the response can say which
  one it took rather than silently dropping the rest.
* **Missing ``<ele>`` leaves ``ascent_m`` and ``descent_m`` as ``None``.**
  Reporting zero climb for a file that simply never recorded elevation
  would state a fact we do not have. The two figures are always known or
  unknown together — they are read off the same series.

Parsing goes through ``defusedxml.ElementTree`` rather than the stdlib
parser — the same choice, for the same reason, as the Météo-France DPBRA
path in ``apps/bulletins/services/meteofrance_translator.py``: an XXE or
entity-bomb payload is rejected by the parser itself rather than by
validation we would have to remember to write.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from defusedxml.ElementTree import ParseError, fromstring

from apps.core.coordinates import InvalidCoordinatesError, validate_coordinates

if TYPE_CHECKING:
    # The ``Element`` type lives in the stdlib; defusedxml.fromstring returns
    # plain xml.etree.ElementTree.Element instances. This import is type-only —
    # no parser code reaches xml.etree at runtime — so the
    # ``use-defused-xml`` rule does not apply.
    # nosemgrep: python.lang.security.use-defused-xml.use-defused-xml
    from xml.etree.ElementTree import Element  # noqa: S405

logger = logging.getLogger(__name__)

# Mean Earth radius in metres (IUGG). The track lengths this serves are
# tens of kilometres over mountain terrain, where the difference between a
# spherical and an ellipsoidal model is far below the GPS noise already in
# the source data.
_EARTH_RADIUS_M = 6_371_008.8

# Upper bound on the number of coordinates stored in ``Route.points``. A
# 30,000-point ride is perfectly ordinary for a watch recording at 1 Hz, and
# storing it verbatim would put roughly a megabyte of JSON in one column and
# hand the map a line with far more vertices than a screen has pixels. The
# bound is a hard guarantee, not a target: ``_simplify`` falls back to stride
# sampling if Douglas-Peucker has not got under it.
MAX_POINTS = 2_000

# Starting Douglas-Peucker tolerance, in degrees, and the number of times
# ``_simplify`` may double it. 1e-5 degrees is on the order of a metre, and
# 24 doublings reaches ~170 degrees — far past any real track, so the loop
# always terminates on the point bound rather than on the iteration count.
_INITIAL_TOLERANCE_DEG = 1e-5
_MAX_SIMPLIFY_ITERATIONS = 24

# A polyline needs two ends. A one-point "track" has no length, no bounds
# worth fitting to and nothing to draw.
_MIN_POINTS = 2


class GPXParseError(Exception):
    """Raised when uploaded bytes are not a GPX file we can import.

    Covers malformed XML, a non-GPX document, a file with no ``<trk>`` or
    ``<rte>`` in it (waypoints only), a track too short to be a polyline,
    and out-of-range coordinates.
    """


@dataclass(frozen=True)
class ParsedRoute:
    """The result of ingesting one GPX file.

    Attributes:
        name: The name carried in the GPX, or "" when it names nothing.
        points: The simplified track, ``[[lon, lat, ele], …]`` in GeoJSON
            axis order. ``ele`` is ``None`` where the source had no
            ``<ele>``.
        distance_m: Great-circle length of the **full-resolution** track.
        ascent_m: Total positive elevation gain over the full-resolution
            track, or ``None`` when the file carried no elevation at all.
        descent_m: Total elevation loss over the full-resolution track, as
            a positive magnitude, or ``None`` on the same condition —
            the two are always known or unknown together.
        point_count: ``len(points)`` — what is stored, not what was read.
        bounds: GeoJSON bbox over ``points``:
            ``[min_lon, min_lat, max_lon, max_lat]``.
        track_count: How many ``<trk>``/``<rte>`` elements the file held.
            Greater than 1 means the rest were dropped, and the caller is
            expected to say so.
        source_point_count: How many coordinates were read before
            simplification, so a caller can report what was thinned.

    """

    name: str
    points: list[list[float | None]]
    distance_m: float
    ascent_m: float | None
    descent_m: float | None
    point_count: int
    bounds: list[float]
    track_count: int
    source_point_count: int


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """Return an element tag's local name, dropping any ``{namespace}``.

    GPX 1.0 and 1.1 use different namespace URIs and some exporters emit
    none at all, so every lookup in this module matches on the local name
    rather than on a fully-qualified tag.

    Args:
        tag: The raw ``element.tag`` string.

    Returns:
        The portion after the closing brace, or the whole tag if unnamespaced.

    """
    return tag.rpartition("}")[2]


def _find_all(parent: "Element", name: str) -> list["Element"]:
    """Return every descendant of ``parent`` with the given local name.

    ``Element.iter()`` yields in document order, which is what makes the
    "concatenate ``<trkseg>`` in document order" and "take the first
    ``<trk>``" rules well-defined.

    Args:
        parent: The element to search beneath (inclusive of itself).
        name: The local tag name to match.

    Returns:
        Matching elements in document order.

    """
    return [element for element in parent.iter() if _local(element.tag) == name]


def _child_text(parent: "Element", name: str) -> str:
    """Return the stripped text of ``parent``'s first direct child ``name``.

    Direct children only — a ``<trk>``'s own ``<name>``, never a
    ``<trkpt>``'s.

    Args:
        parent: The element whose children are searched.
        name: The local tag name to match.

    Returns:
        The child's stripped text, or "" when absent or empty.

    """
    for child in parent:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return ""


# ---------------------------------------------------------------------------
# Point extraction
# ---------------------------------------------------------------------------


def _point_from_element(element: "Element") -> tuple[float, float, float | None]:
    """Return ``(lon, lat, ele)`` for one ``<trkpt>`` / ``<rtept>``.

    Args:
        element: The point element, carrying ``lat`` and ``lon``
            attributes and an optional ``<ele>`` child.

    Returns:
        A ``(lon, lat, ele)`` triple in GeoJSON axis order. ``ele`` is
        ``None`` when the point has no usable ``<ele>``.

    Raises:
        GPXParseError: When ``lat``/``lon`` are missing, unparseable, or
            outside WGS-84 bounds.

    """
    raw_lat, raw_lon = element.get("lat"), element.get("lon")
    if raw_lat is None or raw_lon is None:
        raise GPXParseError("A track point is missing its lat/lon attributes.")
    try:
        latitude, longitude = float(raw_lat), float(raw_lon)
        # Rejects NaN/±Inf and out-of-range values before anything is
        # persisted or handed to shapely (SNOW-464).
        validate_coordinates(latitude, longitude)
    except InvalidCoordinatesError as exc:
        raise GPXParseError(f"Track point out of range: {exc}") from exc
    except ValueError as exc:
        raise GPXParseError(f"Unparseable track point: {exc}") from exc

    elevation: float | None = None
    raw_ele = _child_text(element, "ele")
    if raw_ele:
        try:
            candidate = float(raw_ele)
        except ValueError:
            # A malformed <ele> on an otherwise valid point is a gap in the
            # elevation series, not a broken file — the point still has a
            # position worth keeping.
            candidate = math.nan
        if math.isfinite(candidate):
            elevation = candidate

    return longitude, latitude, elevation


def _select_track(root: "Element") -> tuple[list["Element"], int, str]:
    """Choose the element list to read points from, preferring ``<trk>``.

    Args:
        root: The parsed ``<gpx>`` root element.

    Returns:
        A ``(point_elements, track_count, name)`` triple — the chosen
        track's points in document order, how many tracks the file held,
        and the chosen track's own ``<name>`` (or "").

    Raises:
        GPXParseError: When the file holds neither a ``<trk>`` nor a
            ``<rte>``.

    """
    tracks = _find_all(root, "trk")
    if tracks:
        track = tracks[0]
        # Every <trkpt> under the track, across all its <trkseg> children,
        # in document order — which concatenates the segments for free.
        return _find_all(track, "trkpt"), len(tracks), _child_text(track, "name")

    routes = _find_all(root, "rte")
    if routes:
        route = routes[0]
        return _find_all(route, "rtept"), len(routes), _child_text(route, "name")

    raise GPXParseError(
        "No <trk> or <rte> found — a file of waypoints alone has no route in it."
    )


# ---------------------------------------------------------------------------
# Derived figures
# ---------------------------------------------------------------------------


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Return the great-circle distance between two WGS-84 points, in metres.

    Args:
        lon1: Longitude of the first point, in degrees.
        lat1: Latitude of the first point, in degrees.
        lon2: Longitude of the second point, in degrees.
        lat2: Latitude of the second point, in degrees.

    Returns:
        Distance in metres.

    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = phi2 - phi1
    delta_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _total_distance_m(points: list[tuple[float, float, float | None]]) -> float:
    """Return the summed great-circle length of a point list, in metres.

    Args:
        points: ``(lon, lat, ele)`` triples in order.

    Returns:
        Total distance in metres; 0.0 for a list too short to have a leg.

    """
    return sum(
        _haversine_m(first[0], first[1], second[0], second[1])
        for first, second in zip(points, points[1:], strict=False)
    )


def _total_ascent_descent_m(
    points: list[tuple[float, float, float | None]],
) -> tuple[float | None, float | None]:
    """Return total climb and total drop in metres, or ``(None, None)``.

    Only consecutive pairs where **both** points carry an elevation
    contribute, so a gap in the series is skipped rather than counted as a
    cliff. When no point in the track has an elevation at all the answer is
    ``(None, None)``: the file does not say, and zero would claim it does.

    Descent is returned as a **positive magnitude** — "1,100 m descent" is
    how a route is read, and a signed figure would need every display site
    to remember to flip it.

    The two are measured independently and neither is netted against the
    other: a lap that climbs 800 m and drops back down it carries 800 m of
    each, not zero of both. That is the whole point of reporting them as a
    pair — an out-and-back and a one-way traverse of the same length and
    the same climb are very different days on skis, and only the descent
    figure separates them.

    Args:
        points: ``(lon, lat, ele)`` triples in order.

    Returns:
        An ``(ascent_m, descent_m)`` pair in metres, or ``(None, None)``
        when the track carries no elevation data.

    """
    elevations = [point[2] for point in points]
    if all(elevation is None for elevation in elevations):
        return None, None

    ascent = 0.0
    descent = 0.0
    for previous, current in zip(elevations, elevations[1:], strict=False):
        if previous is None or current is None:
            continue
        delta = current - previous
        if delta > 0:
            ascent += delta
        else:
            descent -= delta
    return ascent, descent


def _bounds(points: list[list[float | None]]) -> list[float]:
    """Return the GeoJSON bbox of a stored point list.

    Args:
        points: ``[lon, lat, ele]`` entries — the stored, simplified track.

    Returns:
        ``[min_lon, min_lat, max_lon, max_lat]``.

    """
    longitudes = [float(point[0]) for point in points]  # type: ignore[arg-type]
    latitudes = [float(point[1]) for point in points]  # type: ignore[arg-type]
    return [min(longitudes), min(latitudes), max(longitudes), max(latitudes)]


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------


def _stride_sample(
    points: list[tuple[float, float, float | None]], limit: int
) -> list[tuple[float, float, float | None]]:
    """Thin a point list to at most ``limit`` entries by even sampling.

    The backstop behind ``_simplify``: crude, but it cannot fail to meet
    the bound. Both endpoints are always kept so the line still starts and
    finishes where the track did.

    Args:
        points: ``(lon, lat, ele)`` triples in order.
        limit: Maximum number of points to return. Must be at least 2.

    Returns:
        A subsequence of ``points`` of length ``limit`` or fewer.

    """
    stride = math.ceil(len(points) / (limit - 1))
    sampled = points[::stride]
    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _simplify(
    points: list[tuple[float, float, float | None]],
) -> list[tuple[float, float, float | None]]:
    """Reduce a track to at most ``MAX_POINTS`` coordinates.

    Uses shapely's Douglas-Peucker ``simplify`` over the (lon, lat) plane,
    doubling the tolerance until the result fits the bound. Elevation is
    deliberately kept out of the geometry handed to shapely and mapped back
    afterwards by walking the surviving vertices against the originals in
    order — Douglas-Peucker returns a subsequence of its input, so the walk
    is exact and no Z-coordinate handling has to be assumed of GEOS.

    The shapely import is function-local, matching the convention documented
    in ``apps/regions/services/basemap_tiles.py``: shapely pulls in GEOS, and
    only the handful of call sites that need it should pay for that at
    import time.

    Args:
        points: ``(lon, lat, ele)`` triples in order.

    Returns:
        The thinned track, or ``points`` unchanged when already within the
        bound.

    """
    from shapely.geometry import LineString  # noqa: PLC0415

    if len(points) <= MAX_POINTS:
        return points

    line = LineString([(point[0], point[1]) for point in points])
    tolerance = _INITIAL_TOLERANCE_DEG
    coords: list[tuple[float, float]] = list(line.coords)
    for _ in range(_MAX_SIMPLIFY_ITERATIONS):
        coords = list(line.simplify(tolerance, preserve_topology=False).coords)
        if len(coords) <= MAX_POINTS:
            break
        tolerance *= 2
    else:
        # Douglas-Peucker never got under the bound (a pathological track
        # of near-collinear-but-not-quite points). Guarantee it instead.
        logger.warning(
            "GPX simplification hit the iteration cap at %d points; "
            "falling back to stride sampling.",
            len(coords),
        )
        return _stride_sample(points, MAX_POINTS)

    # Re-attach elevations: walk the surviving coordinates against the
    # originals, both in order. The bound check is a guard on the
    # subsequence assumption above, not an expected path — if GEOS ever
    # returned a vertex we did not give it, degrade to stride sampling
    # rather than fail a user's upload with an IndexError.
    simplified: list[tuple[float, float, float | None]] = []
    index = 0
    for longitude, latitude in coords:
        while index < len(points) and (
            points[index][0] != longitude or points[index][1] != latitude
        ):
            index += 1
        if index >= len(points):
            logger.warning(
                "GPX simplification produced a vertex absent from the source "
                "track; falling back to stride sampling."
            )
            return _stride_sample(points, MAX_POINTS)
        simplified.append(points[index])
        index += 1
    return simplified


def _child_text_in_metadata(root: "Element") -> str:
    """Return the file-level ``<metadata><name>`` text, or "".

    The fallback when the chosen track names itself but the file does —
    common in exports that title the document rather than the track.

    Args:
        root: The parsed ``<gpx>`` root element.

    Returns:
        The metadata name, stripped, or "".

    """
    for element in _find_all(root, "metadata"):
        name = _child_text(element, "name")
        if name:
            return name
    return ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_gpx(raw: bytes) -> ParsedRoute:
    """Parse GPX bytes into a stored-shape track with its derived figures.

    Args:
        raw: The raw bytes of the uploaded ``.gpx`` file.

    Returns:
        A ``ParsedRoute``. ``distance_m``, ``ascent_m`` and ``descent_m``
        are measured on the full-resolution track; ``points``,
        ``point_count`` and ``bounds`` describe the simplified track that
        will be stored.

    Raises:
        GPXParseError: When the bytes are not well-formed XML, are not a
            GPX document, hold no ``<trk>``/``<rte>``, or hold fewer than
            two usable points.

    """
    try:
        root = fromstring(raw)
    except ParseError as exc:
        raise GPXParseError(f"Not a well-formed XML document: {exc}") from exc
    except ValueError as exc:
        # defusedxml raises its own EntityDeclared/DTDForbidden family here
        # for XXE and entity-bomb payloads — the whole reason parsing goes
        # through it rather than the stdlib parser.
        raise GPXParseError(f"Rejected XML document: {exc}") from exc

    if _local(root.tag) != "gpx":
        raise GPXParseError(f"Root element is <{_local(root.tag)}>, not <gpx>.")

    point_elements, track_count, track_name = _select_track(root)
    source_points = [_point_from_element(element) for element in point_elements]
    if len(source_points) < _MIN_POINTS:
        raise GPXParseError(
            f"A route needs at least {_MIN_POINTS} points; found {len(source_points)}."
        )

    # Distance, ascent and descent come from the full-resolution track —
    # simplifying first would shorten the line and flatten both the climb
    # and the drop.
    distance_m = _total_distance_m(source_points)
    ascent_m, descent_m = _total_ascent_descent_m(source_points)

    simplified = _simplify(source_points)
    points: list[list[float | None]] = [
        [longitude, latitude, elevation]
        for longitude, latitude, elevation in simplified
    ]

    name = track_name or _child_text_in_metadata(root)
    return ParsedRoute(
        name=name,
        points=points,
        distance_m=distance_m,
        ascent_m=ascent_m,
        descent_m=descent_m,
        point_count=len(points),
        bounds=_bounds(points),
        track_count=track_count,
        source_point_count=len(source_points),
    )
