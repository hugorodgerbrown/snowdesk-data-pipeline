"""
mcp_server/resolvers.py — Region lookup and fuzzy place-name search.

Two entry points consumed by ``mcp_server.tools``:

* ``resolve_region(region_id)`` — exact ``MicroRegion.region_id`` lookup,
  reusing the same prefetch shape as the bulletin page so a hit is cheap
  to render further down the tool.
* ``search_places(query)`` — fuzzy name search across resorts and
  micro/major regions, for callers who only have a place name.

The candidate universe (~1500 rows across ``Resort``, ``MicroRegion``, and
``MajorRegion``) is cheap to hold in full and is cached in the Django
default cache, keyed on a fingerprint of the newest ``updated_at`` across
all three tables — the same pattern as
``bulletins.services.coverage.covered_region_ids``. Any edit to a region or
resort changes the fingerprint, which changes the cache key, which is a
guaranteed miss — no explicit invalidation needed.
"""

from __future__ import annotations

from typing import Any

from django.core.cache import cache
from django.db.models import Max
from django.http import Http404
from rapidfuzz import fuzz, process

from mcp_server.normalise import normalise
from public.views import _resolve_region_for_bulletin
from regions.models import MajorRegion, MicroRegion, Resort

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

    Thin wrapper over ``public.views._resolve_region_for_bulletin`` — reuses
    its ``select_related("subregion__major")`` prefetch (the tools need the
    parent major region's name) — but returns ``None`` instead of raising
    ``Http404`` so the tool layer can turn "unknown region" into a JSON-RPC
    tool error rather than a bare 404.

    Args:
        region_id: An SLF-style region identifier, e.g. ``"CH-4115"``.
            Matched case-insensitively.

    Returns:
        The MicroRegion, or ``None`` if no region has that id.

    """
    try:
        return _resolve_region_for_bulletin(region_id)
    except Http404:
        return None


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


def _major_region_representatives() -> dict[int, str]:
    """Map each MajorRegion's pk to one representative MicroRegion.region_id.

    A MajorRegion aggregates many MicroRegions and has no bulletin page of
    its own, so a name match against it (e.g. "Wallis" -> Valais) still
    needs to resolve to a single ``region_id`` the other three tools can
    take. The alphabetically-first child MicroRegion is used as a
    best-effort representative — a known v1 limitation for major regions
    with many children; see docs/mcp-server.md.

    Returns:
        A dict of ``{major_region_pk: region_id}``, one entry per
        MajorRegion that has at least one MicroRegion child.

    """
    representatives: dict[int, str] = {}
    rows = MicroRegion.objects.order_by("region_id").values_list(
        "region_id", "subregion__major_id"
    )
    for region_id, major_pk in rows:
        representatives.setdefault(major_pk, region_id)
    return representatives


def _build_candidate_pool() -> list[dict[str, Any]]:
    """Build the full search-candidate pool from the database.

    One row per (Resort / MicroRegion / MajorRegion) x distinct human
    name. Each row carries the ``region_id`` a matching tool call should
    use, a display ``name``, a ``kind`` (``"micro"``, ``"major"``, or
    ``"resort"``), a human-readable ``parent`` for context, and the
    pre-computed ``normalised`` search key.

    Returns:
        The full candidate-pool list — not cached here; caching is
        ``_candidate_pool``'s responsibility.

    """
    rows: list[dict[str, Any]] = []

    for region in MicroRegion.objects.select_related("subregion__major"):
        major = region.subregion.major
        parent = major.name_en or major.name_native
        rows.append(
            {
                "region_id": region.region_id,
                "name": region.name,
                "kind": "micro",
                "parent": parent,
                "normalised": normalise(region.name),
            }
        )

    major_reps = _major_region_representatives()
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

    return rows


def _pool_cache_key() -> str:
    """Return a cache key that changes whenever the candidate data changes.

    Fingerprints the pool on the newest ``updated_at`` across
    ``MicroRegion``, ``MajorRegion``, and ``Resort`` — any create, edit, or
    delete touches one of those timestamps, which changes the fingerprint,
    which guarantees the next call is a cache miss. No explicit
    invalidation call site is needed anywhere else in the codebase.

    Returns:
        A cache key string unique to the current state of the three
        source tables.

    """
    stamps = [
        MicroRegion.objects.aggregate(latest=Max("updated_at"))["latest"],
        MajorRegion.objects.aggregate(latest=Max("updated_at"))["latest"],
        Resort.objects.aggregate(latest=Max("updated_at"))["latest"],
    ]
    known = [s for s in stamps if s is not None]
    fingerprint = max(known).isoformat() if known else "empty"
    return f"mcp:candidates:{fingerprint}"


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
