"""
mcp_server/tools.py — MCP tool registry and handlers.

Five read-only tools, composed from services that already exist elsewhere
in the codebase (``mcp_server.resolvers``, ``public.views``,
``RegionDayRating.objects.for_region_range``, ``Bulletin.render_model``,
``Bulletin.get_avalanche_problems``):

* ``search_regions`` — fuzzy place-name search (``resolvers.search_places``).
* ``get_current_conditions`` — danger rating + forecaster prose for one
  region on one day.
* ``get_avalanche_problems`` — the structured avalanche-problem list
  (type, elevation, aspects, comment) for one region on one day.
* ``get_danger_history`` — per-day min/max ratings for one region over a
  date range, clamped to a single avalanche season.
* ``list_resorts_in_region`` — geocoded resorts within one region.

Each tool is implemented as a plain, typed Python function (the signature
a caller reasons about) plus a thin ``_handle_*`` adapter that unpacks the
JSON-RPC ``arguments`` dict and calls it — the adapter is what
``mcp_server.protocol`` invokes via the ``TOOLS`` registry. Keeping the
two separate means the business-logic functions stay directly unit
testable (e.g. passing an explicit ``today`` to make season clamping
deterministic) without needing to fake a JSON-RPC arguments dict in every
test.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.utils import timezone

from bulletins.models import RegionDayRating
from bulletins.schema import AvalancheProblem
from mcp_server import resolvers, season
from public.views import _select_bulletin_for_date
from regions.models import Resort

# Rank order for "at or above" comparisons in get_danger_history. Mirrors
# public/api.py's private _RATING_TO_INT, minus NO_RATING — duplicated
# rather than imported since that constant is a module-private
# implementation detail of a different app's choropleth encoding, not a
# shared public API. NO_RATING is deliberately excluded here (rather than
# ranked 0, as it is in _RATING_TO_INT): "at or above no_rating" is a null
# constraint that would match every day and helps no LLM caller, so
# min_rating="no_rating" is rejected as an invalid value instead of
# silently accepted (see the "min_rating" schema description below, which
# only ever advertised the five real ratings).
_RATING_RANK: dict[str, int] = {
    RegionDayRating.Rating.LOW: 1,
    RegionDayRating.Rating.MODERATE: 2,
    RegionDayRating.Rating.CONSIDERABLE: 3,
    RegionDayRating.Rating.HIGH: 4,
    RegionDayRating.Rating.VERY_HIGH: 5,
}

# The subset of Bulletin.render_model["prose"] the plan calls out as
# useful to an LLM client — excludes weather_review, tendency, and
# tendency_lead, which are template-specific rather than general-purpose
# prose.
_PROSE_FIELDS = ("snowpack_structure", "weather_forecast", "avalanche_activity")


class ToolError(Exception):
    """Raised by a tool's business logic for a domain-level failure.

    Caught by ``mcp_server.protocol._handle_tools_call`` and turned into an
    MCP ``CallToolResult`` with ``isError: true`` — an unknown region_id
    or a malformed date is a normal, successfully-routed ``tools/call``
    that the tool itself couldn't satisfy, not a JSON-RPC protocol error.
    """


@dataclass(frozen=True)
class ToolSpec:
    """One entry in the MCP tool registry, as advertised by ``tools/list``."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]


# ---------------------------------------------------------------------------
# Argument-parsing helpers shared by the _handle_* adapters
# ---------------------------------------------------------------------------


def _require_str(arguments: dict[str, Any], key: str) -> str:
    """Return ``arguments[key]`` as a non-blank string, or raise ToolError.

    Args:
        arguments: The raw JSON-RPC ``arguments`` dict.
        key: The required argument name.

    Returns:
        The argument value.

    """
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"'{key}' is required and must be a non-empty string.")
    return value


def _parse_iso_date(value: str, key: str) -> dt.date:
    """Parse an ISO ``YYYY-MM-DD`` string, or raise ToolError.

    Args:
        value: The raw string to parse.
        key: The argument name, for the error message.

    Returns:
        The parsed date.

    """
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"'{key}' is not a valid YYYY-MM-DD date: {value!r}.") from exc


def _required_iso_date(arguments: dict[str, Any], key: str) -> dt.date:
    """Return ``arguments[key]`` parsed as a required ISO date.

    Args:
        arguments: The raw JSON-RPC ``arguments`` dict.
        key: The required argument name.

    Returns:
        The parsed date.

    """
    value = arguments.get(key)
    if not isinstance(value, str):
        raise ToolError(f"'{key}' is required and must be a YYYY-MM-DD string.")
    return _parse_iso_date(value, key)


def _optional_iso_date(arguments: dict[str, Any], key: str) -> dt.date | None:
    """Return ``arguments[key]`` parsed as an optional ISO date.

    Args:
        arguments: The raw JSON-RPC ``arguments`` dict.
        key: The optional argument name.

    Returns:
        The parsed date, or ``None`` when the argument is absent.

    """
    value = arguments.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ToolError(f"'{key}' must be a YYYY-MM-DD string when supplied.")
    return _parse_iso_date(value, key)


# ---------------------------------------------------------------------------
# search_regions
# ---------------------------------------------------------------------------


def search_regions(query: str) -> dict[str, Any]:
    """Fuzzy-search resorts and regions by a free-text place name.

    Args:
        query: A place name, possibly misspelled, accent-stripped, or
            differently punctuated — or an exact ``region_id``.

    Returns:
        ``{query, results, count, summary}`` — ``results`` is
        ``resolvers.search_places``'s candidate list, best match first.

    """
    results = resolvers.search_places(query)
    return {
        "query": query,
        "results": results,
        "count": len(results),
        "summary": _search_regions_summary(query, results),
    }


def _search_regions_summary(query: str, results: list[dict[str, Any]]) -> str:
    """Return a one-line, LLM-quotable summary of a search_regions result."""
    if not results:
        return f"No regions or resorts matched {query!r}."
    names = ", ".join(f"{r['name']} ({r['region_id']})" for r in results[:5])
    return f"{len(results)} match(es) for {query!r}: {names}."


def _handle_search_regions(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``search_regions`` JSON-RPC arguments to the tool function."""
    query = _require_str(arguments, "query")
    return search_regions(query)


# ---------------------------------------------------------------------------
# get_current_conditions
# ---------------------------------------------------------------------------


def get_current_conditions(
    region_id: str,
    date: dt.date | None = None,
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Return the danger rating and forecaster prose for a region on a day.

    Args:
        region_id: Exact region identifier, e.g. ``"CH-4115"``.
        date: The day to look up. Defaults to ``today`` (or, if that is
            also unset, the real current date) when omitted.
        today: Overrides "today" for the default-date fallback — a
            settable-in-tests seam; production callers never pass this.

    Returns:
        A dict describing the day. When no bulletin covers ``date`` this
        is a structured "no data" result (``has_bulletin: False``), not
        an error — a quiet day with no forecast is a legitimate outcome.

    Raises:
        ToolError: ``region_id`` does not match any known region.

    """
    region = resolvers.resolve_region(region_id)
    if region is None:
        raise ToolError(f"Unknown region_id: {region_id!r}.")

    target_date = date or today or timezone.localdate()
    bulletin = _select_bulletin_for_date(region, target_date)
    if bulletin is None:
        return {
            "region_id": region.region_id,
            "region_name": region.name,
            "date": target_date.isoformat(),
            "has_bulletin": False,
            "summary": (
                f"No bulletin is available for {region.name} "
                f"({region.region_id}) on {target_date.isoformat()}."
            ),
        }

    danger = bulletin.render_model.get("danger") or {}
    prose_full = bulletin.render_model.get("prose") or {}
    prose = {key: prose_full.get(key) for key in _PROSE_FIELDS}
    return {
        "region_id": region.region_id,
        "region_name": region.name,
        "date": target_date.isoformat(),
        "has_bulletin": True,
        "danger_level": danger.get("key"),
        "danger_ratings": bulletin.highest_danger_rating(),
        "prose": prose,
        "summary": _current_conditions_summary(region, target_date, danger, prose),
    }


def _current_conditions_summary(
    region: Any,
    target_date: dt.date,
    danger: dict[str, Any],
    prose: dict[str, Any],
) -> str:
    """Return a one-line-plus-prose, LLM-quotable conditions summary."""
    level = (danger.get("key") or "no_rating").replace("_", " ")
    parts = [
        f"{region.name} ({region.region_id}) on {target_date.isoformat()}: "
        f"danger level {level}."
    ]
    if prose.get("snowpack_structure"):
        parts.append(prose["snowpack_structure"])
    if prose.get("weather_forecast"):
        parts.append(prose["weather_forecast"])
    return " ".join(parts)


def _handle_get_current_conditions(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``get_current_conditions`` JSON-RPC arguments to the tool function."""
    region_id = _require_str(arguments, "region_id")
    parsed_date = _optional_iso_date(arguments, "date")
    return get_current_conditions(region_id, parsed_date)


# ---------------------------------------------------------------------------
# get_avalanche_problems
# ---------------------------------------------------------------------------


def get_avalanche_problems(
    region_id: str,
    date: dt.date | None = None,
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Return the structured avalanche-problem list for a region on a day.

    The decision-support layer behind the scalar danger level returned by
    ``get_current_conditions`` — each problem carries its own type,
    elevation band, aspects, and forecaster comment.

    Args:
        region_id: Exact region identifier, e.g. ``"CH-4115"``.
        date: The day to look up. Defaults to ``today`` (or, if that is
            also unset, the real current date) when omitted.
        today: Overrides "today" for the default-date fallback — a
            settable-in-tests seam; production callers never pass this.

    Returns:
        A dict describing the day's problems. When no bulletin covers
        ``date`` this is a structured "no data" result (``has_bulletin:
        False``), not an error — a quiet day with no forecast is a
        legitimate outcome. A bulletin with no distinct problems (an empty
        ``avalancheProblems`` array) is also a legitimate, non-error
        outcome, distinguished from the no-bulletin case by
        ``has_bulletin: True`` and a different summary wording.

    Raises:
        ToolError: ``region_id`` does not match any known region.

    """
    region = resolvers.resolve_region(region_id)
    if region is None:
        raise ToolError(f"Unknown region_id: {region_id!r}.")

    target_date = date or today or timezone.localdate()
    bulletin = _select_bulletin_for_date(region, target_date)
    if bulletin is None:
        return {
            "region_id": region.region_id,
            "region_name": region.name,
            "date": target_date.isoformat(),
            "has_bulletin": False,
            "problems": [],
            "count": 0,
            "summary": (
                f"No bulletin is available for {region.name} "
                f"({region.region_id}) on {target_date.isoformat()}."
            ),
        }

    problems = [_serialise_problem(p) for p in bulletin.get_avalanche_problems()]
    return {
        "region_id": region.region_id,
        "region_name": region.name,
        "date": target_date.isoformat(),
        "has_bulletin": True,
        "problems": problems,
        "count": len(problems),
        "summary": _avalanche_problems_summary(region, target_date, problems),
    }


def _serialise_problem(problem: AvalancheProblem) -> dict[str, Any]:
    """Serialise one ``AvalancheProblem`` to a JSON-friendly dict.

    Curated to the subset of fields useful to an LLM caller —
    ``problem_type``, ``danger_rating_value``, ``valid_time_period``,
    ``elevation``, ``aspects``, ``comment``, ``avalanche_size``.
    Provider-specific fields (``subdivision``, ``avalanche_type``,
    ``snowpack_stability``, ``frequency``) are deliberately omitted.

    Args:
        problem: The dataclass instance to serialise.

    Returns:
        A dict with the curated field subset. ``elevation`` is ``None``
        when the source problem has no elevation constraint, otherwise a
        ``{lower_bound, upper_bound}`` dict (either bound may be
        ``None``). ``aspects`` is always a list.

    """
    elevation: dict[str, str | None] | None = None
    if problem.elevation is not None:
        elevation = {
            "lower_bound": problem.elevation.lower_bound,
            "upper_bound": problem.elevation.upper_bound,
        }
    return {
        "problem_type": problem.problem_type,
        "danger_rating_value": problem.danger_rating_value,
        "valid_time_period": problem.valid_time_period,
        "elevation": elevation,
        "aspects": list(problem.aspects),
        "comment": problem.comment,
        "avalanche_size": problem.avalanche_size,
    }


def _avalanche_problems_summary(
    region: Any,
    target_date: dt.date,
    problems: list[dict[str, Any]],
) -> str:
    """Return a one-line, LLM-quotable summary of a get_avalanche_problems result."""
    header = f"{region.name} ({region.region_id}) on {target_date.isoformat()}"
    if not problems:
        return f"{header}: 0 avalanche problems reported."
    noun = "avalanche problem" if len(problems) == 1 else "avalanche problems"
    described = ", ".join(
        f"{p['problem_type']} ({p['danger_rating_value']})"
        if p["danger_rating_value"]
        else p["problem_type"]
        for p in problems
    )
    return f"{header}: {len(problems)} {noun} — {described}."


def _handle_get_avalanche_problems(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``get_avalanche_problems`` JSON-RPC arguments to the tool function."""
    region_id = _require_str(arguments, "region_id")
    parsed_date = _optional_iso_date(arguments, "date")
    return get_avalanche_problems(region_id, parsed_date)


# ---------------------------------------------------------------------------
# get_danger_history
# ---------------------------------------------------------------------------


def get_danger_history(
    region_id: str,
    from_date: dt.date,
    to_date: dt.date,
    min_rating: str | None = None,
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Return per-day min/max danger ratings for a region over a date range.

    Cost-capped to a single region and a single avalanche season (Nov 1 ->
    May 31): the requested range is clamped to whichever season contains
    (or most recently preceded) ``today``.

    Args:
        region_id: Exact region identifier, e.g. ``"CH-4115"``.
        from_date: Requested range start (inclusive), before clamping.
        to_date: Requested range end (inclusive), before clamping.
        min_rating: Optional ``RegionDayRating.Rating`` value; when set,
            the response also counts days whose peak (``max_rating``)
            rating is at or above this threshold.
        today: Overrides "today" for season resolution — a
            settable-in-tests seam; production callers never pass this.

    Returns:
        A dict with the requested and effective date windows, the season
        bounds, whether clamping occurred, the per-day ratings, and (when
        ``min_rating`` is set) the qualifying-day count. A range that
        falls entirely outside the season window returns ``days: []``
        with ``clamped: True`` rather than an error.

    Raises:
        ToolError: ``region_id`` is unknown, or ``min_rating`` is not a
            recognised rating value.

    """
    region = resolvers.resolve_region(region_id)
    if region is None:
        raise ToolError(f"Unknown region_id: {region_id!r}.")
    if min_rating is not None and min_rating not in _RATING_RANK:
        raise ToolError(f"Invalid 'min_rating': {min_rating!r}.")

    resolved_today = today or timezone.localdate()
    season_start, season_end = season.current_or_last_season(resolved_today)
    effective_from = max(from_date, season_start)
    effective_to = min(to_date, season_end)
    clamped = effective_from != from_date or effective_to != to_date

    days: list[dict[str, Any]] = []
    if effective_from <= effective_to:
        rows = RegionDayRating.objects.for_region_range(
            region, effective_from, effective_to
        ).order_by("date")
        days = [
            {
                "date": row.date.isoformat(),
                "min_rating": row.min_rating,
                "max_rating": row.max_rating,
            }
            for row in rows
        ]

    count_at_or_above_min_rating: int | None = None
    if min_rating is not None:
        threshold = _RATING_RANK[min_rating]
        count_at_or_above_min_rating = sum(
            1 for d in days if _RATING_RANK.get(d["max_rating"], -1) >= threshold
        )

    return {
        "region_id": region.region_id,
        "region_name": region.name,
        "requested_from": from_date.isoformat(),
        "requested_to": to_date.isoformat(),
        "effective_from": effective_from.isoformat(),
        "effective_to": effective_to.isoformat(),
        "season_start": season_start.isoformat(),
        "season_end": season_end.isoformat(),
        "clamped": clamped,
        "days": days,
        "count": len(days),
        "count_at_or_above_min_rating": count_at_or_above_min_rating,
        "summary": _danger_history_summary(
            region,
            effective_from,
            effective_to,
            clamped,
            days,
            min_rating,
            count_at_or_above_min_rating,
        ),
    }


def _danger_history_summary(
    region: Any,
    effective_from: dt.date,
    effective_to: dt.date,
    clamped: bool,
    days: list[dict[str, Any]],
    min_rating: str | None,
    count_at_or_above_min_rating: int | None,
) -> str:
    """Return a one-line, LLM-quotable summary of a get_danger_history result."""
    if not days:
        return (
            f"No RegionDayRating data for {region.name} ({region.region_id}) "
            "in the requested range."
        )
    parts = [
        f"{region.name} ({region.region_id}): {len(days)} day(s) rated from "
        f"{effective_from} to {effective_to}."
    ]
    if clamped:
        parts.append("Range clamped to the avalanche season window.")
    if min_rating is not None:
        parts.append(
            f"{count_at_or_above_min_rating} day(s) at or above '{min_rating}'."
        )
    return " ".join(parts)


def _handle_get_danger_history(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``get_danger_history`` JSON-RPC arguments to the tool function."""
    region_id = _require_str(arguments, "region_id")
    from_date = _required_iso_date(arguments, "from_date")
    to_date = _required_iso_date(arguments, "to_date")
    min_rating = arguments.get("min_rating")
    if min_rating is not None and not isinstance(min_rating, str):
        raise ToolError("'min_rating' must be a string when supplied.")
    return get_danger_history(region_id, from_date, to_date, min_rating)


# ---------------------------------------------------------------------------
# list_resorts_in_region
# ---------------------------------------------------------------------------


def list_resorts_in_region(region_id: str) -> dict[str, Any]:
    """List the geocoded resorts within a single micro-region.

    Args:
        region_id: Exact region identifier, e.g. ``"CH-4115"``.

    Returns:
        A dict with the resort list (name, coordinates, canton), its
        count, and a plain-text summary.

    Raises:
        ToolError: ``region_id`` does not match any known region.

    """
    region = resolvers.resolve_region(region_id)
    if region is None:
        raise ToolError(f"Unknown region_id: {region_id!r}.")

    resorts = Resort.objects.geocoded().filter(region=region).select_related("region")
    resort_list = [
        {
            "name": resort.name,
            "latitude": resort.latitude,
            "longitude": resort.longitude,
            "canton": resort.canton,
        }
        for resort in resorts
    ]
    return {
        "region_id": region.region_id,
        "region_name": region.name,
        "resorts": resort_list,
        "count": len(resort_list),
        "summary": _list_resorts_summary(region, resort_list),
    }


def _list_resorts_summary(region: Any, resort_list: list[dict[str, Any]]) -> str:
    """Return a one-line, LLM-quotable summary of a list_resorts_in_region result."""
    if not resort_list:
        return f"No geocoded resorts found in {region.name} ({region.region_id})."
    names = ", ".join(r["name"] for r in resort_list[:10])
    return (
        f"{len(resort_list)} resort(s) in {region.name} ({region.region_id}): {names}."
    )


def _handle_list_resorts_in_region(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``list_resorts_in_region`` JSON-RPC arguments to the tool function."""
    region_id = _require_str(arguments, "region_id")
    return list_resorts_in_region(region_id)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

TOOLS: dict[str, ToolSpec] = {
    "search_regions": ToolSpec(
        name="search_regions",
        description=(
            "Fuzzy-search Snowdesk's ski resorts and avalanche-warning regions "
            "by a free-text place name. Tolerates missing accents, typos, and "
            "punctuation variance (e.g. 'Val d'Isere', 'Zermat', 'St Anton'). "
            "Returns up to 10 candidates ranked by match score. Call this "
            "first if you only have a place name rather than an exact "
            "region_id."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Place name to search for.",
                },
            },
            "required": ["query"],
        },
        handler=_handle_search_regions,
    ),
    "get_current_conditions": ToolSpec(
        name="get_current_conditions",
        description=(
            "Return the avalanche danger rating and forecaster prose "
            "(snowpack structure, weather forecast, avalanche activity) for "
            "one region on a given day. Defaults to today."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "region_id": {
                    "type": "string",
                    "description": (
                        "Exact region_id, e.g. 'CH-4115'. Use search_regions "
                        "first if you only have a place name."
                    ),
                },
                "date": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD. Defaults to today.",
                },
            },
            "required": ["region_id"],
        },
        handler=_handle_get_current_conditions,
    ),
    "get_avalanche_problems": ToolSpec(
        name="get_avalanche_problems",
        description=(
            "Return the structured avalanche-problem list (type, elevation "
            "band, aspects, forecaster comment) for one region on a given "
            "day — the decision-support detail behind the scalar danger "
            "level from get_current_conditions. Defaults to today."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "region_id": {
                    "type": "string",
                    "description": (
                        "Exact region_id, e.g. 'CH-4115'. Use search_regions "
                        "first if you only have a place name."
                    ),
                },
                "date": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD. Defaults to today.",
                },
            },
            "required": ["region_id"],
        },
        handler=_handle_get_avalanche_problems,
    ),
    "get_danger_history": ToolSpec(
        name="get_danger_history",
        description=(
            "Return per-day minimum/maximum danger ratings for one region "
            "over a date range. The range is clamped to a single avalanche "
            "season (1 November - 31 May) to bound query cost; the response "
            "reports the effective window and whether clamping occurred. "
            "Optionally counts days whose peak rating is at or above "
            "min_rating."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "region_id": {
                    "type": "string",
                    "description": "Exact region_id, e.g. 'CH-4115'.",
                },
                "from_date": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD, inclusive.",
                },
                "to_date": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD, inclusive.",
                },
                "min_rating": {
                    "type": "string",
                    "description": (
                        "Optional threshold: low|moderate|considerable|high|very_high."
                    ),
                },
            },
            "required": ["region_id", "from_date", "to_date"],
        },
        handler=_handle_get_danger_history,
    ),
    "list_resorts_in_region": ToolSpec(
        name="list_resorts_in_region",
        description=(
            "List the geocoded ski resorts within one avalanche-warning region."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "region_id": {
                    "type": "string",
                    "description": "Exact region_id, e.g. 'CH-4115'.",
                },
            },
            "required": ["region_id"],
        },
        handler=_handle_list_resorts_in_region,
    ),
}
