"""
mcp_server/tools.py — MCP tool registry and handlers.

Four read-only tools, composed from services that already exist elsewhere
in the codebase (``mcp_server.resolvers``, ``public.views``,
``RegionDayRating.objects.for_region_range``, ``Bulletin.render_model``):

* ``search_regions`` — fuzzy place-name search (``resolvers.search_places``).
* ``get_current_conditions`` — danger rating + forecaster prose for one
  region on one day.
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

from bulletins.models import Bulletin, RegionDayRating
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
}
