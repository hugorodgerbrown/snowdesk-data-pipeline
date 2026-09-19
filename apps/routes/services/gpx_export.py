"""
apps/routes/services/gpx_export.py — a stored route, back out as a .gpx file.

SNOW-988. ``apps.routes.services.gpx`` reads a GPX file into a ``Route``;
this is the other direction, and it exists because a stored route is a JSON
column. Reading one means a shell, and getting it into anything that
understands tracks — a mapping tool, a design session, another developer's
machine — means hand-writing a converter, which is exactly what happened
the week this was written.

**A RECONSTITUTION, NEVER THE ORIGINAL.** The uploaded ``.gpx`` is parsed
and discarded at ingest and no copy survives
(``docs/decisions/gpx-uploads-are-parsed-not-stored.md``), so what comes
back out is built from what was kept. Three things differ from the file
that went in, and the document says so in its own ``<desc>`` rather than
leaving a reader to discover them:

* **No per-point ``<time>``.** Only the recording's two ends are stored
  (``started_at`` / ``finished_at``), so they go in ``<metadata>`` and no
  track point carries a stamp. Interpolating a time per point from the span
  would be inventing a pace the recording never had — the same refusal
  ``Route.duration``'s docstring makes about moving time.
* **The geometry may be the SIMPLIFIED track.** Above
  ``gpx.MAX_POINTS`` the stored line has been thinned, and
  ``source_point_count`` (when known) says by how much. The ``<desc>``
  carries both figures so a reader can tell a 894-point recording from a
  894-point remnant of 30,000.
* **The totals are the full-resolution ones.** ``distance_m``,
  ``ascent_m`` and ``descent_m`` were measured before simplification, so
  re-deriving them from the emitted track gives slightly smaller numbers.
  They are not written into the file at all: GPX has no standard place for
  them, and a reader who recomputes should get the track's own figures
  rather than a mix.

**Elevation is omitted per point, not defaulted.** ``ele`` is ``None``
wherever the source had no ``<ele>``, and such a point is emitted without
the element. Writing a zero would put a track at sea level; writing the
neighbouring value would invent terrain. This mirrors what
``elevation_profile_core.js`` does on the same nulls — break the line
rather than bridge it.

Everything here is pure: it takes a ``Route`` and returns a string. Nothing
writes to disk, and the admin action that calls it streams the result.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

if TYPE_CHECKING:
    from datetime import datetime

    from apps.routes.models import Route

logger = logging.getLogger(__name__)

# Coordinate precision, in decimal places. Six is ~0.1 m at these
# latitudes — finer than any consumer GPS, and the precision
# ``Route.points`` already holds, so rounding here loses nothing that
# survived ingest.
_COORD_DP = 6

# Elevation precision. One place matches what the parser stores; a metre is
# already below the vertical accuracy of the devices that record these.
_ELE_DP = 1

_CREATOR = "Snowdesk route export"

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _timestamp(value: "datetime | None") -> str:
    """Render a datetime as a GPX UTC stamp, or "" when it is absent.

    Args:
        value: A tz-aware datetime, or None.

    Returns:
        The formatted stamp, or "" when there is nothing to format.

    """
    if value is None:
        return ""
    return value.strftime(_TIME_FORMAT)


def _describe(route: "Route") -> str:
    """Return the ``<desc>`` text that keeps the file from passing as the upload.

    Three claims, each of which a reader would otherwise have to know from
    the codebase: where it came from, that the original is gone, and how
    many coordinates were thinned to reach the stored track.

    Args:
        route: The route being exported.

    Returns:
        The description text, unescaped.

    """
    parts = [
        f"Reconstituted from Snowdesk stored points (route {route.uuid}).",
        "The uploaded file was parsed and discarded at ingest; this is not it.",
    ]
    if route.source_point_count is None:
        parts.append(
            f"{route.point_count} points stored; the source count was not "
            "recorded for this route."
        )
    elif route.source_point_count > route.point_count:
        parts.append(
            f"{route.point_count} points stored, simplified from "
            f"{route.source_point_count} in the source file."
        )
    else:
        parts.append(
            f"{route.point_count} points, the source file's own count — not simplified."
        )
    parts.append("Per-point timestamps are not stored and are absent here.")
    return " ".join(parts)


def build_gpx(route: "Route") -> str:
    """Render one stored route as a GPX 1.1 document.

    Args:
        route: The route to export. Its ``points`` are emitted verbatim,
            in the order stored.

    Returns:
        The GPX document as a string, ready to be encoded UTF-8.

    """
    label = route.name or route.source_filename or f"route-{route.uuid}"
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<gpx version="1.1" creator="{escape(_CREATOR)}"',
        '     xmlns="http://www.topografix.com/GPX/1/1"',
        '     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"',
        '     xsi:schemaLocation="http://www.topografix.com/GPX/1/1'
        ' http://www.topografix.com/GPX/1/1/gpx.xsd">',
        "  <metadata>",
        f"    <name>{escape(label)}</name>",
        f"    <desc>{escape(_describe(route))}</desc>",
    ]

    started = _timestamp(route.started_at)
    if started:
        lines.append(f"    <time>{started}</time>")

    bounds = route.bounds or []
    if len(bounds) == 4:
        lines.append(
            f'    <bounds minlat="{bounds[1]}" minlon="{bounds[0]}"'
            f' maxlat="{bounds[3]}" maxlon="{bounds[2]}"/>'
        )

    lines += [
        "  </metadata>",
        "  <trk>",
        f"    <name>{escape(label)}</name>",
        "    <trkseg>",
    ]

    for point in route.points:
        lon, lat = point[0], point[1]
        ele = point[2] if len(point) > 2 else None
        head = f'      <trkpt lat="{lat:.{_COORD_DP}f}" lon="{lon:.{_COORD_DP}f}">'
        if ele is None:
            lines.append(f"{head}</trkpt>")
        else:
            lines.append(f"{head}<ele>{ele:.{_ELE_DP}f}</ele></trkpt>")

    lines += [
        "    </trkseg>",
        "  </trk>",
        "</gpx>",
        "",
    ]
    return "\n".join(lines)


def gpx_filename(route: "Route") -> str:
    """Return a filesystem-safe download name for one route's export.

    Built from the route's own label so a staff member downloading several
    can tell them apart, and reduced to characters that survive every
    platform's download handling. Falls back to the uuid when the label
    reduces to nothing — an all-emoji route name is not a filename.

    Args:
        route: The route being exported.

    Returns:
        A name ending in ``.gpx``.

    """
    label = route.name or route.source_filename or ""
    safe = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in label
    ).strip("-")
    while "--" in safe:
        safe = safe.replace("--", "-")
    if not safe:
        safe = f"route-{route.uuid}"
    return f"{safe[:80]}.gpx"
