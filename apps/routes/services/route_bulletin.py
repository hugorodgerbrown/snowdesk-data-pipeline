"""
apps/routes/services/route_bulletin.py — this track, against that day.

SNOW-839. Resolves the regions a stored track crosses, finds the bulletin
each of them published for the day in question, and asks
``apps.routes.services.bulletin_join`` which stretches of the track fall
inside each problem's own stated aspects and elevations.

**DERIVED AT READ TIME AND NEVER STORED.** This is the one part of
SNOW-909 that changes twice a day. A cached answer would outlive the
bulletin it was taken from, and — worse on this product — the service
worker would hold it offline, so a reader could be shown last night's
intersection under today's date with nothing on screen admitting it. The
walk is a handful of queries over a few hundred segments, which is
cheaper than being wrong.

**A ROUTE CROSSES AS MANY BULLETINS AS IT CROSSES REGIONS.** A track over
a ridge is routinely in two micro-regions, and on a border it is two
PROVIDERS with two independent readings of the same day. Nothing here
picks a winner: each region's stretch is reported under its own bulletin,
named, and a reader who sees two different pictures of one day is seeing
something true about the forecast rather than a bug in the join.

**WHICH ISSUE A DAY SHOWS IS NOT DECIDED HERE.**
``apps.bulletins.services.selection`` owns that rule, and owns it for the
bulletin page too — a second implementation would eventually disagree
with the page a reader opens next, and neither would be wrong on its own
terms.

The output carries no score. See ``bulletin_join``'s module docstring for
why that is a position rather than an omission.
"""

from __future__ import annotations

import datetime
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apps.bulletins.services.selection import select_bulletin_for_date
from apps.core.geo import haversine_m
from apps.regions.services.point_match import regions_for_points
from apps.routes.services.bulletin_join import (
    ProblemOverlap,
    SegmentFacts,
    overlaps,
)

if TYPE_CHECKING:
    from apps.bulletins.models import Bulletin
    from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RegionReading:
    """One region's bulletin, and where this track sits inside it.

    ``region`` and ``bulletin`` are carried so the surface can name and
    link them — the whole output is an invitation to go and read the
    bulletin, so it must say which one.
    """

    region: "MicroRegion"
    bulletin: "Bulletin | None"
    length_m: float
    problem_overlaps: list[ProblemOverlap]


def readings_for_track(
    points: list[list[float | None]],
    slope_samples: dict[str, Any] | None,
    target_date: datetime.date,
) -> list[RegionReading]:
    """Return what each region's bulletin says about this track.

    Args:
        points: The stored track, ``[lon, lat, ele]`` triples. Read for
            its ELEVATION only — where the skier is, which is precisely
            the track. Steepness never comes from here (SNOW-910).
        slope_samples: The terrain record, whose per-segment ``aspect_deg``
            is the other half of a problem's geography. None for a track
            nothing has sampled, which produces no readings at all: the
            join needs an aspect, and inventing one would be inventing
            exposure.
        target_date: The day to ask about. A trip has one of its own; a
            route implies today.

    Returns:
        One ``RegionReading`` per region the track crosses, longest
        stretch first — the region a reader is mostly IN is the one they
        should read first. Empty when the track has no terrain record, no
        region could be resolved, or no segment could be placed.

    """
    segments = _segment_facts(points, slope_samples)
    if not segments:
        return []

    sample_points = _sample_coordinates(slope_samples)
    regions = regions_for_points(sample_points)

    by_region: dict[int, list[SegmentFacts]] = {}
    region_by_pk: dict[int, MicroRegion] = {}
    for region, segment in zip(regions, segments, strict=True):
        if region is None:
            # Outside every region we hold a boundary for. Reported by
            # its absence rather than attributed to a neighbour — a
            # bulletin is only about the ground its own region covers.
            continue
        region_by_pk[region.pk] = region
        by_region.setdefault(region.pk, []).append(segment)

    readings = [
        _reading_for(region_by_pk[pk], region_segments, target_date)
        for pk, region_segments in by_region.items()
    ]
    return sorted(readings, key=lambda reading: reading.length_m, reverse=True)


def _reading_for(
    region: "MicroRegion",
    segments: list[SegmentFacts],
    target_date: datetime.date,
) -> RegionReading:
    """Return one region's reading of the stretch inside it.

    Args:
        region: The region.
        segments: The track's segments that fall in it.
        target_date: The day to ask about.

    Returns:
        The reading. Its ``bulletin`` is None when the region published
        nothing for that day, which is a fact worth showing — a reader
        whose route crosses an unforecast region should be told so rather
        than shown a shorter route than they have.

    """
    bulletin = select_bulletin_for_date(region, target_date)
    problems = _problems_of(bulletin)
    return RegionReading(
        region=region,
        bulletin=bulletin,
        length_m=sum(segment.length_m for segment in segments),
        problem_overlaps=overlaps(segments, problems),
    )


def _problems_of(bulletin: "Bulletin | None") -> list[dict[str, Any]]:
    """Return every problem in a bulletin's render model, across its traits.

    The render model groups problems under ``traits`` — one entry per
    time period or elevation band — and the same problem can appear in
    more than one. They are flattened here because a route asks a
    geographic question, not a temporal one: "does my line enter this
    problem's ground" has the same answer whichever band listed it.

    Args:
        bulletin: The bulletin, or None.

    Returns:
        The problems, possibly empty.

    """
    if bulletin is None:
        return []
    traits = (bulletin.render_model or {}).get("traits") or []
    problems: list[dict[str, Any]] = []
    for trait in traits:
        problems.extend(trait.get("problems") or [])
    return problems


def _segment_facts(
    points: list[list[float | None]], slope_samples: dict[str, Any] | None
) -> list[SegmentFacts]:
    """Reduce a track and its terrain record to what the join can read.

    Args:
        points: The stored track, for its elevation series.
        slope_samples: The terrain record, for its aspects and geometry.

    Returns:
        One entry per sampled segment, or an empty list when the record
        is absent or does not pair up with its own coordinates.

    """
    if not slope_samples:
        return []
    sample_points = slope_samples.get("points") or []
    segments = slope_samples.get("segments") or []
    if len(sample_points) != len(segments) + 1:
        logger.warning("route slope record is malformed; no bulletin join built")
        return []

    heights = _height_lookup(points)
    facts: list[SegmentFacts] = []
    for index, segment in enumerate(segments):
        longitude, latitude = sample_points[index][0], sample_points[index][1]
        facts.append(
            SegmentFacts(
                aspect_deg=segment.get("aspect_deg"),
                elevation_m=heights(latitude, longitude),
                length_m=haversine_m(
                    latitude,
                    longitude,
                    sample_points[index + 1][1],
                    sample_points[index + 1][0],
                ),
            )
        )
    return facts


def _sample_coordinates(
    slope_samples: dict[str, Any] | None,
) -> list[tuple[float, float]]:
    """Return each segment's MIDDLE as ``(latitude, longitude)``.

    The middle, not either end, and for the reason the sampler places its
    angle there: an end-sampled segment takes its answer from a point it
    only touches, and has to choose between its two ends to do it.

    Taking the START would be worse than arbitrary here — it would mean
    the track's final boundary never informed the walk at all, so a route
    that ENDS in another region would never report that region's
    bulletin. At a 25 m stride the midpoint and the start are almost
    always in the same place; the case where they are not is exactly the
    one that matters.

    Args:
        slope_samples: The terrain record.

    Returns:
        One coordinate per segment.

    """
    if not slope_samples:
        return []
    points = slope_samples.get("points") or []
    return [
        (
            (points[index][1] + points[index + 1][1]) / 2,
            (points[index][0] + points[index + 1][0]) / 2,
        )
        for index in range(len(points) - 1)
    ]


def _height_lookup(
    points: list[list[float | None]],
) -> Callable[[float, float], float | None]:
    """Return a function giving the track's height at a coordinate.

    **THE TRACK'S OWN ELEVATION, WHICH IS THE INVERSE OF SNOW-910'S
    RULE.** Steepness must never come from the track; height must. See
    ``bulletin_join``'s module docstring for why both follow from one
    principle.

    **INTERPOLATED ALONG THE LEG, NOT SNAPPED TO THE NEARER END.** A
    stored track is SIMPLIFIED, so one leg can be kilometres long and
    climb hundreds of metres. Taking the nearer vertex's height would
    give every sample in the leg's first half the height of its start and
    every sample in the second half the height of its end — a step
    halfway up, where the route actually climbs steadily. Against a
    bulletin band that is a real miscount: a band boundary crossed
    somewhere in the middle of a leg would be placed at the leg's
    midpoint instead, and the length reported inside the band would be
    wrong by whatever the difference was. The terrain sampler
    interpolates coordinates along the track for the same reason.

    Args:
        points: The stored track, ``[lon, lat, ele]``.

    Returns:
        A callable taking ``(latitude, longitude)`` and returning a
        height in metres, or None when the GPX carried no elevation at
        all — which is "unknown", never zero.

    """
    # (latitude, longitude, elevation) for every stored point, with the
    # elevation left as None where the file had none: a leg between two
    # heights can be interpolated, and one with a gap at either end
    # cannot, so the gaps have to survive this far.
    track: list[tuple[float, float, float | None]] = [
        (
            float(point[1] or 0.0),
            float(point[0] or 0.0),
            None if len(point) < 3 or point[2] is None else float(point[2]),
        )
        for point in points
    ]
    if not any(height is not None for _, _, height in track):
        return lambda latitude, longitude: None

    def _height(latitude: float, longitude: float) -> float | None:
        """Return the interpolated height at a point on the track."""
        leg = _nearest_leg(track, latitude, longitude)
        if leg is None:
            return None
        first, second = leg
        if first[2] is None or second[2] is None:
            # One end of this leg has no height. The other end's is a
            # fact about a different place, so the honest answer is that
            # we do not know this one's.
            return first[2] if second[2] is None else second[2]
        fraction = _leg_fraction(first, second, latitude, longitude)
        return first[2] + (second[2] - first[2]) * fraction

    return _height


def _nearest_leg(
    track: list[tuple[float, float, float | None]],
    latitude: float,
    longitude: float,
) -> tuple[tuple[float, float, float | None], tuple[float, float, float | None]] | None:
    """Return the two stored points the sample sits between.

    Nearest by the sum of the distances to each end, which picks the leg
    the point lies ON rather than the leg with the nearest single vertex
    — the distinction that matters at a switchback, where the nearest
    vertex can belong to a leg running the other way.

    Args:
        track: The stored points as ``(lat, lon, ele)``.
        latitude: The sample's latitude.
        longitude: The sample's longitude.

    Returns:
        The pair, or None for a track with fewer than two points.

    """
    if len(track) < 2:
        return None

    def _detour(index: int) -> float:
        """Return the sample's summed distance to this leg's two ends.

        The SUM, not the nearer end: it is smallest for the leg the
        sample lies on, which at a switchback is not the leg owning the
        nearest single vertex.
        """
        first, second = track[index], track[index + 1]
        to_first = math.sqrt(_sq(first, latitude, longitude))
        to_second = math.sqrt(_sq(second, latitude, longitude))
        return to_first + to_second

    best = min(range(len(track) - 1), key=_detour)
    return track[best], track[best + 1]


def _sq(
    point: tuple[float, float, float | None], latitude: float, longitude: float
) -> float:
    """Return the squared degree distance from a stored point to a sample."""
    return (point[0] - latitude) ** 2 + (point[1] - longitude) ** 2


def _leg_fraction(
    first: tuple[float, float, float | None],
    second: tuple[float, float, float | None],
    latitude: float,
    longitude: float,
) -> float:
    """Return how far along a leg the sample lies, from 0 to 1.

    The scalar projection of the sample onto the leg, clamped to the
    leg's own ends — a sample slightly off the line projects to the
    nearest point ON it, which is what "how far along" means.

    Degrees rather than metres throughout: the ratio is scale-free, and
    over one leg of a ski track the latitude distortion is far below the
    precision a bulletin band needs.

    Args:
        first: The leg's start, as ``(lat, lon, ele)``.
        second: Its end.
        latitude: The sample's latitude.
        longitude: The sample's longitude.

    Returns:
        A fraction in [0, 1]. Zero for a leg of no length, which is a
        duplicated point rather than a position.

    """
    d_lat = second[0] - first[0]
    d_lon = second[1] - first[1]
    length_sq = d_lat * d_lat + d_lon * d_lon
    if length_sq == 0:
        return 0.0
    along: float = (latitude - first[0]) * d_lat + (longitude - first[1]) * d_lon
    return max(0.0, min(1.0, along / length_sq))


@dataclass(frozen=True)
class OverlapDisplay:
    """One problem overlap, ready for a template.

    The numbers are formatted here rather than in the template because
    the rounding is part of the claim: a stretch reported to the metre
    would suggest the join knows where a problem's edge is, and it knows
    only which 25 m segments fell inside the aspects and heights the
    forecaster stated.
    """

    problem_label: str
    aspects: str
    # The length as a phrase rather than a number, because the UNIT is
    # part of the rounding decision: one or two 25 m segments is a real
    # overlap and "0.0 km" is what a kilometre figure makes of it — a
    # named avalanche problem beside a length of zero, which reads as a
    # bug and understates the day. Under a kilometre it is metres, the
    # same rule ``route_slope_core.js``'s own figures follow.
    length_label: str
    lowest_m: int | None
    highest_m: int | None
    elevation_undecided: bool


def display_overlaps(overlaps_: list[ProblemOverlap]) -> list[OverlapDisplay]:
    """Return overlaps in the order and shape a template renders.

    **LONGEST FIRST.** A reader scanning one line of a panel should meet
    the problem their day spends most of its length inside; ordering by
    danger rating instead would put a 40 m brush with a high-rated
    problem above 3 km inside a moderate one, which is not what the
    length figure is for.

    Args:
        overlaps_: The overlaps for one region, as the join returned them.

    Returns:
        One entry per overlap, longest stretch first.

    """
    from apps.bulletins.schema import AvalancheProblemType  # noqa: PLC0415

    labels = dict(AvalancheProblemType.choices)
    ordered = sorted(overlaps_, key=lambda o: o.length_m, reverse=True)
    return [
        OverlapDisplay(
            # The enum's own label where the type is one we know, and the
            # raw value where a provider sends something new — which is
            # visible rather than swallowed, so a reader can report it.
            problem_label=str(labels.get(overlap.problem_type, overlap.problem_type)),
            aspects=_aspect_phrase(overlap.aspects),
            length_label=_length_phrase(overlap.length_m),
            lowest_m=None if overlap.lowest_m is None else round(overlap.lowest_m),
            highest_m=None if overlap.highest_m is None else round(overlap.highest_m),
            elevation_undecided=overlap.elevation_undecided,
        )
        for overlap in ordered
    ]


def _length_phrase(metres: float) -> str:
    """Return a stretch's length, in the unit that does not round it away.

    Args:
        metres: The stretch, in metres.

    Returns:
        ``"1.4 km"`` at a kilometre and over, ``"80 m"`` below it. A real
        overlap is never rendered as zero.

    """
    if metres < 1000:
        return f"{round(metres)} m"
    return f"{metres / 1000:.1f} km"


def _aspect_phrase(aspects: set[str]) -> str:
    """Return the crossed aspects in compass order.

    Compass order, not alphabetical: "N, NE, E" is how a bulletin reads
    and how the reader will check it against the page they open next.
    Alphabetical would give "E, N, NE", which is the same set and a
    different sentence.

    Args:
        aspects: The octants the route crossed inside this problem.

    Returns:
        A comma-separated phrase, empty when there are none.

    """
    from apps.routes.services.bulletin_join import OCTANTS  # noqa: PLC0415

    return ", ".join(octant for octant in OCTANTS if octant in aspects)
