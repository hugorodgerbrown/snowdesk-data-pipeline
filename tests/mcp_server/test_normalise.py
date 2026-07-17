"""
tests/mcp_server/test_normalise.py — Unit tests for mcp_server.normalise.

Parametrised across the alpine-name misspelling table from the SNOW-391
implementation plan — every ``input -> expected`` case runs independently
so a regression names the exact string that broke.
"""

from __future__ import annotations

import pytest

from mcp_server.normalise import normalise

# Each row: (input, expected_normalised, pytest id).
_CASES = [
    ("Val d'Isère", "val disere", "accent-and-apostrophe-canonical-form"),
    ("Val d'Isere", "val disere", "accent-stripped"),
    ("Val dIsere", "val disere", "accent-stripped-no-apostrophe"),
    ("Zürs", "zurs", "umlaut-supplied"),
    ("Zurs", "zurs", "umlaut-absent-already-ascii"),
    ("Wallis", "wallis", "german-name-variant-passthrough"),
    ("Valais", "valais", "english-name-variant-passthrough"),
    ("VERBIER", "verbier", "uppercase"),
    ("verbier", "verbier", "lowercase-passthrough"),
    ("Verbie", "verbie", "missing-letter-passthrough"),
    ("Zermat", "zermat", "wrong-letter-passthrough"),
    ("Zremat", "zremat", "transposed-letter-passthrough"),
    ("St Anton", "saint anton", "st-no-period"),
    ("St. Anton", "saint anton", "st-with-period"),
    ("Sankt Anton", "saint anton", "sankt-variant"),
    ("Val-Thorens", "val thorens", "hyphen-to-space"),
    ("Val  Thorens", "val thorens", "collapsed-whitespace"),
    ("Verbier ski resort", "verbier ski resort", "noise-words-passthrough"),
    ("Aspen", "aspen", "absent-place-passthrough"),
    ("Val", "val", "ambiguous-short-passthrough"),
    ("", "", "empty-string"),
    ("   ", "", "whitespace-only"),
]


@pytest.mark.parametrize(
    ("raw", "expected"), [(c[0], c[1]) for c in _CASES], ids=[c[2] for c in _CASES]
)
def test_normalise(raw: str, expected: str) -> None:
    """normalise() reduces every alpine-name variant to its canonical form."""
    assert normalise(raw) == expected


def test_normalise_is_idempotent() -> None:
    """Normalising an already-normalised string is a no-op."""
    once = normalise("St. Anton am Arlberg")
    assert normalise(once) == once


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("St Anton", "Sankt Anton"),
        ("Val d'Isère", "Val d'Isere"),
        ("Val d'Isère", "Val dIsere"),
        ("Val-Thorens", "Val  Thorens"),
        ("VERBIER", "verbier"),
    ],
)
def test_normalise_equates_known_variant_pairs(a: str, b: str) -> None:
    """Distinct real-world spellings of the same place normalise identically."""
    assert normalise(a) == normalise(b)
