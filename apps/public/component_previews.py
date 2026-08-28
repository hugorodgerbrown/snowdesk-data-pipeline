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

import datetime
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
        "context_line": _("Routes are private and not shared."),
        "section_label": _("Tracks"),
        "cta_label": _("Add a route"),
        "toggle_id": "help-illustration-toggle-routes",
        "rows": (
            # Mirrors the real row's "12.4 km · 850 m ascent · 900 m descent"
            # shape from routes/partials/_route.html.
            {"label": _("Rosablanche"), "meta": _("14.2 km · 1,320 m ascent")},
            {"label": _("Pigne d'Arolla"), "meta": _("11.8 km · 1,540 m ascent")},
        ),
    },
    "downloads": {
        "title": _("Downloads on this device"),
        "icon_template": "includes/_icon_downloads.html",
        "context_line": _("Downloads and budget stay on this device."),
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


def help_illustrations() -> dict[str, Any]:
    """
    Build the illustration context for the /help/ page.

    Returns:
        A mapping consumed by ``public/help.html``: ``season_calendar`` for
        the heatmap topic, and ``panels`` keyed by topic for the four that
        share the UGC panel shell.

        The season scrubber is deliberately absent — its styles live in
        ``static/css/map.css``, which ``/help/`` does not load. See that
        topic's comment in ``public/help.html``.

    """
    return {
        "season_calendar": synthetic_season_grid(),
        "panels": _PANELS,
    }
