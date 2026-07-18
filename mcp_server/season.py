"""
mcp_server/season.py — Avalanche-season window resolution.

Bounds the ``get_danger_history`` MCP tool's query cost: a caller can only
ever pull data for a single avalanche season (Nov 1 -> May 31) and a single
region per call. ``current_or_last_season`` is the pure function that
decides which season window applies for a given calendar date.
"""

from __future__ import annotations

from datetime import date

# An avalanche season runs from 1 November to 31 May inclusive — matches
# how ``settings.SEASON_START_DATE`` is used as the ``fetch_bulletins``
# pipeline backstop (config/settings/base.py).
_SEASON_START_MONTH = 11
_SEASON_START_DAY = 1
_SEASON_END_MONTH = 5
_SEASON_END_DAY = 31


def current_or_last_season(today: date) -> tuple[date, date]:
    """Return the (start, end) bounds of the active or most recent season.

    A season spans 1 November of year *Y* to 31 May of year *Y+1*.
    ``today`` is always inside exactly one of two brackets:

    * November or December -> the season that just started this year
      (``Nov 1 this year`` -> ``May 31 next year``).
    * January through May -> the season that started last year and is
      either still running or just finished (``Nov 1 last year`` ->
      ``May 31 this year``).

    June through October fall in the second bracket too — there is no
    season "in progress" during the off-season, so the window returned is
    the most recently *completed* season rather than a future one.

    Deliberately takes ``today`` as a parameter rather than calling
    ``date.today()`` internally (inversion of control) so callers —
    production code and tests alike — control the clock explicitly.

    Args:
        today: The calendar date to resolve a season window for.

    Returns:
        A ``(season_start, season_end)`` tuple, both inclusive.

    """
    if today.month >= _SEASON_START_MONTH:
        season_start = date(today.year, _SEASON_START_MONTH, _SEASON_START_DAY)
        season_end = date(today.year + 1, _SEASON_END_MONTH, _SEASON_END_DAY)
    else:
        season_start = date(today.year - 1, _SEASON_START_MONTH, _SEASON_START_DAY)
        season_end = date(today.year, _SEASON_END_MONTH, _SEASON_END_DAY)
    return season_start, season_end
