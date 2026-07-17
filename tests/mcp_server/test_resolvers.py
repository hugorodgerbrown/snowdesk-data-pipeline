"""
tests/mcp_server/test_resolvers.py — Tests for mcp_server.resolvers.

Covers ``resolve_region`` (exact ``region_id`` lookup) and
``search_places`` (fuzzy name search), including the alpine-name
misspelling table from the SNOW-391 implementation plan.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

from mcp_server import resolvers
from tests.factories import (
    MajorRegionFactory,
    MicroRegionFactory,
    ResortFactory,
    SubRegionFactory,
)


@pytest.fixture(autouse=True)
def clear_mcp_candidate_cache() -> None:
    """Ensure the candidate-pool cache is clear before every test.

    The cache key is fingerprinted on ``max(updated_at)`` across the three
    source tables (see ``resolvers._pool_cache_key``), but two tests
    creating fixtures in quick succession could theoretically produce
    identical microsecond-resolution timestamps — clearing the cache
    removes any chance of one test's pool leaking into another's.
    """
    cache.clear()


@pytest.fixture
def alpine_fixture() -> dict[str, object]:
    """Build the alpine-name misspelling fixture set from the SNOW-391 plan.

    Returns:
        A dict of the created MicroRegion rows, keyed by short mnemonic
        names, for tests that need to assert against a specific
        ``region_id``.

    """
    valais_major = MajorRegionFactory.create(
        prefix="CH-4", country="CH", name_en="Valais", name_native="Wallis"
    )
    valais_sub = SubRegionFactory.create(prefix="CH-41", major=valais_major)
    bas_valais = MicroRegionFactory.create(
        region_id="CH-4115", name="Bas-Valais", subregion=valais_sub
    )
    ResortFactory.create(name="Val d'Isère", region=bas_valais)
    ResortFactory.create(name="Verbier", region=bas_valais)

    zermatt_major = MajorRegionFactory.create(prefix="CH-5", country="CH")
    zermatt_sub = SubRegionFactory.create(prefix="CH-52", major=zermatt_major)
    zermatt_region = MicroRegionFactory.create(
        region_id="CH-5251", name="Zermatt", subregion=zermatt_sub
    )
    ResortFactory.create(name="Zermatt", region=zermatt_region)

    at_major = MajorRegionFactory.create(prefix="AT-07", country="AT")
    at_sub = SubRegionFactory.create(prefix="AT-07-19", major=at_major)
    st_anton_region = MicroRegionFactory.create(region_id="AT-07-19", subregion=at_sub)
    ResortFactory.create(name="St. Anton am Arlberg", region=st_anton_region)

    fr_major = MajorRegionFactory.create(prefix="FR-1", country="FR")
    fr_sub = SubRegionFactory.create(prefix="FR-16", major=fr_major)
    val_thorens_region = MicroRegionFactory.create(region_id="FR-16", subregion=fr_sub)
    ResortFactory.create(name="Val Thorens", region=val_thorens_region)

    zurs_major = MajorRegionFactory.create(
        prefix="AT-02", country="AT", name_native="Zürs am Arlberg", name_en=""
    )
    zurs_sub = SubRegionFactory.create(prefix="AT-02-01", major=zurs_major)
    zurs_region = MicroRegionFactory.create(region_id="AT-02-01", subregion=zurs_sub)

    return {
        "bas_valais": bas_valais,
        "zermatt": zermatt_region,
        "st_anton": st_anton_region,
        "val_thorens": val_thorens_region,
        "zurs": zurs_region,
    }


# ---------------------------------------------------------------------------
# resolve_region
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_resolve_region_returns_the_micro_region(alpine_fixture: dict) -> None:
    """resolve_region returns the MicroRegion for a known region_id."""
    result = resolvers.resolve_region("CH-4115")
    assert result is not None
    assert result.pk == alpine_fixture["bas_valais"].pk


@pytest.mark.django_db
def test_resolve_region_is_case_insensitive(alpine_fixture: dict) -> None:
    """resolve_region matches region_id case-insensitively."""
    result = resolvers.resolve_region("ch-4115")
    assert result is not None
    assert result.pk == alpine_fixture["bas_valais"].pk


@pytest.mark.django_db
def test_resolve_region_returns_none_for_unknown_id() -> None:
    """resolve_region returns None rather than raising for an unknown id."""
    assert resolvers.resolve_region("XX-9999") is None


# ---------------------------------------------------------------------------
# search_places — basics
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_places_exact_region_id_short_circuit(alpine_fixture: dict) -> None:
    """An exact region_id query short-circuits straight to that region."""
    results = resolvers.search_places("CH-4115")
    assert len(results) == 1
    assert results[0]["region_id"] == "CH-4115"
    assert results[0]["kind"] == "micro"
    assert results[0]["score"] == 100.0


@pytest.mark.django_db
def test_search_places_no_match_returns_empty_list(alpine_fixture: dict) -> None:
    """A query with no plausible match returns an empty list, not an error."""
    assert resolvers.search_places("Aspen") == []


@pytest.mark.django_db
def test_search_places_blank_query_returns_empty_list(alpine_fixture: dict) -> None:
    """A blank query short-circuits to an empty list without hitting the DB."""
    assert resolvers.search_places("   ") == []


@pytest.mark.django_db
def test_search_places_results_are_score_ordered(alpine_fixture: dict) -> None:
    """Results are returned in descending score order."""
    results = resolvers.search_places("Val")
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.django_db
def test_search_places_ambiguous_short_query_returns_multiple(
    alpine_fixture: dict,
) -> None:
    """A short, ambiguous query returns an acceptable multi-candidate list."""
    results = resolvers.search_places("Val")
    assert 1 < len(results) <= 10


@pytest.mark.django_db
def test_search_places_reflects_a_newly_added_resort(alpine_fixture: dict) -> None:
    """The cached candidate pool picks up a resort added after the first search.

    Exercises the fingerprinted cache-key invalidation strategy in
    ``resolvers._pool_cache_key`` — a plain TTL-only cache would still
    serve the stale pool here.
    """
    assert resolvers.search_places("Sinnladen") == []
    ResortFactory.create(name="Sinnladen", region=alpine_fixture["bas_valais"])
    results = resolvers.search_places("Sinnladen")
    assert results
    assert results[0]["region_id"] == alpine_fixture["bas_valais"].region_id


@pytest.mark.django_db
def test_search_places_ties_broken_by_kind_priority() -> None:
    """Equal-scoring hits rank micro region ahead of resort."""
    major = MajorRegionFactory.create(prefix="CH-9", country="CH")
    sub = SubRegionFactory.create(prefix="CH-91", major=major)
    micro_hit = MicroRegionFactory.create(region_id="CH-9101", name="Alpha")
    resort_region = MicroRegionFactory.create(region_id="CH-9102", subregion=sub)
    ResortFactory.create(name="Alpha", region=resort_region)

    results = resolvers.search_places("Alpha")
    assert results[0]["kind"] == "micro"
    assert results[0]["region_id"] == micro_hit.region_id


# ---------------------------------------------------------------------------
# search_places — alpine-name misspelling table (SNOW-391 plan)
# ---------------------------------------------------------------------------

# Each row: (query, fixture_key_for_expected_region, pytest id, rationale).
_MISSPELLING_CASES = [
    ("Val d'Isere", "bas_valais", "accent-stripped", "ASCII-fold"),
    (
        "Val dIsere",
        "bas_valais",
        "accent-stripped-no-apostrophe",
        "apostrophe stripped",
    ),
    ("Zurs", "zurs", "umlaut-stripped", "NFKD"),
    ("Zürs", "zurs", "umlaut-supplied", "exact after normalise"),
    ("Wallis", "bas_valais", "german-name-variant", "name_native indexed"),
    ("Valais", "bas_valais", "english-name-variant", "name_en indexed"),
    ("VERBIER", "bas_valais", "case-upper", "lowercased"),
    ("verbier", "bas_valais", "case-lower-typo", "lowercased"),
    ("Verbie", "bas_valais", "single-missing-letter", "WRatio >= 70"),
    ("Zermat", "zermatt", "single-wrong-letter", "WRatio >= 70"),
    # Observed score ~76.9 (fuzz.WRatio("zremat", "zermatt")) at rapidfuzz
    # 3.14.5 — a future rapidfuzz version bump that shifts this below the
    # 70 cutoff should fail this test with a clear signal.
    ("Zremat", "zermatt", "transposed-letter", "WRatio ~76.9 observed"),
    ("St Anton", "st_anton", "st-vs-st-period", "punctuation stripped"),
    ("Sankt Anton", "st_anton", "sankt-variant", "sankt -> saint token"),
    ("Val-Thorens", "val_thorens", "hyphen-vs-space", "hyphen -> space"),
    ("Val  Thorens", "val_thorens", "collapsed-whitespace", "whitespace collapsed"),
    # Observed score 90.0 (fuzz.WRatio("verbier ski resort", "verbier")) —
    # a partial/token-ratio match against the plain resort name.
    (
        "Verbier ski resort",
        "bas_valais",
        "noise-word",
        "WRatio partial-token score ~90.0 observed",
    ),
]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("query", "expected_fixture_key"),
    [(c[0], c[1]) for c in _MISSPELLING_CASES],
    ids=[c[2] for c in _MISSPELLING_CASES],
)
def test_search_places_misspelling_table(
    alpine_fixture: dict, query: str, expected_fixture_key: str
) -> None:
    """The top hit for every misspelling-table query is the expected region."""
    expected_region_id = alpine_fixture[expected_fixture_key].region_id
    results = resolvers.search_places(query)
    assert results, f"expected at least one hit for {query!r}"
    assert results[0]["region_id"] == expected_region_id
