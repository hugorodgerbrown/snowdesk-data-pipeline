"""
apps/weather/icon_sets.py — Which drawing of the weather icons is served.

**Snowdesk draws its own icons** (``bin/build-weather-icons``), and that is
what ships. The other sets stay because choosing between them is a decision
that gets revisited, and the only honest way to judge a drawing is against
real data, at real sizes, on the real surfaces — so the served set stays
switchable rather than baked in, and ``/_icon-sets/`` shows all of them.

Every set ships the **same fourteen filenames**, so switching is a directory
change and nothing else. That is a deliberate normalisation: Meteocons
upstream carries a day/night pair for all twelve buckets where Yr draws one
only where a sun or moon appears, and reconciling those two schemes at
runtime would mean threading the active set through
``weather_icon_filename`` and its three callers. Collapsing Meteocons onto
the fourteen-file scheme costs its night variants for precipitation — which
no comparison of *legibility* depends on — and keeps the switch to one
string.

``meteoswiss`` and ``bbc`` live under ``_local/`` and are **gitignored**.
This repository is public, and both reserve all rights to their graphics;
the files are populated on a developer's machine, never committed and never
deployed. A set whose directory is absent simply does not appear in
:func:`available_icon_sets`, so a clone sees only the sets it may carry.
"""

from __future__ import annotations

import contextvars
import pathlib
from collections.abc import Callable

from django.conf import settings

# Set name -> path below STATIC_URL, trailing slash included so callers can
# concatenate a bare filename onto it.
ICON_SETS: dict[str, str] = {
    "snowdesk": "icons/weather/snowdesk/",
    "yr": "icons/weather/yr/",
    "meteocons": "icons/weather/meteocons/",
    "meteoswiss": "icons/weather/_local/meteoswiss/",
    "bbc": "icons/weather/_local/bbc/",
}

DEFAULT_ICON_SET = "snowdesk"

# How to rebuild the gitignored sets on another machine.
#
# These two cannot be committed (see the module docstring), so a fresh clone
# has neither and the comparison page silently drops their columns. This is
# the recipe, rendered at the top of ``/_icon-sets/`` so it lives with the
# thing it describes rather than in a doc nobody opens.
#
# Both are **manual on purpose**. MeteoSwiss's terms of use expressly forbid
# downloading their site with bots or other automated methods, so there is no
# fetch script here and there should not be one.
LOCAL_SET_SOURCES: dict[str, dict[str, object]] = {
    "meteoswiss": {
        "label": "MeteoSwiss",
        "source": "https://www.meteoswiss.admin.ch/static/resources/weather-symbols/<code>.svg",
        "notes": [
            "Save each file by hand — their terms forbid automated downloading.",
            "Ships ready to use: no colour or markup changes are needed.",
        ],
        # our filename -> their symbol code
        "mapping": {
            "clear-day": "1",
            "clear-night": "101",
            "partly_cloudy-day": "3",
            "partly_cloudy-night": "102",
            "cloudy": "5",
            "fog": "28",
            "drizzle": "6",
            "light_rain": "14",
            "moderate_rain": "20",
            "heavy_rain": "20",
            "light_snow": "30",
            "moderate_snow": "8",
            "heavy_snow": "8",
            "thunder": "24",
        },
    },
    "bbc": {
        "label": "BBC Weather",
        "source": "inline SVG sprite on any https://www.bbc.co.uk/weather/<id> page",
        "notes": [
            "Symbols are <symbol id='wr-icon-weather-type--<code>'>; wrap each in an "
            "<svg> carrying that symbol's own viewBox.",
            "They ship with NO fills — colours come from forecast.css. Resolve each "
            "class to its literal: thick-cloud #000000, light-cloud #969696, "
            "snowflake #969696, hailstone #969696, raindrop and drizzle #3789c6, "
            "lightning #6db1de, sun and partial-sun #fdc400, moon and partial-moon "
            "#b4b4b4, text and dash #000000.",
            "Drop the opaque white rect (class ...__svg-background): every "
            "other set here is transparent, and a white tile misrepresents "
            "them on the dark card.",
            "Some codes are duplicate artwork (8 == 37, 6 == 35); either twin works.",
        ],
        "mapping": {
            "clear-day": "1",
            "clear-night": "0",
            "partly_cloudy-day": "3",
            "partly_cloudy-night": "2",
            "cloudy": "8",
            "fog": "6",
            "drizzle": "11",
            "light_rain": "12",
            "moderate_rain": "15",
            "heavy_rain": "15",
            "light_snow": "24",
            "moderate_snow": "27",
            "heavy_snow": "27",
            "thunder": "30",
        },
    },
}

# Which sets need the silhouette edge painted FOR them.
#
# The edge is a blur — ``drop-shadow(0 0 1px …)`` twice — so it is a cost as
# well as a benefit: it softens every mark it passes over, which turns a
# six-armed flake into a blob at 27 px. It is worth paying only where the
# artwork has no edge of its own.
#
# Meteocons draws its cloud near-white (1.16:1 on the light card) and Yr at
# #dddddd (1.36:1); both dissolve on a white plate without help. The Snowdesk
# set paints a fixed mid-grey edge into the artwork, MeteoSwiss ships its own
# shading, and the BBC draws its cloud in solid black — so for those three
# the filter would blur without buying anything.
SETS_NEEDING_HALO: frozenset[str] = frozenset({"yr", "meteocons"})


def active_set_needs_halo() -> bool:
    """Whether the active set needs the CSS/canvas silhouette edge.

    Returns:
        ``True`` when the set's own artwork carries no edge.

    """
    return active_icon_set_name() in SETS_NEEDING_HALO


# The set active for the request being served.
#
# A ContextVar rather than a template variable because every weather partial
# is included with ``only``, which strips the parent context — so a context
# processor's value never reaches the five templates that need it, and
# threading it through six ``include`` call sites is a rule waiting to be
# forgotten.
#
# It holds a *resolver*, not a name. Resolving reads the session, and reading
# the session both opens a database query and stamps ``Vary: Cookie`` — so a
# request that never renders an icon must never resolve. ``/livez`` is the
# case that proves it: it carries a session and is required to issue zero
# queries (tests/core/test_views.py).
#
# **Everything that needs the active set must go through
# :func:`active_icon_set_name`**, never read the session itself. The
# switcher strip did read it directly and showed the previous choice: a
# context processor runs before the template body, so it saw the session as
# it was before the lazy resolver had recorded the new one. One resolver,
# called from both, cannot disagree with itself.
_resolver: contextvars.ContextVar[Callable[[], str | None] | None] = (
    contextvars.ContextVar("weather_icon_set_resolver", default=None)
)


def active_icon_set_name() -> str:
    """Return the name of the icon set active for this request.

    Returns:
        The pinned set if one was requested, otherwise
        ``settings.WEATHER_ICON_SET``. Resolving reads the session, so call
        this only where an icon (or the switcher) is actually being drawn.

    """
    resolve = _resolver.get()
    return (resolve() if resolve is not None else None) or settings.WEATHER_ICON_SET


def set_active_icon_set_resolver(resolve: Callable[[], str | None] | None) -> None:
    """Install the callable that names this request's pinned icon set.

    Args:
        resolve: Called at most once, and only if an icon is actually
            rendered. ``None`` clears any previous resolver.

    """
    _resolver.set(resolve)


def active_icon_dir() -> str:
    """Return the static-relative directory for the active icon set.

    Calls the installed resolver, if any — so the session is read only when
    a template actually asks for an icon path.

    Returns:
        The directory, trailing slash included.

    """
    return icon_set_dir(active_icon_set_name())


def icon_set_dir(name: str | None) -> str:
    """Return the static-relative directory for an icon set.

    Args:
        name: A key of :data:`ICON_SETS`, or ``None``.

    Returns:
        The directory, trailing slash included. An unknown or missing name
        falls back to :data:`DEFAULT_ICON_SET` rather than raising — a bad
        value in the environment should not take the site out.

    """
    return ICON_SETS.get(name or "", ICON_SETS[DEFAULT_ICON_SET])


def available_icon_sets() -> list[str]:
    """Return the icon sets whose directory actually exists on disk.

    ``meteoswiss`` and ``bbc`` are gitignored, so they are present on a
    machine where they have been populated and absent everywhere else.
    Listing by what is on disk means the switch offers exactly what it can
    serve, and a deployed site offers only the sets it may carry.

    Returns:
        Set names, in :data:`ICON_SETS` order.

    """
    root = pathlib.Path(settings.BASE_DIR) / "static"
    return [name for name, rel in ICON_SETS.items() if (root / rel).is_dir()]
