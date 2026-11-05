"""
apps/public/weather_points.py — Context for one place's point forecast.

``weather_point_context`` builds what the location weather page and the
map's weather-detail endpoint (``api.weather_detail``) both render: the
forecast panel for a curated ``Location``, the places it serves and the
heading. Moved out of ``apps/public/views.py`` by SNOW-1426
(docs/decisions/public-splits-by-surface.md).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from django.utils import timezone
from django.utils.translation import (
    gettext_lazy as _,
)

from apps.locations.models import Location
from apps.regions.models import MicroRegion, Resort
from apps.weather.models import Weather
from apps.weather.services.day_strips import build_day_strips
from apps.weather.services.hourly_chart import build_hourly_chart
from apps.weather.services.weather_display import build_point_forecast_panel

logger = logging.getLogger(__name__)


def _location_forecast_context(
    location: Location,
    observed_on: datetime.date,
    now: datetime.datetime,
    *,
    strips: bool = False,
) -> dict[str, Any]:
    """Assemble the full forecast for one location on one day.

    Shared by nothing yet, but written as its own function because the shape
    it returns is the answer to "what does a full forecast consist of" and
    the view around it is otherwise all HTTP.

    ``hourly_charts`` is one SNOW-723 meteogram per **selectable** day,
    indexed by that day's position in the picker so the two line up
    (SNOW-787): the picker is the selector and the meteograms are what it
    selects between. Every day's row carries its own hourly series since
    SNOW-1337, so every day of a live week is selectable; a row with no
    hourly series (a backfilled day the archive gave no hours for)
    contributes no meteogram rather than an empty frame.

    The index is the panel position, not an enumeration of the meteograms.
    A day that drops out of the picker for a malformed date would otherwise
    shift every later meteogram off its cell.

    **A forward day opens today's week with that day selected** (SNOW-1105).
    The week always leads with today, so the map's sheet, linking here with
    the day it was showing, finds the same seven cells whichever day it
    names: today's row and the rows after it (SNOW-1337), with
    ``selected_index`` pointing the picker at the requested day. A day past
    the window falls back to the row's own day, the picker's default.

    **The map's sheet draws day strips, not the meteogram** (SNOW-1219).
    With ``strips`` set, each selectable day gets a ``build_day_strips``
    drawing in ``day_strips`` and ``hourly_charts`` stays empty: the sheet
    shows one or the other, and building both would double its weight.
    The days past the hourly window get ``day_figures`` instead — the same
    four readings, one figure each.

    ``show_day_picker`` is the page's shape discriminator, precomputed here
    rather than left to a ``|length`` test in the template (SNOW-789). A day
    with no rows after it — the newest day a station holds — is exactly one
    day, so the whole "week vs one day" distinction is this one comparison,
    in the one place it can be tested.

    Args:
        location: The location to describe.
        observed_on: The calendar day asked for. Today or before, the row
            read; after today, the day selected inside today's row.
        now: The reference instant for each day/night icon decision.
        strips: Build ``day_strips`` in place of ``hourly_charts``.

    Returns:
        A dict of ``weather``, ``panel``, ``show_day_picker``,
        ``selected_index``, ``hourly_charts``, ``day_strips`` and
        ``day_figures``. The first two are
        ``None`` when the location has no row for the day;
        ``hourly_charts`` is then empty and ``show_day_picker`` is
        ``False``.

    """
    row_date = min(observed_on, timezone.localdate())
    weather = Weather.objects.filter(location=location, observed_on=row_date).first()
    panel = build_point_forecast_panel(weather)
    selected_index = next(
        (
            index
            for index, day in enumerate(panel["days"] if panel else [])
            if day["date"] == observed_on
        ),
        0,
    )
    hourly_charts = []
    day_strips = []
    day_figures = []
    for index, day in enumerate(panel["days"] if panel else []):
        if not day["selectable"]:
            if strips:
                # A day with no hourly series: its daily figures. Every
                # fetched day carries hours since SNOW-1337, so this is only
                # a day whose row was written without them.
                day_figures.append({"index": index, "day": day})
            continue
        if strips:
            drawing = build_day_strips(day, elevation=location.elevation_m, now=now)
            if drawing is not None:
                day_strips.append({"index": index, "strips": drawing})
            continue
        chart = build_hourly_chart(
            day,
            elevation=location.elevation_m,
            location_label=location.name or "",
            now=now,
        )
        if chart is not None:
            hourly_charts.append({"index": index, "chart": chart})
    return {
        "weather": weather,
        "panel": panel,
        "show_day_picker": panel is not None and len(panel["days"]) > 1,
        "selected_index": selected_index,
        "hourly_charts": hourly_charts,
        "day_strips": day_strips,
        "day_figures": day_figures,
    }


def _weather_point_places(
    location: Location,
) -> tuple[Resort | None, MicroRegion | None]:
    """Return the resort a weather point stands for and the region it is in.

    Resort first (SNOW-1144): a resort's pin, then a resort linking the
    point. The region is that resort's, else the region the point is the
    centroid of. A point that is neither — rare on the public estate —
    gets no region rather than a full scan of every boundary on a tap.

    Args:
        location: The weather point.

    Returns:
        ``(resort, region)``, either of them None.

    """
    resort = Resort.objects.standing_at(location).select_related("region").first()
    if resort is not None and resort.region_id is not None:
        return resort, resort.region
    return resort, location.micro_regions.first()


def _weather_detail_heading(
    location: Location, resort: Resort | None, region: MicroRegion | None
) -> str:
    """Name an unnamed location by what it stands for.

    Most locations on the weather map carry no ``name``, and that is by
    design rather than an omission: a named ``Location`` is part of the
    curated estate ``import_locations`` owns, so region centroids and
    resort pins are minted anonymous (see ``docs/locations.md``). The sheet
    still needs a heading, and ``Location.to_string()`` would give it a
    pair of raw coordinates.

    Resort before region, because a location reaching both is standing at
    the resort — the region is the area around it, and the more specific
    answer is the one the tapper is looking at.

    Args:
        location: The location being described.
        resort: The resort it stands for, from ``_weather_point_places``.
        region: The region it is in, from ``_weather_point_places``.

    Returns:
        The resort's or region's name, else a generic label. Never the
        coordinates.

    """
    if resort is not None:
        return str(resort.name)
    if region is not None:
        return str(region.name)
    return str(_("Weather station"))


def weather_point_context(
    location: Location, target_date: datetime.date, now: datetime.datetime
) -> dict[str, Any]:
    """Assemble everything one location's weather says about one day.

    THE SHEET IS THE PAGE (Hugo, 24 and 25 Oct 2026). The map's weather
    sheet (``api.weather_detail``) and the forecast page
    (``location_weather``) render the same content — the header, the
    day's summary line, the week, the selected day's strips or figures,
    the provenance and the "Where this is" rows — so they read it from
    this one function. What differs is only the frame: the sheet's peek
    row and date row, the page's ``<h1>``, meta tags and canonical link.

    **A forward day is a forecast** (SNOW-1105): it reads today's row and
    opens the picker on the matching entry. A day past the forecast
    window, or one with no row, has no ``day``; the panel is then dropped
    too, so neither container opens some other day's week under a line
    that says there is no forecast.

    Args:
        location: The weather point.
        target_date: The day asked for.
        now: The reference instant for each day/night icon decision.

    Returns:
        The template context both containers render.

    """
    today = timezone.localdate()
    forecast = _location_forecast_context(location, target_date, now, strips=True)
    weather = forecast["weather"]
    panel = forecast["panel"]
    day = (
        next((d for d in panel["days"] if d["date"] == target_date), None)
        if panel
        else None
    )
    if day is None:
        forecast = {
            **forecast,
            "panel": None,
            "show_day_picker": False,
            "hourly_charts": [],
            "day_strips": [],
            "day_figures": [],
        }
    place_resort, place_region = _weather_point_places(location)
    return {
        "location": location,
        # A curated location has a name; an anonymous one (a region
        # centroid, which is most of the map) does not, and
        # Location.to_string() would print its raw coordinates as a
        # heading. Name what it stands for instead.
        "heading": location.name
        or _weather_detail_heading(location, place_resort, place_region),
        # "Where this is" (Hugo, 25 Oct 2026): the resort the point stands
        # for and the avalanche region it is in, each a row onward.
        "place_resort": place_resort,
        "place_region": place_region,
        "place_region_url": (
            ""
            if place_region is None
            else place_region.get_absolute_url()
            if target_date == today
            else place_region.get_absolute_url(target_date=target_date)
        ),
        "day": day,
        "is_forecast": target_date > today,
        "another_day": target_date != today,
        # When the forecast was issued: the row's last fetch.
        "issued_at": weather.fetched_at if weather else None,
        "target_date": target_date,
        **forecast,
    }
