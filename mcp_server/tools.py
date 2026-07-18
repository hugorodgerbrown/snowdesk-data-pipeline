"""
mcp_server/tools.py — MCP tool registry and handlers.

Read-only tools, composed from services that already exist elsewhere in
the codebase (``mcp_server.resolvers``, ``public.views``,
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
* ``get_bulletin_metadata`` — provenance (issued_at, source, source_url,
  language variants) for one region on one day.
* ``get_bulletin_raw`` — full CAAML v6 payload escape hatch for one
  region on one day.
* ``bulk_current_conditions`` — batched fan-out over
  ``get_current_conditions``, capped at 20 region_ids per call.
* ``find_regions_near`` — haversine reverse geolocation over
  ``MicroRegion.centre``; radius capped at 100 km per call.
* ``get_danger_trend`` — trailing-window danger series plus computed
  direction / change_point / current_streak layers.
* ``get_regional_snapshot`` — per-region peak-danger entries plus a
  scope-level summary for a whole country or major region, sourced from
  ``RegionDayRating`` (one indexed query, not an N-region fan-out).

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

from bulletins.models import Bulletin, RegionDayRating
from bulletins.schema import AvalancheProblem
from mcp_server import resolvers, season
from public.api import COUNTRY_NAMES
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
# get_bulletin_metadata
# ---------------------------------------------------------------------------


# Canonical human-facing pages per provider, used when the bulletin row has
# no direct ``pdf_url`` recorded. An LLM caller quoting attribution wants a
# page a human can click through to, not a raw JSON endpoint.
_SOURCE_HOME_URL: dict[str, str] = {
    Bulletin.Source.SLF: "https://www.slf.ch/en/avalanche-bulletin-and-snow-situation/",
    Bulletin.Source.ALBINA: "https://avalanche.report/",
    Bulletin.Source.METEOFRANCE: (
        "https://meteofrance.com/meteo-montagne/risque-avalanche"
    ),
}


def _source_from_render_model(bulletin: Bulletin) -> str | None:
    """Return the provider source string stamped on a bulletin's render_model.

    Returns ``None`` when the render model failed to build (version 0) and
    the source stamp is therefore missing — a rare but possible state that
    the tool response reports as ``source_provider: null`` rather than
    guessing a value.
    """
    source = bulletin.render_model.get("source") if bulletin.render_model else None
    return source if isinstance(source, str) and source else None


def get_bulletin_metadata(
    region_id: str,
    date: dt.date | None = None,
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Return provenance metadata for a bulletin covering one region/day.

    Answers "when was this bulletin issued?", "when is the next update
    expected?", and "where's the canonical source I can cite?" — the
    fields a downstream LLM needs to avoid hallucinating attribution.

    Args:
        region_id: Exact region identifier, e.g. ``"CH-4115"``.
        date: The day to look up. Defaults to today.
        today: Overrides "today" for the default-date fallback — a
            settable-in-tests seam; production callers never pass this.

    Returns:
        A dict with ``region_id``, ``date``, ``has_bulletin`` and, when a
        bulletin is available, ``issued_at``, ``valid_from``, ``valid_to``,
        ``next_update_expected``, ``source_provider``, ``source_url``,
        ``language`` and ``language_variants_available``. A day with no
        covering bulletin is a structured empty result, not an error.

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

    source = _source_from_render_model(bulletin)
    # bulletin_id is unique, so filtering by it can only ever return this
    # same row — language variants aren't stored side-by-side. Report the
    # single language the row was fetched in; other-language variants live
    # on the provider, not in the Snowdesk DB.
    language_variants = [bulletin.lang] if bulletin.lang else []
    source_url = bulletin.pdf_url or (_SOURCE_HOME_URL.get(source) if source else "")
    return {
        "region_id": region.region_id,
        "region_name": region.name,
        "date": target_date.isoformat(),
        "has_bulletin": True,
        "bulletin_id": bulletin.bulletin_id,
        "issued_at": bulletin.issued_at.isoformat(),
        "valid_from": bulletin.valid_from.isoformat(),
        "valid_to": bulletin.valid_to.isoformat(),
        "next_update_expected": (
            bulletin.next_update.isoformat() if bulletin.next_update else None
        ),
        "source_provider": source,
        "source_url": source_url,
        "language": bulletin.lang or None,
        "language_variants_available": language_variants,
        "summary": _bulletin_metadata_summary(region, target_date, bulletin, source),
    }


def _bulletin_metadata_summary(
    region: Any,
    target_date: dt.date,
    bulletin: Bulletin,
    source: str | None,
) -> str:
    """Return a one-line, LLM-quotable summary of a metadata result."""
    provider = source or "unknown provider"
    parts = [
        f"{region.name} ({region.region_id}) bulletin for {target_date.isoformat()}: "
        f"issued {bulletin.issued_at.isoformat()} by {provider}."
    ]
    if bulletin.next_update:
        parts.append(f"Next update expected {bulletin.next_update.isoformat()}.")
    return " ".join(parts)


def _handle_get_bulletin_metadata(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``get_bulletin_metadata`` JSON-RPC arguments to the tool function."""
    region_id = _require_str(arguments, "region_id")
    parsed_date = _optional_iso_date(arguments, "date")
    return get_bulletin_metadata(region_id, parsed_date)


# ---------------------------------------------------------------------------
# bulk_current_conditions
# ---------------------------------------------------------------------------

# Per-call cap on the region_ids batch. Sized to the "generous batch" tier
# the scoping comment established alongside get_danger_history's one-region
# ceiling: at 20 sequential ORM reads per call, worst-case cost stays well
# within the endpoint's 60/min rate-limit ceiling. Revisit against real
# usage before raising further.
_BULK_CURRENT_CONDITIONS_CAP = 20


def bulk_current_conditions(
    region_ids: list[str],
    date: dt.date | None = None,
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Return current conditions for a batch of regions on one day.

    Fan-out over :func:`get_current_conditions`, returning one entry per
    ``region_id`` in the input order. Unknown region_ids come back as a
    per-item ``{region_id, has_bulletin: false, error: "unknown_region"}``
    rather than failing the whole call — a client's stale region_id
    shouldn't wipe out the other 19.

    Args:
        region_ids: Up to :data:`_BULK_CURRENT_CONDITIONS_CAP` region_ids.
            Duplicates are allowed and return duplicate entries — the
            caller's own bug, not something for the tool to hide.
        date: The day to look up. Defaults to today.
        today: Overrides "today" for the default-date fallback — a
            settable-in-tests seam; production callers never pass this.

    Returns:
        ``{query, results, count, summary}`` — ``results`` is a list of
        per-region dicts matching :func:`get_current_conditions`'s
        response shape, or ``{region_id, has_bulletin: false, error:
        "unknown_region"}`` for stale/unknown region_ids.

    """
    # Batch shape validation lives in the JSON-RPC handler (raises
    # ProtocolError → -32602). The business-logic function trusts what
    # it's given so unit tests don't have to fake the JSON-RPC layer.
    resolved_today = today or timezone.localdate()
    target_date = date or resolved_today

    results: list[dict[str, Any]] = []
    for region_id in region_ids:
        region = resolvers.resolve_region(region_id)
        if region is None:
            results.append(
                {
                    "region_id": region_id,
                    "has_bulletin": False,
                    "error": "unknown_region",
                    "summary": f"Unknown region_id: {region_id!r}.",
                }
            )
            continue
        results.append(
            get_current_conditions(region_id, target_date, today=resolved_today)
        )

    return {
        "query": {
            "region_ids": list(region_ids),
            "date": target_date.isoformat(),
        },
        "results": results,
        "count": len(results),
        "summary": _bulk_current_conditions_summary(target_date, results),
    }


def _bulk_current_conditions_summary(
    target_date: dt.date, results: list[dict[str, Any]]
) -> str:
    """Return a one-line, LLM-quotable summary of a bulk_current_conditions result."""
    with_bulletin = sum(1 for r in results if r.get("has_bulletin"))
    unknown = sum(1 for r in results if r.get("error") == "unknown_region")
    parts = [
        f"{len(results)} region(s) queried for {target_date.isoformat()}: "
        f"{with_bulletin} with bulletin"
    ]
    if unknown:
        parts.append(f"{unknown} unknown region_id(s)")
    return ", ".join(parts) + "."


def _handle_bulk_current_conditions(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``bulk_current_conditions`` JSON-RPC arguments to the tool function.

    Enforces the batch-shape contract from the scoping comment: rejects an
    empty batch or one that exceeds the cap as JSON-RPC ``-32602``, rather
    than as a domain-level ``isError: true`` — batch shape is an input
    contract, not a domain failure the tool tried and couldn't satisfy.
    """
    # Local import — see the module-level import in mcp_server.protocol; a
    # top-level import here would create a cycle (protocol imports tools).
    from mcp_server.protocol import INVALID_PARAMS, ProtocolError

    raw = arguments.get("region_ids")
    if not isinstance(raw, list):
        raise ProtocolError(
            INVALID_PARAMS, "'region_ids' is required and must be an array of strings."
        )
    if not raw:
        raise ProtocolError(INVALID_PARAMS, "'region_ids' must not be empty.")
    if len(raw) > _BULK_CURRENT_CONDITIONS_CAP:
        raise ProtocolError(
            INVALID_PARAMS,
            f"'region_ids' has {len(raw)} entries; the cap is "
            f"{_BULK_CURRENT_CONDITIONS_CAP} per call.",
        )
    for item in raw:
        if not isinstance(item, str) or not item.strip():
            raise ProtocolError(
                INVALID_PARAMS,
                "'region_ids' entries must be non-empty strings.",
            )
    parsed_date = _optional_iso_date(arguments, "date")
    return bulk_current_conditions(raw, parsed_date)


# ---------------------------------------------------------------------------
# get_bulletin_raw
# ---------------------------------------------------------------------------


def get_bulletin_raw(
    region_id: str,
    date: dt.date | None = None,
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Return the full CAAML v6 payload for a region + day.

    Machine-readable escape hatch: when a flattened tool has dropped a
    field the caller needs, this returns the source-shape CAAML JSON
    (wrapped in the GeoJSON Feature envelope Snowdesk stores). Payload
    runs ~10-30 KB per bulletin; prefer the flattened tools for the
    common questions.

    Args:
        region_id: Exact region identifier, e.g. ``"CH-4115"``.
        date: The day to look up. Defaults to today.
        today: Overrides "today" for the default-date fallback — a
            settable-in-tests seam; production callers never pass this.

    Returns:
        A dict with the standard wrapping metadata (``region_id``,
        ``date``, ``has_bulletin``, ``provider``, ``issued_at``) and the
        raw ``caaml`` payload verbatim. When no bulletin covers ``date``
        the result carries ``has_bulletin: false`` and no ``caaml`` key —
        a quiet day is a legitimate empty result, not an error.

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

    source = _source_from_render_model(bulletin)
    return {
        "region_id": region.region_id,
        "region_name": region.name,
        "date": target_date.isoformat(),
        "has_bulletin": True,
        "provider": source,
        "issued_at": bulletin.issued_at.isoformat(),
        "caaml": bulletin.raw_data,
        "summary": (
            f"Raw CAAML payload for {region.name} ({region.region_id}) on "
            f"{target_date.isoformat()} (issued {bulletin.issued_at.isoformat()})."
        ),
    }


def _handle_get_bulletin_raw(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``get_bulletin_raw`` JSON-RPC arguments to the tool function."""
    region_id = _require_str(arguments, "region_id")
    parsed_date = _optional_iso_date(arguments, "date")
    return get_bulletin_raw(region_id, parsed_date)


# ---------------------------------------------------------------------------
# find_regions_near
# ---------------------------------------------------------------------------

# Hard cap on the radius input, in kilometres. 100 km is generous for a
# single trailhead-to-region lookup (roughly Verbier → Zurich) but keeps
# the loop from turning into a whole-Alps sweep, which is what
# search_regions and get_current_conditions are for.
_FIND_REGIONS_NEAR_RADIUS_CAP_KM = 100.0

# Default result count when the caller doesn't specify — same tier as
# search_regions.
_FIND_REGIONS_NEAR_DEFAULT_LIMIT = 10


def find_regions_near(
    lat: float, lon: float, radius_km: float, limit: int | None = None
) -> dict[str, Any]:
    """Return covered regions inside a radius of one point, nearest first.

    Reverse geolocation: skiers know their trailhead coordinates from a
    mapping app but not the SLF region slug. ``search_regions`` doesn't
    help when the place isn't named; this tool bridges that gap.

    Args:
        lat: Latitude in degrees, ``[-90, 90]``.
        lon: Longitude in degrees, ``[-180, 180]``.
        radius_km: Radius bound in kilometres.
        limit: Maximum results (nearest first). Defaults to 10.

    Returns:
        ``{query: {lat, lon, radius_km}, results, count, summary}`` —
        ``results`` is a list of ``{region_id, name, kind, distance_km,
        provider}`` dicts. Empty when no MicroRegion falls within the
        radius (e.g. a query point outside Snowdesk's coverage) — a
        legitimate empty result, not an error.

    """
    resolved_limit = limit if limit is not None else _FIND_REGIONS_NEAR_DEFAULT_LIMIT
    results = resolvers.find_places_near(lat, lon, radius_km, resolved_limit)
    return {
        "query": {"lat": lat, "lon": lon, "radius_km": radius_km},
        "results": results,
        "count": len(results),
        "summary": _find_regions_near_summary(lat, lon, radius_km, results),
    }


def _find_regions_near_summary(
    lat: float, lon: float, radius_km: float, results: list[dict[str, Any]]
) -> str:
    """Return a one-line, LLM-quotable summary of a find_regions_near result."""
    if not results:
        return f"No covered regions within {radius_km:g} km of ({lat:g}, {lon:g})."
    names = ", ".join(
        f"{r['name']} ({r['region_id']}, {r['distance_km']:g} km)" for r in results[:5]
    )
    return (
        f"{len(results)} region(s) within {radius_km:g} km of "
        f"({lat:g}, {lon:g}): {names}."
    )


def _handle_find_regions_near(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``find_regions_near`` JSON-RPC arguments to the tool function.

    Rejects invalid geographic inputs and over-cap radius as JSON-RPC
    ``-32602`` — these are input-contract failures, not domain-level
    outcomes the tool tried and couldn't satisfy.
    """
    from mcp_server.protocol import INVALID_PARAMS, ProtocolError

    lat = arguments.get("lat")
    lon = arguments.get("lon")
    radius_km = arguments.get("radius_km")
    if not isinstance(lat, int | float) or not -90 <= lat <= 90:
        raise ProtocolError(INVALID_PARAMS, "'lat' must be a number in [-90, 90].")
    if not isinstance(lon, int | float) or not -180 <= lon <= 180:
        raise ProtocolError(INVALID_PARAMS, "'lon' must be a number in [-180, 180].")
    if not isinstance(radius_km, int | float) or radius_km <= 0:
        raise ProtocolError(INVALID_PARAMS, "'radius_km' must be a positive number.")
    if radius_km > _FIND_REGIONS_NEAR_RADIUS_CAP_KM:
        raise ProtocolError(
            INVALID_PARAMS,
            f"'radius_km' is {radius_km:g}; the cap is "
            f"{_FIND_REGIONS_NEAR_RADIUS_CAP_KM:g} km per call.",
        )
    limit = arguments.get("limit")
    if limit is not None and (not isinstance(limit, int) or limit <= 0):
        raise ProtocolError(
            INVALID_PARAMS, "'limit' must be a positive integer when supplied."
        )
    return find_regions_near(float(lat), float(lon), float(radius_km), limit)


# ---------------------------------------------------------------------------
# get_danger_trend
# ---------------------------------------------------------------------------

# Default trailing window in days when the caller doesn't specify. Two
# weeks is short enough to be a "trend" (recent snowpack story) rather
# than a "history" question — get_danger_history covers arbitrary
# ranges.
_TREND_DEFAULT_DAYS = 14

# Hard cap on the trend window. Same shape as get_danger_history: the
# season-clamp already bounds cost, so the cap here is about semantics
# (a 200-day "trend" isn't a trend) rather than query cost.
_TREND_MAX_DAYS = 90

# Threshold, in rating rank steps, above which a mean-shift between the
# first and last third of the window is called a directional trend.
# Half a rating step means the shift must be at least the equivalent of
# half the population moving up one rating level; smaller shifts are
# noise.
_TREND_DIRECTION_THRESHOLD = 0.5


def get_danger_trend(
    region_id: str,
    days: int | None = None,
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Return the danger rating series plus a computed trend layer.

    Answers "is danger rising or falling in Verbier?" in one call.
    Wraps :func:`get_danger_history` (so season-clamp, region
    resolution, and DB reads are shared verbatim) and layers three
    pure-function helpers on top: ``direction`` (rising / stable /
    falling), ``change_point`` (most recent rating transition), and
    ``current_streak`` (trailing days at the current peak rating).

    Args:
        region_id: Exact region identifier, e.g. ``"CH-4115"``.
        days: Trailing window length in days. Defaults to
            :data:`_TREND_DEFAULT_DAYS`; cap :data:`_TREND_MAX_DAYS`.
        today: Overrides "today" for the trailing-window end — a
            settable-in-tests seam; production callers never pass this.

    Returns:
        The full :func:`get_danger_history` envelope with three added
        fields: ``direction``, ``change_point`` (nullable), and
        ``current_streak`` (nullable).

    Raises:
        ToolError: ``region_id`` is unknown, ``days`` is not a positive
            integer, or ``days`` exceeds :data:`_TREND_MAX_DAYS`.

    """
    resolved_days = _TREND_DEFAULT_DAYS if days is None else days
    if not isinstance(resolved_days, int) or resolved_days <= 0:
        raise ToolError("'days' must be a positive integer.")
    if resolved_days > _TREND_MAX_DAYS:
        raise ToolError(f"'days' is {resolved_days}; the cap is {_TREND_MAX_DAYS}.")

    resolved_today = today or timezone.localdate()
    to_date = resolved_today
    from_date = to_date - dt.timedelta(days=resolved_days - 1)

    envelope = get_danger_history(region_id, from_date, to_date, today=resolved_today)

    days_series = envelope["days"]
    direction = _direction(days_series)
    change_point = _change_point(days_series)
    current_streak = _current_streak(days_series)

    envelope["direction"] = direction
    envelope["change_point"] = change_point
    envelope["current_streak"] = current_streak
    envelope["summary"] = _danger_trend_summary(
        envelope, direction, change_point, current_streak
    )
    return envelope


def _direction(days_series: list[dict[str, Any]]) -> str:
    """Classify a peak-rating series as rising, falling, or stable.

    Compares the mean rank of the first third of the window to the last
    third. A difference of :data:`_TREND_DIRECTION_THRESHOLD` rating
    steps or more classifies as ``"rising"`` / ``"falling"``; smaller
    shifts are ``"stable"``. Series with fewer than three data points
    are ``"stable"`` (nothing meaningful to infer from one or two rows).

    Args:
        days_series: A list of ``{date, min_rating, max_rating}`` dicts,
            ordered by date.

    Returns:
        ``"rising"``, ``"falling"``, or ``"stable"``.

    """
    ranks = [_RATING_RANK.get(d["max_rating"], 0) for d in days_series]
    ranks = [r for r in ranks if r > 0]
    if len(ranks) < 3:
        return "stable"
    third = max(1, len(ranks) // 3)
    first_mean = sum(ranks[:third]) / third
    last_mean = sum(ranks[-third:]) / third
    delta = last_mean - first_mean
    if delta >= _TREND_DIRECTION_THRESHOLD:
        return "rising"
    if delta <= -_TREND_DIRECTION_THRESHOLD:
        return "falling"
    return "stable"


def _change_point(days_series: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the most recent day whose peak rating differs from the prior day's.

    Args:
        days_series: A list of ``{date, min_rating, max_rating}`` dicts,
            ordered by date.

    Returns:
        ``{date, from_rating, to_rating}`` for the most recent transition,
        or ``None`` when the series is empty or flat.

    """
    for i in range(len(days_series) - 1, 0, -1):
        prev = days_series[i - 1]
        curr = days_series[i]
        if curr["max_rating"] != prev["max_rating"]:
            return {
                "date": curr["date"],
                "from_rating": prev["max_rating"],
                "to_rating": curr["max_rating"],
            }
    return None


def _current_streak(days_series: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the trailing consecutive-day count at the current peak rating.

    Args:
        days_series: A list of ``{date, min_rating, max_rating}`` dicts,
            ordered by date.

    Returns:
        ``{rating, days}`` for the trailing streak, or ``None`` when the
        series is empty.

    """
    if not days_series:
        return None
    current_rating = days_series[-1]["max_rating"]
    streak = 0
    for entry in reversed(days_series):
        if entry["max_rating"] != current_rating:
            break
        streak += 1
    return {"rating": current_rating, "days": streak}


def _danger_trend_summary(
    envelope: dict[str, Any],
    direction: str,
    change_point: dict[str, Any] | None,
    current_streak: dict[str, Any] | None,
) -> str:
    """Return a one-line, LLM-quotable summary of a get_danger_trend result."""
    days = envelope["days"]
    if not days:
        # Reuse the empty-window message from get_danger_history.
        # Cast to str — envelope is typed as dict[str, Any] so mypy would
        # otherwise complain about returning Any from a -> str function.
        return str(envelope["summary"])
    parts = [
        f"{envelope['region_name']} ({envelope['region_id']}) over "
        f"{envelope['count']} day(s): trend {direction}."
    ]
    if current_streak is not None:
        parts.append(
            f"Current streak: {current_streak['days']} day(s) at "
            f"'{current_streak['rating']}'."
        )
    if change_point is not None:
        parts.append(
            f"Last change on {change_point['date']}: "
            f"{change_point['from_rating']} → {change_point['to_rating']}."
        )
    return " ".join(parts)


def _handle_get_danger_trend(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``get_danger_trend`` JSON-RPC arguments to the tool function."""
    region_id = _require_str(arguments, "region_id")
    days = arguments.get("days")
    if days is not None and not isinstance(days, int):
        raise ToolError("'days' must be an integer when supplied.")
    return get_danger_trend(region_id, days)


# ---------------------------------------------------------------------------
# get_regional_snapshot
# ---------------------------------------------------------------------------


def _scope_label(country: str | None, major_region_id: str | None) -> str:
    """Return a human-readable label for a get_regional_snapshot scope.

    Args:
        country: The normalised (upper-cased) country code, or ``None``.
        major_region_id: The normalised major-region prefix, or ``None``.

    Returns:
        The country's English name (e.g. ``"Switzerland"``) when scoped by
        country, else the major-region prefix itself — the tool doesn't
        look up the MajorRegion's own name, since a major-region scope may
        legitimately resolve to zero MicroRegions.

    """
    if country:
        return COUNTRY_NAMES.get(country, country)
    return major_region_id or "the requested scope"


def _peak_rating_population(
    region_entries: list[dict[str, Any]],
) -> tuple[str | None, int]:
    """Return the highest ``danger_level`` present and how many regions carry it.

    Args:
        region_entries: The per-region entries from
            :func:`get_regional_snapshot`, each carrying a
            ``danger_level`` that is ``None`` when ``has_bulletin`` is
            ``False``.

    Returns:
        ``(rating, count)`` for the highest-ranked ``danger_level`` value
        present, or ``(None, 0)`` when no entry carries a rating.

    """
    ranked = [
        (entry["danger_level"], _RATING_RANK.get(entry["danger_level"], 0))
        for entry in region_entries
        if entry["danger_level"] is not None
    ]
    if not ranked:
        return None, 0
    highest_rank = max(rank for _rating, rank in ranked)
    peak_rating = next(rating for rating, rank in ranked if rank == highest_rank)
    peak_count = sum(1 for _rating, rank in ranked if rank == highest_rank)
    return peak_rating, peak_count


def get_regional_snapshot(
    country: str | None = None,
    major_region_id: str | None = None,
    date: dt.date | None = None,
    *,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Return a per-region peak-danger snapshot for a country or major region.

    Answers "where should I go?" in one call: given a country (ISO-2) or a
    ``MajorRegion.prefix``, returns one entry per covered MicroRegion with
    its peak (``max_rating``) and minimum (``min_rating``) danger rating
    for the day, plus a scope-level summary. A natural companion to
    :func:`bulk_current_conditions` for the case where the caller doesn't
    yet know which ``region_ids`` they want.

    Sourced from ``RegionDayRating`` — one indexed query per call rather
    than an N-region fan-out over :func:`get_current_conditions` — the
    same table :func:`get_danger_history` and :func:`get_danger_trend`
    already read from, and the peak-rating semantic already established
    for the choropleth, map tooltip, and season calendar
    (``docs/compressed-views-rating-rule.md``).

    Args:
        country: ISO-3166-1 alpha-2 country code, e.g. ``"CH"``. Mutually
            exclusive with ``major_region_id`` — exactly one is required.
        major_region_id: A ``MajorRegion.prefix``, e.g. ``"CH-4"``.
            Mutually exclusive with ``country``.
        date: The day to look up. Defaults to ``today`` (or, if that is
            also unset, the real current date) when omitted.
        today: Overrides "today" for the default-date fallback — a
            settable-in-tests seam; production callers never pass this.

    Returns:
        ``{scope, date, regions, count, count_with_bulletin, summary}``.
        ``regions`` is one entry per MicroRegion in scope, ordered by
        ``region_id``: ``{region_id, name, danger_level, min_rating,
        has_bulletin}``. ``danger_level`` (the peak, i.e.
        ``RegionDayRating.max_rating``) and ``min_rating`` are ``None``
        when ``has_bulletin`` is ``False`` — a region with no
        ``RegionDayRating`` row for that day is a legitimate outcome, not
        an error.

    Raises:
        ToolError: neither or both of ``country`` / ``major_region_id``
            are supplied, ``country`` isn't a recognised ISO-2 code, or
            ``major_region_id`` doesn't match any known ``MajorRegion``.

    """
    try:
        regions = resolvers.regions_for_scope(country, major_region_id)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc

    target_date = date or today or timezone.localdate()

    ratings_by_region_id = {
        row.region.region_id: row
        for row in RegionDayRating.objects.filter(
            region__in=regions, date=target_date
        ).select_related("region")
    }

    region_entries: list[dict[str, Any]] = []
    for region in regions:
        rating = ratings_by_region_id.get(region.region_id)
        if rating is None:
            region_entries.append(
                {
                    "region_id": region.region_id,
                    "name": region.name,
                    "danger_level": None,
                    "min_rating": None,
                    "has_bulletin": False,
                }
            )
        else:
            region_entries.append(
                {
                    "region_id": region.region_id,
                    "name": region.name,
                    "danger_level": rating.max_rating,
                    "min_rating": rating.min_rating,
                    "has_bulletin": True,
                }
            )

    count_with_bulletin = sum(1 for entry in region_entries if entry["has_bulletin"])
    scope = {
        "country": country.strip().upper() if country else None,
        "major_region_id": major_region_id.strip() if major_region_id else None,
    }
    return {
        "scope": scope,
        "date": target_date.isoformat(),
        "regions": region_entries,
        "count": len(region_entries),
        "count_with_bulletin": count_with_bulletin,
        "summary": _regional_snapshot_summary(
            _scope_label(scope["country"], scope["major_region_id"]),
            target_date,
            region_entries,
            count_with_bulletin,
        ),
    }


def _regional_snapshot_summary(
    scope_label: str,
    target_date: dt.date,
    region_entries: list[dict[str, Any]],
    count_with_bulletin: int,
) -> str:
    """Return a one-line, LLM-quotable summary of a get_regional_snapshot result."""
    parts = [
        f"{scope_label} on {target_date.isoformat()}: {count_with_bulletin}/"
        f"{len(region_entries)} region(s) with a bulletin."
    ]
    peak_rating, peak_count = _peak_rating_population(region_entries)
    if peak_rating is not None:
        parts.append(
            f"Peak rating {peak_rating.replace('_', ' ')} ({peak_count} region(s))."
        )
    return " ".join(parts)


def _handle_get_regional_snapshot(arguments: dict[str, Any]) -> dict[str, Any]:
    """Adapt the ``get_regional_snapshot`` JSON-RPC arguments to the tool function."""
    country = arguments.get("country")
    if country is not None and not isinstance(country, str):
        raise ToolError("'country' must be a string when supplied.")
    major_region_id = arguments.get("major_region_id")
    if major_region_id is not None and not isinstance(major_region_id, str):
        raise ToolError("'major_region_id' must be a string when supplied.")
    parsed_date = _optional_iso_date(arguments, "date")
    return get_regional_snapshot(country, major_region_id, parsed_date)


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
    "get_bulletin_metadata": ToolSpec(
        name="get_bulletin_metadata",
        description=(
            "Return provenance metadata for the bulletin covering one region "
            "on one day: issued_at, valid_from/to, next_update_expected, "
            "source_provider (slf/albina/meteofrance), source_url for "
            "citation, and language variants. Call this when you need to "
            "quote the bulletin's freshness or attribute the source."
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
        handler=_handle_get_bulletin_metadata,
    ),
    "get_bulletin_raw": ToolSpec(
        name="get_bulletin_raw",
        description=(
            "Escape hatch: return the full CAAML v6 payload (wrapped in a "
            "GeoJSON Feature envelope) for one region on one day. Use when "
            "the flattened tools have dropped a field you need — payload "
            "is ~10-30 KB per bulletin, so prefer get_current_conditions "
            "or get_avalanche_problems for the common questions."
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
        handler=_handle_get_bulletin_raw,
    ),
    "bulk_current_conditions": ToolSpec(
        name="bulk_current_conditions",
        description=(
            "Batched fan-out over get_current_conditions. Prevents 'show me "
            "Valais today' from turning into 20+ sequential MCP round-trips. "
            "Capped at 20 region_ids per call; unknown region_ids come back "
            "per-item as {has_bulletin: false, error: 'unknown_region'} "
            "rather than failing the whole batch."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "region_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Up to 20 region_ids, e.g. ['CH-4115', 'CH-4114']."
                    ),
                    "minItems": 1,
                    "maxItems": _BULK_CURRENT_CONDITIONS_CAP,
                },
                "date": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD. Defaults to today.",
                },
            },
            "required": ["region_ids"],
        },
        handler=_handle_bulk_current_conditions,
    ),
    "find_regions_near": ToolSpec(
        name="find_regions_near",
        description=(
            "Reverse geolocation: given (lat, lon, radius_km) return the "
            "covered avalanche-warning regions inside the radius, nearest "
            "first. Use when you have coordinates from a mapping app but "
            "don't know the SLF region slug — search_regions covers the "
            "opposite direction (place name → region). Radius is capped "
            "at 100 km per call."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "lat": {
                    "type": "number",
                    "description": "Latitude in degrees, [-90, 90].",
                    "minimum": -90,
                    "maximum": 90,
                },
                "lon": {
                    "type": "number",
                    "description": "Longitude in degrees, [-180, 180].",
                    "minimum": -180,
                    "maximum": 180,
                },
                "radius_km": {
                    "type": "number",
                    "description": ("Radius bound in kilometres. Capped at 100."),
                    "exclusiveMinimum": 0,
                    "maximum": _FIND_REGIONS_NEAR_RADIUS_CAP_KM,
                },
                "limit": {
                    "type": "integer",
                    "description": ("Maximum results (nearest first). Default 10."),
                    "minimum": 1,
                },
            },
            "required": ["lat", "lon", "radius_km"],
        },
        handler=_handle_find_regions_near,
    ),
    "get_danger_trend": ToolSpec(
        name="get_danger_trend",
        description=(
            "Return the danger rating series for one region over the "
            "trailing N days plus a computed trend layer: direction "
            "(rising / stable / falling), most recent change_point, and "
            "current_streak (trailing days at the current peak rating). "
            "Answers 'is danger rising or falling in Verbier?' in one "
            "call. Default window 14 days; cap 90 days."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "region_id": {
                    "type": "string",
                    "description": "Exact region_id, e.g. 'CH-4115'.",
                },
                "days": {
                    "type": "integer",
                    "description": (
                        "Trailing window length in days. Default 14, cap 90."
                    ),
                    "minimum": 1,
                    "maximum": _TREND_MAX_DAYS,
                },
            },
            "required": ["region_id"],
        },
        handler=_handle_get_danger_trend,
    ),
    "get_regional_snapshot": ToolSpec(
        name="get_regional_snapshot",
        description=(
            "Return a per-region peak-danger snapshot for every region in a "
            "country or major-region scope, plus a scope-level summary — "
            "answers 'where should I go?' in one call instead of an N-region "
            "fan-out over get_current_conditions. Exactly one of 'country' "
            "or 'major_region_id' is required. Sourced from RegionDayRating "
            "(the same peak-rating table get_danger_history, "
            "get_danger_trend, and the map choropleth read from), not a "
            "per-region bulletin scan."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "country": {
                    "type": "string",
                    "description": (
                        "ISO-3166-1 alpha-2 country code, e.g. 'CH'. Mutually "
                        "exclusive with major_region_id."
                    ),
                },
                "major_region_id": {
                    "type": "string",
                    "description": (
                        "A MajorRegion prefix, e.g. 'CH-4'. Mutually "
                        "exclusive with country."
                    ),
                },
                "date": {
                    "type": "string",
                    "description": "ISO date YYYY-MM-DD. Defaults to today.",
                },
            },
        },
        handler=_handle_get_regional_snapshot,
    ),
}
