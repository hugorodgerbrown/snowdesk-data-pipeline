"""
apps/mcp_server/resolvers.py — Region lookup and fuzzy place-name search.

Entry points consumed by ``apps.mcp_server.tools``:

* ``resolve_region(region_id)`` — exact ``MicroRegion.region_id`` lookup,
  scoped to what the MCP tools need (the parent major region's name via
  ``select_related("subregion__major")``); deliberately lighter than the
  bulletin page's own lookup, which also prefetches ``neighbours`` for a
  UI panel no tool reads.
* ``search_places(query)`` — fuzzy name search across resorts and
  micro/major regions, for callers who only have a place name.
* ``list_regions(country, provider)`` — every ``MicroRegion``, optionally
  filtered by country and/or provider (ANDed); both filter arguments are
  expected already validated and normalised by the caller
  (``apps.mcp_server.tools.list_regions``).
* ``regions_for_scope(country, major_region_id)`` — the MicroRegions
  covered by a country or a major-region prefix, for
  ``get_regional_snapshot``'s "where should I go?" fan-out.

The candidate universe (~1500 rows across ``Resort``, ``MicroRegion``,
``MajorRegion``, and ``RegionAlias``) is cheap to hold in full and is
cached in the Django default cache, keyed on a fingerprint of the newest
``updated_at`` **and the row count** across all four tables — the same
pattern as ``apps.bulletins.services.coverage.covered_region_ids``, plus the
counts SNOW-552 added. Any create, edit, or delete of a region, resort,
or alias changes the fingerprint, which changes the cache key, which is
a guaranteed miss — no explicit invalidation needed. (The count is what
covers deletes: removing a row that is not the newest leaves the max
timestamp untouched.)
"""

from __future__ import annotations

import math
from typing import Any

from django.core.cache import cache
from django.db.models import Count, Max, QuerySet
from rapidfuzz import fuzz, process

from apps.mcp_server.normalise import normalise
from apps.public.api import COUNTRY_NAMES
from apps.regions.models import MajorRegion, MicroRegion, RegionAlias, Resort

# ISO-3166-1 alpha-2 codes Snowdesk has bulletin coverage for. Re-exported
# from ``apps.public.api.COUNTRY_NAMES`` (rather than a second, drifting list)
# so ``regions_for_scope`` validates a ``country`` scope against the same
# set the map's ``?country=`` filter and region tooltip already use.
VALID_COUNTRIES: frozenset[str] = frozenset(COUNTRY_NAMES)

# Country → provider mapping for the ``find_places_near`` result shape.
# Static because Snowdesk fetches from exactly one provider per country;
# the region's own country label is authoritative regardless of whether a
# bulletin exists on any given day. Values are ``Bulletin.Source`` values
# (SNOW-582 upper-cased them) — ``tools.py`` round-trips them through
# ``Bulletin.Source(value)`` and ``_ISSUE_SCHEDULE`` lookups, both of which
# require an exact match against the enum's current values.
_PROVIDER_BY_COUNTRY: dict[str, str] = {
    "CH": "SLF",
    "AT": "ALBINA",
    "IT": "ALBINA",
    "FR": "METEOFRANCE",
}

# Approximate mean earth radius in kilometres — good to five significant
# figures at the mid-latitudes Snowdesk covers, which is well below the
# accuracy floor of MicroRegion.centre (a single lon/lat pair per region,
# so distance to any specific point is already a coarse estimate).
_EARTH_RADIUS_KM = 6371.0088

# How long a built candidate pool stays in cache once computed. The cache
# *key* already changes whenever underlying data changes (see
# ``_pool_cache_key``), so this TTL is purely a memory-hygiene bound, not
# a correctness one.
_POOL_TTL = 3600

# Score threshold below which a fuzzy match is discarded as noise.
_SCORE_CUTOFF = 70

# Tie-break order when two candidates score identically: a bulletin
# region is more directly useful to the other three tools than a broader
# major region, which is in turn more useful than a single resort.
_KIND_PRIORITY = {"micro": 0, "major": 1, "resort": 2}


def resolve_region(region_id: str) -> MicroRegion | None:
    """Look up a MicroRegion by ``region_id``, or ``None`` if unknown.

    ``select_related("subregion__major")`` is all the MCP tools need (the
    parent major region's name); deliberately doesn't prefetch
    ``neighbours`` — that's only used by the bulletin page's "Adjoining
    regions" panel, which no MCP tool reads, and at the endpoint's 60/min
    rate-limit ceiling a redundant prefetch is a real query cost.

    Args:
        region_id: An SLF-style region identifier, e.g. ``"CH-4115"``.
            Matched case-insensitively.

    Returns:
        The MicroRegion, or ``None`` if no region has that id.

    """
    return (
        MicroRegion.objects.select_related("subregion__major")
        .filter(region_id__iexact=region_id)
        .first()
    )


def list_regions(
    country: str | None = None, provider: str | None = None
) -> QuerySet[MicroRegion]:
    """Return MicroRegions filtered by country and/or provider, both ANDed.

    Args:
        country: An already-normalised ISO-3166-1 alpha-2 country code
            (e.g. ``"CH"``), or ``None`` for no country filter.
        provider: An already-normalised ``Bulletin.Source`` value (e.g.
            ``"SLF"``), or ``None`` for no provider filter — resolved to
            the set of countries that provider serves via
            ``_PROVIDER_BY_COUNTRY``.

    Returns:
        A ``MicroRegion`` queryset with ``select_related("subregion__major")``,
        in the model's default ``region_id`` order. Neither argument is
        validated here — the caller (``apps.mcp_server.tools.list_regions``)
        validates and normalises before calling.

    """
    queryset = MicroRegion.objects.select_related("subregion__major")
    if country is not None:
        queryset = queryset.filter(subregion__major__country=country)
    if provider is not None:
        countries = [c for c, p in _PROVIDER_BY_COUNTRY.items() if p == provider]
        queryset = queryset.filter(subregion__major__country__in=countries)
    return queryset


def regions_for_scope(
    country: str | None, major_region_id: str | None
) -> list[MicroRegion]:
    """Return the MicroRegions in scope for a country or a major-region prefix.

    Backs ``get_regional_snapshot`` — the "where should I go?" tool that
    answers a country- or major-region-wide danger question in a single
    call rather than an N-region fan-out. Exactly one of ``country`` or
    ``major_region_id`` must be supplied.

    ``display_on_map=False`` major regions are included — this answers a
    coverage question, not a map-visibility one.

    Args:
        country: ISO-3166-1 alpha-2 country code, e.g. ``"CH"``. Matched
            case-insensitively against :data:`VALID_COUNTRIES`.
        major_region_id: A ``MajorRegion.prefix``, e.g. ``"CH-4"``.
            Matched case-insensitively.

    Returns:
        The matching MicroRegions, ``select_related("subregion__major")``
        so callers can read ``.subregion.major`` (country, name) without a
        query per region, in the model's default ``region_id`` ordering.

    Raises:
        ValueError: neither or both of ``country`` / ``major_region_id``
            are supplied, ``country`` isn't a recognised code, or
            ``major_region_id`` doesn't match any known ``MajorRegion``.

    """
    if bool(country) == bool(major_region_id):
        raise ValueError("Exactly one of 'country' or 'major_region_id' is required.")

    queryset = MicroRegion.objects.select_related("subregion__major")

    if country is not None:
        normalised_country = country.strip().upper()
        if normalised_country not in VALID_COUNTRIES:
            raise ValueError(f"Unknown country: {country!r}.")
        return list(queryset.filter(subregion__major__country=normalised_country))

    normalised_prefix = (major_region_id or "").strip()
    matches = list(queryset.filter(subregion__major__prefix__iexact=normalised_prefix))
    if (
        not matches
        and not MajorRegion.objects.filter(prefix__iexact=normalised_prefix).exists()
    ):
        raise ValueError(f"Unknown major_region_id: {major_region_id!r}.")
    return matches


def _distinct_names(*names: str) -> list[str]:
    """Return the given names, blank entries dropped and duplicates removed.

    Preserves first-seen order. Used to build one candidate-pool row per
    *distinct* human name a region or resort carries (e.g. a MajorRegion's
    ``name_en`` and ``name_native`` are usually different and both worth
    indexing; a Resort's blank ``name_alt`` contributes nothing).

    Args:
        *names: Candidate name strings, some of which may be blank.

    Returns:
        The distinct, non-blank names in first-seen order.

    """
    seen: set[str] = set()
    result: list[str] = []
    for raw in names:
        name = raw.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(name)
    return result


def _build_candidate_pool() -> list[dict[str, Any]]:
    """Build the full search-candidate pool from the database.

    One row per (Resort / MicroRegion / MajorRegion / RegionAlias) x
    distinct human name. Each row carries the ``region_id`` a matching
    tool call should use, a display ``name``, a ``kind`` (``"micro"``,
    ``"major"``, or ``"resort"``), a human-readable ``parent`` for
    context, and the pre-computed ``normalised`` search key.

    Each MajorRegion's representative ``region_id`` (see
    ``_build_candidate_pool``'s major-region loop below) is derived from
    the same ``MicroRegion`` rows loaded for the micro-region loop, rather
    than a second query — ``MicroRegion.Meta.ordering = ["region_id"]``
    means the loop already visits rows alphabetically, so recording the
    first ``region_id`` seen per major-region pk reproduces the
    "alphabetically-first child" representative with no extra table scan.

    The ``RegionAlias`` loop runs last, after the ``MicroRegion`` loop —
    ``_match_exact_region_id`` returns the *first* pool row whose
    ``kind == "micro"`` matches a queried ``region_id``, so an alias row
    appended before its region's canonical ``MicroRegion`` row would win
    an exact-id lookup instead of the canonical name. Each alias row uses
    ``kind="micro"`` (not a fourth kind) so it dedups against — and, on an
    exact-id query, loses to — its region's canonical row.

    Returns:
        The full candidate-pool list — not cached here; caching is
        ``_candidate_pool``'s responsibility.

    """
    rows: list[dict[str, Any]] = []
    major_reps: dict[int, str] = {}

    for region in MicroRegion.objects.select_related("subregion__major"):
        major = region.subregion.major
        parent = major.name_en or major.name_native
        major_reps.setdefault(major.pk, region.region_id)
        rows.append(
            {
                "region_id": region.region_id,
                "name": region.name,
                "kind": "micro",
                "parent": parent,
                "normalised": normalise(region.name),
            }
        )

    for major_region in MajorRegion.objects.all():
        region_id = major_reps.get(major_region.pk)
        if region_id is None:
            continue  # No MicroRegion children — nothing to resolve to.
        for name in _distinct_names(major_region.name_en, major_region.name_native):
            rows.append(
                {
                    "region_id": region_id,
                    "name": name,
                    "kind": "major",
                    "parent": major_region.country,
                    "normalised": normalise(name),
                }
            )

    for resort in Resort.objects.select_related("region"):
        for name in _distinct_names(resort.name, resort.name_alt):
            rows.append(
                {
                    "region_id": resort.region.region_id,
                    "name": name,
                    "kind": "resort",
                    "parent": resort.region.name,
                    "normalised": normalise(name),
                }
            )

    for alias in RegionAlias.objects.select_related("region__subregion__major"):
        major = alias.region.subregion.major
        parent = major.name_en or major.name_native
        rows.append(
            {
                "region_id": alias.region.region_id,
                "name": alias.alias_text,
                "kind": "micro",
                "parent": parent,
                "normalised": normalise(alias.alias_text),
            }
        )

    return rows


def _pool_cache_key() -> str:
    """Return a cache key that changes whenever the candidate data changes.

    Fingerprints the pool on two things per source table — ``MicroRegion``,
    ``MajorRegion``, ``Resort``, and ``RegionAlias``:

    * the newest ``updated_at``, which a create or an edit moves forward;
    * the row count, which a **delete** changes and a timestamp does not
      (SNOW-552). Deleting a row whose ``updated_at`` was not the maximum
      left the aggregate — and so the cache key — identical, and the stale
      pre-deletion pool kept being served for up to ``_POOL_TTL``: an
      obsolete alias went on appearing in search results for an hour after
      an administrator removed it.

    Folding delete-sensitivity into the fingerprint keeps the "no explicit
    invalidation call site" property. The alternative — bumping a cache
    version from create/update/delete signals — would need a call site in
    ``import_resorts`` and in every admin delete path, and conflicts with
    ``docs/decisions/no-signals-for-side-effects.md``.

    Returns:
        A cache key string unique to the current state of the four
        source tables.

    """
    querysets = (
        MicroRegion.objects,
        MajorRegion.objects,
        Resort.objects,
        RegionAlias.objects,
    )
    parts: list[str] = []
    for manager in querysets:
        summary = manager.aggregate(latest=Max("updated_at"), rows=Count("id"))
        latest = summary["latest"]
        parts.append(f"{latest.isoformat() if latest else 'empty'}:{summary['rows']}")
    return f"mcp:candidates:{'|'.join(parts)}"


def _candidate_pool() -> list[dict[str, Any]]:
    """Return the (possibly cached) full search-candidate pool.

    Returns:
        The candidate-pool list — see :func:`_build_candidate_pool` for
        the row shape.

    """
    return cache.get_or_set(  # type: ignore[return-value]
        _pool_cache_key(), _build_candidate_pool, timeout=_POOL_TTL
    )


def _match_exact_region_id(
    query: str, pool: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Short-circuit fuzzy matching when the query is already a region_id.

    Args:
        query: The raw (not normalised) search query.
        pool: The candidate pool to search.

    Returns:
        A single-candidate result dict with ``score`` 100, or ``None`` if
        the query doesn't match any MicroRegion's ``region_id`` exactly
        (case-insensitively).

    """
    query_upper = query.strip().upper()
    for row in pool:
        if row["kind"] == "micro" and row["region_id"].upper() == query_upper:
            return {
                "region_id": row["region_id"],
                "name": row["name"],
                "kind": row["kind"],
                "parent": row["parent"],
                "score": 100.0,
            }
    return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in kilometres between two lat/lon pairs.

    Pure-Python haversine (no PostGIS, no Shapely) — mirrors the same
    dependency-free precedent the request-path point-in-polygon lookup
    established. Accuracy is ~0.5% at continental distances, far better
    than the ~10 km scale of a MicroRegion centroid.

    Args:
        lat1: Latitude of point one, in degrees.
        lon1: Longitude of point one, in degrees.
        lat2: Latitude of point two, in degrees.
        lon2: Longitude of point two, in degrees.

    Returns:
        The great-circle distance in kilometres.

    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def find_places_near(
    lat: float, lon: float, radius_km: float, limit: int = 10
) -> list[dict[str, Any]]:
    """Return covered MicroRegions inside a radius of one point, nearest first.

    Args:
        lat: Query latitude in degrees, in ``[-90, 90]``.
        lon: Query longitude in degrees, in ``[-180, 180]``.
        radius_km: Radius bound; only regions within this distance are
            returned. Callers are expected to enforce their own upper
            bound (see the ``apps.mcp_server.tools.find_regions_near`` cap).
        limit: Maximum results to return, nearest first.

    Returns:
        A list of ``{region_id, name, kind: "micro", distance_km,
        provider}`` dicts, nearest first. Empty when no MicroRegion has a
        ``centre`` within ``radius_km`` of the query point.

    """
    # Small enough (~1500 rows) to hold in memory and iterate — the same
    # rationale as ``_candidate_pool``. Load with the major-region join
    # for the country → provider derivation.
    regions = MicroRegion.objects.select_related("subregion__major").all()

    hits: list[tuple[float, dict[str, Any]]] = []
    for region in regions:
        centre = region.centre
        if not isinstance(centre, dict):
            continue
        centre_lat = centre.get("lat")
        centre_lon = centre.get("lon")
        if not isinstance(centre_lat, int | float) or not isinstance(
            centre_lon, int | float
        ):
            continue
        distance = _haversine_km(lat, lon, float(centre_lat), float(centre_lon))
        if distance > radius_km:
            continue
        provider = _PROVIDER_BY_COUNTRY.get(region.subregion.major.country)
        hits.append(
            (
                distance,
                {
                    "region_id": region.region_id,
                    "name": region.name,
                    "kind": "micro",
                    "distance_km": round(distance, 2),
                    "provider": provider,
                },
            )
        )

    hits.sort(key=lambda h: h[0])
    return [row for _distance, row in hits[:limit]]


def search_places(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Fuzzy-search resorts and regions by a (possibly misspelled) name.

    Args:
        query: A free-text place name, or an exact ``region_id`` (e.g.
            ``"CH-4115"``), which short-circuits straight to a single hit.
        limit: Maximum number of candidates to return.

    Returns:
        A list of ``{region_id, name, kind, parent, score}`` dicts, best
        match first. Ties are broken by kind priority (micro region >
        major region > resort). Empty when the query is blank or nothing
        scores at or above the match threshold.

    """
    query = query.strip()
    if not query:
        return []

    pool = _candidate_pool()

    exact = _match_exact_region_id(query, pool)
    if exact is not None:
        return [exact]

    query_norm = normalise(query)
    if not query_norm:
        return []

    choices = {i: row["normalised"] for i, row in enumerate(pool)}
    matches = process.extract(
        query_norm,
        choices,
        scorer=fuzz.WRatio,
        score_cutoff=_SCORE_CUTOFF,
        limit=limit,
    )
    ranked = sorted(matches, key=lambda m: (-m[1], _KIND_PRIORITY[pool[m[2]]["kind"]]))

    results: list[dict[str, Any]] = []
    seen_identities: set[tuple[str, str]] = set()
    for _matched_text, score, idx in ranked:
        row = pool[idx]
        identity = (row["kind"], row["region_id"])
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        results.append(
            {
                "region_id": row["region_id"],
                "name": row["name"],
                "kind": row["kind"],
                "parent": row["parent"],
                "score": round(score, 1),
            }
        )
    return results
