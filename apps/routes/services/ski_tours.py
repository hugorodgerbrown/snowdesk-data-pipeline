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

THE TERRAIN IS SAMPLED ONCE PER TOUR, AHEAD OF TIME, BY THE SERVICE.
Walking the terrain grid under a 10 km line takes seconds, too long for a
tap. Since SNOW-1316 the reference-data service walks every tour once
(``render_ski_tours``, daily) and stores the record and its drawing beside
SAC's document; this instance copies both from the v2 ``ski-tours`` feed
(``apps.routes.services.ski_network``) and keeps them, for every reader and
every saved copy, until a sync rewrites the tour. A tour opened before the
service has walked it falls back to the first reader's open: the panel
shows the line and SAC's figures at once and the profile follows
(``tour_slope_samples`` with ``sample``).

The order a line runs in, the summits and the walk itself are
``track.sac`` — pure, shared with the service — so a tour walked here
matches one walked there.

The pure helpers take plain values; ``summits`` and ``tour_overview`` each
read every tour once and are cached until the tours change (a weekly
import at most).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from django.core.cache import cache
from django.db.models import Count, F, Max, QuerySet
from django.db.models.fields.json import KT
from django.utils.translation import get_language

from apps.routes.models import Route, SkiTour
from apps.routes.services.drawn import draft_route
from apps.routes.services.shares import write_route_copy
from apps.routes.services.slope_segments import (
    build_slope_samples,
    enqueue_route_slope_sampling,
)
from apps.routes.services.track_render import store_terrain
from track import sac
from track.render import is_current, leg_lines
from track.sac import (
    WALKED_REVERSED,
    Summit,
    build_summits,
    is_descent,
    tour_key,
    usable,
    walk_tour,
)

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# v2: SNOW-1282 chains a tour's parts, which moves some summits.
# v4: SNOW-1323 adds the newest source_updated_at to the stamp.
# v5: SNOW-1336 drops updated_at — a first open's walk moved it, so every
# first open wrote a fresh ~120 KB copy that lived a week.
_SUMMITS_CACHE_KEY = "routes:ski-summits:v5:{count}:{source}"
_SUMMITS_CACHE_SECONDS = 7 * 24 * 3600

# SNOW-1285: every tour at once, for a map zoomed out past the per-view
# load. Thinned to about 20 m (Douglas-Peucker in degrees) and rounded to
# five places (about a metre): at z9-z11 a pixel is 20-150 m, so nothing
# drawn moves, and the whole set is ~2.3 MB, ~0.6 MB gzipped.
# v3: SNOW-1310 cuts each tour into its legs; v4: SNOW-1323's stamp.
_OVERVIEW_CACHE_KEY = "routes:ski-overview:v4:{count}:{source}:{stamp}"
# SNOW-1336: the overview's key still moves on a walk, so it remembers the
# key it last wrote and deletes that copy when it writes the next one.
_OVERVIEW_CURRENT_KEY = "routes:ski-overview:current"
_OVERVIEW_TOLERANCE_DEG = 0.0002
_OVERVIEW_PLACES = 5


def _tours_stamp(template: str) -> str:
    """Return a cache key from ``template`` that changes when the tours do.

    It carries the tours' count, newest ``source_updated_at`` and newest
    ``updated_at``, one for each writer (SNOW-1323). A sync rewrites a
    tour with ``QuerySet.update()``, which moves ``source_updated_at`` and
    leaves ``auto_now`` alone; a tour walked here on its first open
    (``store_terrain``) moves ``updated_at`` and not ``source_updated_at``.
    So a sync that adds, rewrites or removes a tour, or a local walk that
    gives one its drawing, reads afresh. A template without a ``{stamp}``
    field ignores the walk (SNOW-1336).

    Args:
        template: A cache key with ``{count}``, ``{source}`` and
            optionally ``{stamp}`` fields.

    Returns:
        The key for the tours as they stand.

    """
    stamp = SkiTour.objects.aggregate(
        count=Count("id"),
        source=Max("source_updated_at"),
        newest=Max("updated_at"),
    )
    return template.format(
        count=stamp["count"],
        source=stamp["source"].timestamp() if stamp["source"] else 0,
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
        previous = cache.get(_OVERVIEW_CURRENT_KEY)
        if previous and previous != key:
            cache.delete(previous)
        cache.set(key, cached, _SUMMITS_CACHE_SECONDS)
        cache.set(_OVERVIEW_CURRENT_KEY, key, _SUMMITS_CACHE_SECONDS)
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


def runs_reversed(tour: SkiTour) -> bool:
    """Return whether ``tour_points`` turns SAC's line round (SNOW-1306).

    ``track.sac.runs_reversed`` against every summit (``summits``).
    """
    return sac.runs_reversed(tour, summits())


def tour_points(
    tour: SkiTour, *, reverse: bool | None = None
) -> list[list[float | None]]:
    """Return the tour's line as stored route points, in the tour's order.

    ``track.sac.tour_points`` against every summit (``summits``).
    """
    return sac.tour_points(tour, summits(), reverse=reverse)


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


def sac_time_seconds(label: str) -> int | None:
    """Return SAC's guide time in seconds: "03:30 h" → 12600.

    The route panel writes it as it writes any duration ("3h30m"), so the
    tour's row reads like a saved route's. A label that is not ``HH:MM h``
    has no seconds, and the panel falls back to ``sac_time``'s text.
    """
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*h?\s*", label or "")
    if not match:
        return None
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60


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
    held = usable(tour.slope_samples)
    if held is not None:
        reverse = runs_reversed(tour)
        if bool(held.get(WALKED_REVERSED, False)) != reverse:
            # Walked in the other order: its indices are not the line's.
            held = None
        elif sample and (
            WALKED_REVERSED not in held or not is_current(tour.track_render)
        ):
            # Marked, and its render built (SNOW-1309), without a walk.
            held = {**held, WALKED_REVERSED: reverse}
            _keep(tour, held)
    if held is not None or not sample:
        return held
    record = walk_tour(tour, summits(), build_slope_samples, f"ski tour {tour.sac_id}")
    if record is not None:
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
        "sac_time_s": sac_time_seconds(tour.ascent_time_label),
        "sac_url": localised(tour.urls, tour.urls.get("de", "")),
        "down": is_descent(tour),
    }


def save_ski_tour(user: User, tour: SkiTour) -> Route:
    """Copy one tour onto ``user``'s routes.

    Through ``write_route_copy``, so the per-account cap is checked exactly
    as a claimed share's is. The terrain the tour holds is copied with it;
    a tour never opened is sampled after the commit, as a drawn route is.
    The regions it crosses are resolved there too (SNOW-945): a tour
    carries no list of its own.

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
