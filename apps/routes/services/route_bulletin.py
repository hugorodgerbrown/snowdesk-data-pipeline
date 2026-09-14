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
    """Return a function giving the track's height nearest a coordinate.

    **THE TRACK'S OWN ELEVATION, WHICH IS THE INVERSE OF SNOW-910'S
    RULE.** Steepness must never come from the track; height must. See
    ``bulletin_join``'s module docstring for why both follow from one
    principle.

    Nearest-point rather than interpolated: the sample coordinates sit
    between stored points at a 25 m stride, and a bulletin's elevation
    bands are hundreds of metres wide, so the nearest stored height is
    inside the same band as the exact one in every case that is not
    already a coin-toss at a boundary.

    Args:
        points: The stored track, ``[lon, lat, ele]``.

    Returns:
        A callable taking ``(latitude, longitude)`` and returning a
        height in metres, or None when the GPX carried no elevation at
        all — which is "unknown", never zero.

    """
    # Narrowed to three floats on the way in, so the nearest-point search
    # below is arithmetic rather than a walk through Nones. A point with
    # no height is not a point at height zero.
    known: list[tuple[float, float, float]] = [
        (float(point[1] or 0.0), float(point[0] or 0.0), float(point[2]))
        for point in points
        if len(point) > 2 and point[2] is not None
    ]

    def _height(latitude: float, longitude: float) -> float | None:
        """Return the height of the nearest stored point, or None."""
        if not known:
            return None
        best = min(
            known,
            key=lambda p: (p[0] - latitude) ** 2 + (p[1] - longitude) ** 2,
        )
        return best[2]

    return _height
