"""
track/render.py — a track's drawing, as one pure function of its inputs.

SNOW-1309 stored one ``track_render`` record on every Route, Trip and
SkiTour; SNOW-1316 moved its builder here, out of ``apps.routes``, so the
ingest service renders SAC's tours with the same code an instance renders
a user's upload with. Nothing here touches a database: the writers —
``store_terrain`` and friends — stay in ``apps.routes.services.track_render``.

## The record

``{"v": TRACK_RENDER_VERSION,
   "fp": render_fingerprint(…),        # what it was built from
   "geometry": [[lon, lat, ele], …],   # terrain_points
   "ascent_m": …, "descent_m": …,      # climb_figures
   "slope": {…},                        # compact_slope — omitted when None
   "legs": [{…}, …],                   # wire_legs — omitted when none
   "terrain": {…},                      # summarise_record — omitted when None
   "rows": [{…}, …]}                   # terrain_detail — [] when none``

The keys a Feature reads by their PRESENCE (``slope``, ``legs``,
``terrain``) are omitted rather than nulled, so a reader spreads them
straight onto the wire.

## The fingerprint

``fp`` is a sha256 of every input the builder reads, in canonical JSON,
with the version. Two calls with equal inputs answer equal fingerprints,
so a writer that finds the stored ``fp`` equal to the new one has nothing
to write: a second run is a no-op. The terrain model's version needs no
place of its own: every slope record names its ``grid`` and ``window_m``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, SupportsFloat, TypeGuard

from track.leg_wire import wire_legs
from track.slope_summary import summarise_record
from track.slope_wire import compact_slope
from track.terrain_detail import terrain_detail
from track.terrain_heights import climb_figures, terrain_points

# Raise whenever the record's shape or any of its builders changes what it
# answers; ``rebuild_track_renders --commit`` then rebuilds every row below
# it, and readers build in memory until it has.
TRACK_RENDER_VERSION = 1


def build_track_render(
    points: list[list[float | None]],
    slope_samples: dict[str, Any] | None,
    *,
    ascent_m: float | None,
    descent_m: float | None,
) -> dict[str, Any]:
    """Return the render record for one track.

    Exactly what ``_route_feature`` and the trip page built per request
    before SNOW-1309, in one place. A pure function of its arguments, so
    the same track renders byte-identically on a Route, a Trip and a
    SkiTour, and on every instance.

    Args:
        points: The stored track, ``[[lon, lat, ele], …]``.
        slope_samples: Its slope record, or None if never sampled.
        ascent_m: The row's stored ascent — shown when the record carries
            no model height (``climb_figures``).
        descent_m: The row's stored descent, on the same rule.

    Returns:
        The record described in this module's header.

    """
    # SNOW-1043: the heights are the terrain model's wherever the record
    # has them; the geometry, the legs and the totals all read THESE points.
    geometry = terrain_points(points, slope_samples)
    # SNOW-1053: the slope's ``seams`` index into this geometry.
    slope = compact_slope(slope_samples, coordinates=geometry)
    # The record is passed to the legs only when ``slope`` is sent: with
    # no ``angles`` on the wire the legs must index the stride walk.
    legs = wire_legs(geometry, slope_samples if slope is not None else None)
    terrain = summarise_record(slope_samples)
    climb = climb_figures(points, slope_samples, ascent_m, descent_m)
    return {
        "v": TRACK_RENDER_VERSION,
        "fp": render_fingerprint(
            points, slope_samples, ascent_m=ascent_m, descent_m=descent_m
        ),
        "geometry": geometry,
        "ascent_m": climb.ascent_m,
        "descent_m": climb.descent_m,
        **({"slope": slope} if slope is not None else {}),
        **({"legs": legs} if legs else {}),
        **({"terrain": terrain} if terrain is not None else {}),
        "rows": terrain_detail(slope_samples, points) or [],
    }


def is_current(record: dict[str, Any] | None) -> TypeGuard[dict[str, Any]]:
    """Return whether a stored record was built by the current builder."""
    return bool(record) and record.get("v") == TRACK_RENDER_VERSION  # type: ignore[union-attr]


def leg_lines(
    render: dict[str, Any] | None,
) -> list[tuple[bool, list[list[float]]]]:
    """Return a stored record's legs as ``(climbing, [[lon, lat], …])`` lines.

    The record's own geometry sliced at each leg's ``point_from`` and
    ``point_to``: a read, no computation. Empty when the record is not
    current or holds no leg.

    Args:
        render: A ``track_render`` value, or the two keys of one.

    Returns:
        The lines, in track order.

    """
    if not is_current(render):
        return []
    geometry = render.get("geometry") or []
    return [
        (
            bool(leg["climbing"]),
            [
                [float(lon), float(lat)]
                for lon, lat, *_ in geometry[leg["point_from"] : leg["point_to"] + 1]
            ],
        )
        for leg in render.get("legs") or []
    ]


def render_fingerprint(
    points: list[list[float | None]],
    slope_samples: dict[str, Any] | None,
    *,
    ascent_m: SupportsFloat | None,
    descent_m: SupportsFloat | None,
) -> str:
    """Return the fingerprint of everything ``build_track_render`` reads.

    Canonical JSON — sorted keys, no whitespace — so the same inputs hash
    the same in every process. A ``Decimal`` from a model column is
    written as its float, as the builder reads it.

    Args:
        points: The track.
        slope_samples: Its slope record, or None.
        ascent_m: The stored ascent, or None.
        descent_m: The stored descent, or None.

    Returns:
        A 64-character hex digest.

    """
    payload = {
        "v": TRACK_RENDER_VERSION,
        "points": points,
        "slope_samples": slope_samples,
        "ascent_m": None if ascent_m is None else float(ascent_m),
        "descent_m": None if descent_m is None else float(descent_m),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def same_render(stored: dict[str, Any] | None, built: dict[str, Any]) -> bool:
    """Return whether ``stored`` is ``built`` already, by fingerprint.

    A record from before the fingerprint (no ``fp``) is never the same, so
    the first write after SNOW-1316 stamps one.
    """
    return bool(stored) and stored.get("fp") == built["fp"]  # type: ignore[union-attr]
