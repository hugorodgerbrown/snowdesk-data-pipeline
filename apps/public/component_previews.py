"""
apps/public/component_previews.py — synthetic contexts for the /help/ illustrations.

The help page explains sixteen surfaces and, until SNOW-744, showed none of
them. Each illustrated topic now renders the REAL partial for the thing it
describes, fed by a hand-built context from this module — a live mock, not a
screenshot. A screenshot of a component goes stale silently: there is no
linter for a PNG and no test that fails when the component moves on. Four
claims on that page had rotted exactly that way by the time it was reviewed.
A mock rendered from the partial inherits the design tokens, the theme and
the translations, and breaks loudly when the component changes.

WHY THIS IS NOT ``_component_fixtures.py``. That module serves the staff-only
component library at ``/_components/`` and says in its own docstring that
nothing in it is a public import surface; ``/help/`` is public, so importing
it here would break that contract for the sake of reuse. The reuse would also
have been thin: the library's season-calendar fixture is a hand-curated
thirteen-week grid covering every cell state including the ALBINA band splits,
because its job is exhaustive coverage for a designer. The help page's job is
to teach, so it wants a short grid a reader can take in at a glance. Different
data for a different purpose — what the two genuinely share is the
``SeasonGrid``/``SeasonCell`` dataclasses, and those already live in the public
``apps.public.season_calendar``. See
docs/decisions/help-illustrations-are-live-mocks.md.

Everything here is synthetic and constructed in memory. No database access —
``/help/`` issues no queries today and must keep issuing none.
"""

from __future__ import annotations

import dataclasses
import datetime
from collections.abc import Sequence
from typing import Any

from django.utils.translation import gettext_lazy as _

from apps.public.season_calendar import SeasonCell, SeasonGrid

# The illustrated grid's own calendar. Fixed dates rather than something
# derived from today: the point of the picture is the SHAPE of a season —
# a quiet opening, a rise, a spell at considerable — and a grid anchored to
# the real today would be empty in October and unreadable in May.
_GRID_START = datetime.date(2025, 12, 1)  # a Monday, so no leading padding
_GRID_TODAY = datetime.date(2026, 1, 11)

# Six weeks of ratings, Monday-first, one entry per day. A plain string is a
# uniform day; a (min, max) pair is a day whose rating varied across the
# region, which the template paints as a diagonal split. ``None`` is a day
# the provider published nothing for.
#
# The sequence is chosen to make the three things the copy talks about
# visible in one picture: the flat opening spell, the climb into
# considerable over the Christmas storm, and one split day.
_SCHEDULE: tuple[Any, ...] = (
    # Dec 1–7 — a settled opening
    "low",
    "low",
    "low",
    "low",
    "low",
    "moderate",
    "moderate",
    # Dec 8–14
    "moderate",
    "moderate",
    "low",
    "low",
    "low",
    "moderate",
    "moderate",
    # Dec 15–21 — building
    "moderate",
    "moderate",
    "considerable",
    "considerable",
    "moderate",
    "moderate",
    "moderate",
    # Dec 22–28 — the storm, and the one day that varied by region
    "considerable",
    "considerable",
    ("considerable", "high"),
    "high",
    "considerable",
    "considerable",
    "moderate",
    # Dec 29 – Jan 4 — settling again
    "moderate",
    "moderate",
    "moderate",
    "low",
    "low",
    "low",
    "moderate",
    # Jan 5–11 — a gap in what the provider published, then today
    "moderate",
    "moderate",
    None,
    None,
    "low",
    "low",
    "moderate",
)

_DAYS_PER_WEEK = 7


def _cell(index: int, entry: Any) -> SeasonCell:
    """
    Build one synthetic heatmap tile.

    Args:
        index: Offset in days from ``_GRID_START``.
        entry: A rating key, a ``(min, max)`` pair for a day that varied, or
            ``None`` for a day with no bulletin.

    Returns:
        The tile, with ``month_parity`` set so the template's month-boundary
        tint lands on the right day.

    """
    date = _GRID_START + datetime.timedelta(days=index)
    # December is the first dated month, so it takes parity 0 and January 1.
    parity = 0 if date.month == _GRID_START.month else 1

    if entry is None:
        return SeasonCell(
            date=date,
            min_rating_key="no_rating",
            max_rating_key="no_rating",
            subdivision="",
            has_bulletin=False,
            month_parity=parity,
        )

    minimum, maximum = entry if isinstance(entry, tuple) else (entry, entry)
    return SeasonCell(
        date=date,
        min_rating_key=minimum,
        max_rating_key=maximum,
        subdivision="",
        has_bulletin=True,
        is_today=date == _GRID_TODAY,
        month_parity=parity,
        source="SLF",
    )


def synthetic_season_grid() -> SeasonGrid:
    """
    Build the six-week heatmap shown in the "season calendar" help topic.

    Returns:
        A ``SeasonGrid`` of six week-columns with a month label on the first
        column of each calendar month, matching what ``build_season_grid``
        produces from real ``RegionDayRating`` rows.

    """
    cells = [_cell(index, entry) for index, entry in enumerate(_SCHEDULE)]
    columns: list[tuple[SeasonCell | None, ...]] = [
        tuple(cells[start : start + _DAYS_PER_WEEK])
        for start in range(0, len(cells), _DAYS_PER_WEEK)
    ]

    # One label per column, non-empty only where a calendar month opens.
    labels: list[str] = []
    seen: set[int] = set()
    for column in columns:
        label = ""
        for cell in column:
            if cell is not None and cell.date.month not in seen:
                seen.add(cell.date.month)
                label = cell.date.strftime("%b")
                break
        labels.append(label)

    return SeasonGrid(columns=columns, month_labels=labels, season_label="25/26")


# Each entry feeds includes/_ugc_panel.html — the one shell behind the
# favourites, field-observation, routes and downloads panels. Rendering the
# same component four times with four contents is the point: the help page
# says these panels share a shape, and the illustrations show it.
#
# ``toggle_id`` is namespaced per illustration. The ids the real panels use
# (``#map-favourites-overlay-toggle`` and friends) are how map.js finds the
# switch it drives; a decoration must never answer to that name, and two
# illustrations on one page must not collide with each other either. The
# component library namespaces its own fixtures the same way.
_PANELS: dict[str, dict[str, Any]] = {
    "favourites": {
        "title": _("Favourites"),
        "icon_template": "includes/_icon_favourite.html",
        "context_line": _("Favourites are private and not shared."),
        "section_label": _("Places"),
        "cta_label": _("Add a favourite"),
        "toggle_id": "help-illustration-toggle-favourites",
        "rows": (
            {"label": _("Cabane des Dix"), "meta": _("Val des Dix")},
            {"label": _("Col des Gentianes"), "meta": _("Martigny · Verbier")},
        ),
    },
    "observations": {
        "title": _("Field observations"),
        "icon_template": "includes/_icon_observation.html",
        "context_line": _("Reports are shared with the community."),
        "section_label": _("Reports"),
        "cta_label": _("Report an observation"),
        "toggle_id": "help-illustration-toggle-observations",
        "rows": (
            {"label": _("Whumpfing"), "meta": _("Arolla · 2 hours ago")},
            {"label": _("Shooting cracks"), "meta": _("Verbier · yesterday")},
        ),
    },
    "routes": {
        "title": _("Routes"),
        "icon_template": "includes/_icon_route.html",
        # SNOW-765: tracks the real panel's own wording, which SNOW-764
        # made conditional when it put a Share control on every row.
        "context_line": _("Routes are private unless you share one."),
        "section_label": _("Tracks"),
        "cta_label": _("Add a route"),
        "toggle_id": "help-illustration-toggle-routes",
        "rows": (
            # Mirrors the real row's "12.4 km · 850 m ascent · 900 m descent"
            # shape from routes/partials/_route.html.
            # ``uuid`` is for the Routes article's rows, which render the
            # real action cluster (_routes_rows.html) and its Remove form
            # is addressed by one. A shape no stored route can carry; the
            # wrapper it renders in is inert, so nothing can post to it.
            {
                "label": _("Rosablanche"),
                "meta": _("14.2 km · 1,320 m ascent"),
                "uuid": "00000000-0000-4000-8000-000000000001",
            },
            {
                "label": _("Pigne d'Arolla"),
                "meta": _("11.8 km · 1,540 m ascent"),
                "uuid": "00000000-0000-4000-8000-000000000002",
            },
        ),
    },
    "downloads": {
        # SNOW-749: both strings track the real sheet, which stopped saying
        # "Downloads on this device" and "Downloads and budget stay on this
        # device." when the AREAS started following the account. The
        # illustration renders the real partial, so a stale string here is
        # not a stale mock — it is /help/ showing a reader a panel that
        # does not exist. Identical msgids to _map_downloads_sheet.html's
        # own, so the catalogue carries one entry for each.
        "title": _("Your downloads"),
        "icon_template": "includes/_icon_downloads.html",
        "context_line": _(
            "Your areas follow your account. The map data and the budget "
            "stay on this device."
        ),
        "section_label": _("Regions"),
        "cta_label": _("Download a custom area"),
        "toggle_id": "help-illustration-toggle-downloads",
        "rows": (
            {
                "label": _("Martigny · Verbier"),
                "meta": _("Snowdesk Terrain"),
                "value": "18.2 MB",
            },
            {
                "label": _("Val d'Hérens"),
                "meta": _("Snowdesk Terrain"),
                "value": "9.4 MB",
            },
        ),
    },
}


# ---------------------------------------------------------------------------
# Weather panel (SNOW-761)
#
# A hand-built ``WeatherDisplay``, not the output of
# ``build_weather_display`` against a real row: /help/ renders without
# touching the database, and the illustration is about the panel's layout
# rather than about the derivation, which is covered in
# ``tests/weather/services/test_weather_display.py``.
#
# ``weather`` is a plain dict here where a call site passes a model
# instance — the partial reads only ``weather_code`` off it, for a data
# attribute.
#
# The values describe a snowy day at altitude, because that is the case
# the topic's copy is about: the same forecast reads differently at the
# village and at the summit.
# ---------------------------------------------------------------------------

_WEATHER_PANEL: dict[str, Any] = {
    "location_label": "Mont Fort · 3328 m",
    "weather_display": {
        "weather": {"weather_code": 73},
        "bucket": "snow",
        "is_day": True,
        "time_of_day": "day",
        "sunrise_local": "07:48",
        "sunset_local": "17:12",
        "icon_bucket": "moderate_snow",
        "condition_label": "Snow",
        "icon_filename": "moderate_snow-day.svg",
        "temp_max": -4.0,
        "temp_min": -11.0,
        "snowfall_sum": 18.0,
        "freezing_level_height": 900.0,
    },
}


# ---------------------------------------------------------------------------
# Route popup (the Routes article)
#
# The popup a tap on a route line opens is built in JavaScript — map.js's
# ``activateRoute`` assembles it and static/js/elevation_profile_core.js
# draws the chart — so there is no server partial to render, and the
# illustration template mirrors that markup instead. The one part with
# any arithmetic in it, projecting the elevation series into the chart's
# viewBox, is ported below so the curve on the help page is shaped the way
# the map shapes it; tests/public/test_component_previews.py pins the port
# to the same properties tests/js/test_elevation_profile_core.js pins the
# original to.
#
# The series is a synthetic day on Rosablanche — the first row of the
# routes panel above — as along-track distance and height, so the port
# needs no haversine: the map derives distance from coordinates, and here
# the distances are the data.
# ---------------------------------------------------------------------------

#: The chart's user-space box, and its vertical inset. Both copied from
#: elevation_profile_core.js, where the reasoning for each lives.
PROFILE_VIEWBOX = (288, 72)
_PROFILE_PAD_Y = 6


def profile_paths(
    points: Sequence[tuple[float, float]],
    box: tuple[int, int] = PROFILE_VIEWBOX,
) -> list[dict[str, str]]:
    """
    Project an elevation series into SVG path ``d`` strings.

    A port of ``buildPaths`` in static/js/elevation_profile_core.js for one
    unbroken run: x is along-track distance over the track's length, y is
    height over the track's own min-to-max range, inset by the same pad.
    Two paths, as there: the stroked line, and the closed area under it
    that makes the shape read as terrain.

    Args:
        points: ``(distance_m, elevation_m)`` pairs along the track, at
            least two, in order.
        box: The ``(width, height)`` of the viewBox to draw into.

    Returns:
        A one-entry list of ``{"line": d, "area": d}`` — a list rather
        than a pair so the template loops over it the way the map's builder
        does over its runs.

    """
    if len(points) < 2:
        return []
    width, height = box
    total = points[-1][0]
    low = min(e for _, e in points)
    high = max(e for _, e in points)
    span = high - low
    floor = height - _PROFILE_PAD_Y
    usable = height - _PROFILE_PAD_Y * 2

    def scale_x(d: float) -> float:
        return (d / total) * width if total else 0.0

    def scale_y(e: float) -> float:
        return floor - ((e - low) / span) * usable if span else height / 2

    vertices = [f"{scale_x(d):.2f} {scale_y(e):.2f}" for d, e in points]
    line = "M" + " L".join(vertices)
    start_x = f"{scale_x(points[0][0]):.2f}"
    end_x = f"{scale_x(points[-1][0]):.2f}"
    area = f"{line} L{end_x} {floor:.2f} L{start_x} {floor:.2f} Z"
    return [{"line": line, "area": area}]


#: Along-track distance and height, in metres, at eighteen points of a
#: Rosablanche day: a steady climb to the summit and a longer, gentler way
#: down. Range 2,180–3,336 m; the popup's caption states it because the
#: chart's y-axis is scaled to it.
_ROSABLANCHE_PROFILE: tuple[tuple[float, float], ...] = (
    (0, 2180),
    (700, 2290),
    (1400, 2420),
    (2100, 2560),
    (2800, 2700),
    (3500, 2850),
    (4200, 2960),
    (4900, 3080),
    (5600, 3200),
    (6300, 3336),
    (7300, 3200),
    (8400, 3050),
    (9500, 2900),
    (10600, 2750),
    (11700, 2600),
    (12700, 2450),
    (13500, 2300),
    (14200, 2180),
)

_ROUTE_POPUP: dict[str, Any] = {
    # The same name and figures as the panel's first row, so the two
    # illustrations on the article are one route seen twice.
    "name": _("Rosablanche"),
    "meta": _("14.2 km · 1,320 m ascent"),
    # Range · duration, the caption line map.js draws under the chart.
    # The duration is here because the source file carried times; the
    # article says what that depends on.
    "caption": _("2,180–3,336 m · 5h40m"),
    "paths": profile_paths(_ROSABLANCHE_PROFILE),
}


def help_illustrations() -> dict[str, Any]:
    """
    Build the illustration context for the /help/ page.

    Returns:
        A mapping consumed by ``public/help.html``: ``season_calendar`` for
        the heatmap topic, ``panels`` keyed by topic for the four that
        share the UGC panel shell, ``weather_panel`` for the weather
        topic, and ``route_popup`` for the Routes article.

        The season scrubber is deliberately absent — its styles live in
        ``static/css/map.css``, which ``/help/`` does not load. See that
        topic's comment in ``public/help.html``.

    """
    context: dict[str, Any] = {
        "season_calendar": synthetic_season_grid(),
        "panels": _PANELS,
        "weather_panel": _WEATHER_PANEL,
        "route_popup": _ROUTE_POPUP,
    }
    context.update(_bulletin_illustrations())
    return context


# ---------------------------------------------------------------------------
# Bulletin-page illustrations (SNOW-744 follow-up)
#
# The two components a reader meets on a bulletin page, in the order the
# page stacks them: the day-risk panel and one problem card. Each is the
# REAL partial; only the context below is invented.
#
# Both are styled entirely from output.css — checked against the bulletin
# page, which loads that stylesheet and nothing else. That is what
# separates them from the season scrubber, whose rules live in map.css and
# which /help/ therefore cannot illustrate.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _ElevationBounds:
    """Minimal stand-in for ``apps.public.views.ElevationBounds``.

    The real dataclass cannot be imported here: ``views`` imports this
    module, so reaching back into it would close a cycle. Only the four
    fields the templates and the ``elevation_icon`` filter read are
    reproduced, and truthiness follows the real class — non-empty
    ``display`` means "there is a bound worth printing".
    """

    lower: str
    upper: str
    display: str
    bound_type: str

    def __bool__(self) -> bool:
        """Return True when there is a bound to render."""
        return bool(self.display)


def _day_window(period: str, level_key: str, number: str, pill: str) -> dict[str, Any]:
    """
    Build one row for the day-risk panel.

    Args:
        period: ``validTimePeriod`` value — ``all_day``, ``earlier``, ``later``.
        level_key: EAWS rating key, e.g. ``considerable``.
        number: The level's numeral, with any SLF subdivision suffix.
        pill: The time-window label shown in the row's pill.

    Returns:
        The row mapping ``includes/day_windows.html`` iterates.

    """
    return {
        "type": period,
        "level_key": level_key,
        "level_css": level_key.replace("_", "-"),
        "level_label": level_key.replace("_", " ").title(),
        "level_number": number,
        "caption": "",
        "pill_label": pill,
    }


def _bulletin_illustrations() -> dict[str, Any]:
    """
    Build the contexts for the two bulletin-page illustrations.

    Returns:
        ``day_windows`` for the day-risk topic, and ``card`` for the
        avalanche-problem topic.

    """
    return {
        "region_name": _("Martigny · Verbier"),
        "subregion_name": _("Valais"),
        "page_date": _GRID_TODAY,
        # A two-row day: considerable all day, rising later. That shape is
        # the one the copy has to explain, and a single-row day would not
        # show why the panel exists at all.
        "day_windows": [
            _day_window("all_day", "considerable", "3", str(_("All day"))),
            _day_window("later", "high", "4", str(_("Later"))),
        ],
        "card": {
            "category": "dry",
            "danger_level": 3,
            "danger_level_key": "considerable",
            "problem_type": "wind_slab",
            "time_period": "all_day",
            "panel_title": "",
            "title_time_suffix": str(_("all day")),
            "subdivision": "",
            "subdivision_label": "",
            # A contiguous lee-side set, as a south-westerly would build.
            # A scattered list would be a shape no real bulletin produces and
            # would not match the copy describing it.
            "aspects": ["N", "NE", "E", "SE"],
            "elevation": _ElevationBounds(
                lower="2200",
                upper="",
                display=str(_("above 2200m")),
                bound_type="LOWER",
            ),
            "comment_html": str(
                _(
                    "Fresh wind slabs have formed on lee slopes over the last two "
                    "days. They are most easily released at transitions from "
                    "shallow to deep snow, and can be triggered by one person."
                )
            ),
            "label": str(_("Wind slab")),
            "time_period_label": "",
            "hide_comment": False,
            "core_zone_text": str(_("North to south-east aspects, above 2200m")),
            "avalanche_type": None,
            "avalanche_size": 2,
            "frequency_label": None,
            "stability_label": None,
            "danger_patterns": [],
            "prose_mentions_spatial": False,
            "field_guidance": [],
        },
    }
