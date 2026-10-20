"""
tests/regions/test_cantons.py — Tests for apps.regions.cantons (SNOW-1150).

Covers:
  - ``CANTON_NAMES`` holds exactly the 26 Swiss canton codes.
  - Every canton code in the curated resort sheet (``resorts.tsv``),
    compound or not, resolves to a name rather than falling back to the code.
  - A compound code renders each part, joined by " / ".
  - Lower case and stray whitespace are tolerated.
  - An unknown code falls back to the code itself; an empty code is "".
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from apps.regions.cantons import CANTON_NAMES, canton_label

_RESORT_SHEET = (
    Path(__file__).resolve().parent.parent.parent
    / "apps"
    / "regions"
    / "data"
    / "resorts.tsv"
)


def _sheet_canton_codes() -> set[str]:
    """Return every distinct ``canton`` value in the curated resort sheet."""
    with _RESORT_SHEET.open(encoding="utf-8", newline="") as handle:
        return {row["canton"] for row in csv.DictReader(handle, delimiter="\t")}


def test_all_26_cantons_are_named() -> None:
    """The table is the complete set of Swiss cantons, no more, no fewer."""
    assert len(CANTON_NAMES) == 26
    assert all(len(code) == 2 and code.isupper() for code in CANTON_NAMES)


@pytest.mark.parametrize("code", sorted(_sheet_canton_codes()))
def test_every_code_in_the_resort_sheet_has_a_name(code: str) -> None:
    """No sheet code falls back to its own abbreviation."""
    for part in code.split("/"):
        assert part in CANTON_NAMES, f"{part!r} (from {code!r}) has no name"
    assert canton_label(code) != code


def test_a_single_code_is_named() -> None:
    """A plain code returns its English name."""
    assert canton_label("VS") == "Valais"
    assert canton_label("GR") == "Graubünden"


def test_a_compound_code_names_each_part() -> None:
    """A border-straddling resort's code renders both cantons."""
    assert canton_label("BE/VS") == "Bern / Valais"
    assert canton_label("GR/TI") == "Graubünden / Ticino"


def test_case_and_whitespace_are_tolerated() -> None:
    """A hand-typed code with spacing or lower case still resolves."""
    assert canton_label(" be / vs ") == "Bern / Valais"


def test_an_unknown_code_falls_back_to_itself() -> None:
    """An unexpected value stays visible rather than vanishing."""
    assert canton_label("XX") == "XX"
    assert canton_label("BE/XX") == "Bern / XX"


def test_an_empty_code_is_empty() -> None:
    """No canton, no label."""
    assert canton_label("") == ""
