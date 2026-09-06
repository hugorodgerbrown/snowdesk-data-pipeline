"""
tests/public/test_glossary.py — Tests for the EAWS glossary loader and matcher.

Covers the shape of ``apps/public/eaws_glossary.yaml`` (every entry usable,
every synonym unambiguous) and the compiled matcher's ordering, which is what
makes "melt-freeze crust" win over "crust".

The data assertions are deliberately structural rather than a fixed inventory
of terms: the file is expected to grow, and a test that pins its contents
would fail for the wrong reason. The one thing that must never happen is a
synonym owned by two entries — that is a silent last-one-wins that shows the
reader the wrong definition.
"""

from __future__ import annotations

import collections
import re

from apps.public.glossary import (
    glossary_matcher,
    load_glossary,
    synonym_alternatives,
    synonym_index,
)


class TestLoadGlossary:
    """The loader returns a usable entry for every term in the YAML."""

    def test_returns_a_non_empty_mapping(self) -> None:
        """The glossary loads and is not empty."""
        glossary = load_glossary()
        assert isinstance(glossary, dict)
        assert glossary

    def test_every_entry_has_the_three_keys(self) -> None:
        """Each entry carries exactly ``anchor``, ``synonyms`` and ``text``."""
        for key, entry in load_glossary().items():
            assert set(entry) == {"anchor", "synonyms", "text"}, key

    def test_every_entry_has_non_empty_definition_text(self) -> None:
        """An entry with no definition would render an empty popover."""
        for key, entry in load_glossary().items():
            assert entry["text"].strip(), key

    def test_every_entry_has_non_empty_anchor(self) -> None:
        """The anchor is the link back to the EAWS source and is required."""
        for key, entry in load_glossary().items():
            assert entry["anchor"].strip(), key

    def test_every_entry_has_at_least_one_synonym(self) -> None:
        """A term with no surface form could never be matched in prose."""
        for key, entry in load_glossary().items():
            assert entry["synonyms"], key

    def test_synonyms_are_lower_case(self) -> None:
        """Matching lower-cases the matched text before lookup, so keys must be too."""
        for key, entry in load_glossary().items():
            for synonym in entry["synonyms"]:
                assert synonym == synonym.lower(), f"{key}: {synonym!r}"

    def test_known_entry_round_trips(self) -> None:
        """The ticket's motivating term loads with its EAWS wording intact."""
        entry = load_glossary()["avalanche_prone_location"]
        assert entry["anchor"] == "avalanche-prone-location-danger-zone"
        assert "danger zone" in entry["synonyms"]
        assert entry["text"].startswith("Locations delineated by aspect or altitude")

    def test_result_is_cached(self) -> None:
        """The loader is cached — it is read on every prose block of every render."""
        assert load_glossary() is load_glossary()


class TestSynonymUniqueness:
    """No surface form may belong to two glossary entries."""

    def test_no_synonym_is_owned_by_two_terms(self) -> None:
        """
        A duplicated synonym is a data bug, not a preference.

        ``synonym_index`` is a dict, so a duplicate would silently resolve to
        whichever entry happened to be parsed last and the reader would be
        shown the wrong definition. Count the raw pairs instead.
        """
        counts = collections.Counter(
            synonym
            for entry in load_glossary().values()
            for synonym in entry["synonyms"]
        )
        duplicates = {word: n for word, n in counts.items() if n > 1}
        assert duplicates == {}

    def test_index_covers_every_synonym(self) -> None:
        """Every declared synonym reaches the index."""
        declared = {
            synonym
            for entry in load_glossary().values()
            for synonym in entry["synonyms"]
        }
        assert set(synonym_index()) == declared

    def test_index_maps_synonym_to_its_own_term(self) -> None:
        """A synonym resolves to the entry that declared it."""
        assert synonym_index()["danger zone"] == "avalanche_prone_location"
        assert synonym_index()["melt-freeze crust"] == "melt_freeze_crust"


class TestGlossaryMatcher:
    """The compiled alternation is ordered longest-first and word-anchored."""

    def test_alternatives_are_ordered_longest_first(self) -> None:
        """Lengths never increase as the alternation is walked."""
        lengths = [len(alternative) for alternative in synonym_alternatives()]
        assert lengths == sorted(lengths, reverse=True)

    def test_compiled_pattern_preserves_that_order(self) -> None:
        """The pattern itself — not just the helper — is longest-first."""
        pattern, _ = glossary_matcher()
        expected = "|".join(
            re.escape(alternative) for alternative in synonym_alternatives()
        )
        assert pattern.pattern == rf"\b(?:{expected})\b"

    def test_longer_synonym_wins_over_its_substring(self) -> None:
        """The whole "melt-freeze crust" matches, rather than only its "crust"."""
        pattern, lookup = glossary_matcher()
        match = pattern.search("a thick melt-freeze crust on solar aspects")
        assert match is not None
        assert lookup[match.group(0).lower()] == "melt_freeze_crust"

    def test_matching_is_case_insensitive(self) -> None:
        """Prose capitalises terms at the start of a sentence."""
        pattern, lookup = glossary_matcher()
        match = pattern.search("Weak layers persist.")
        assert match is not None
        assert lookup[match.group(0).lower()] == "weak_layer"

    def test_word_boundaries_prevent_substring_matches(self) -> None:
        """A term must not match inside a longer word — "crust" in "encrusted"."""
        pattern, _ = glossary_matcher()
        assert pattern.search("encrusted rime on the ridge") is None

    def test_matcher_is_cached(self) -> None:
        """The pattern is compiled once, not per prose block."""
        assert glossary_matcher() is glossary_matcher()
