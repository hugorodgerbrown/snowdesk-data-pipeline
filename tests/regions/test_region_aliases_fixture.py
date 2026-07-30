"""
tests/regions/test_region_aliases_fixture.py — SNOW-409 fixture integrity guard.

Reads ``apps/regions/fixtures/region_aliases.json`` and the four ``eaws_*.json``
fixtures directly as JSON — no Django, no database — and checks that every
alias row's ``region`` natural key resolves to a MicroRegion actually
present in the committed EAWS fixtures, and that no ``(region, alias_text)``
pair is duplicated. Kept DB-free so it runs in milliseconds and can't be
skewed by test-DB state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "apps" / "regions" / "fixtures"

_EAWS_FIXTURE_NAMES = ["eaws_CH.json", "eaws_FR.json", "eaws_AT.json", "eaws_IT.json"]


def _load_fixture(name: str) -> list[dict[str, Any]]:
    """Return the parsed JSON rows for a fixture file under apps/regions/fixtures/."""
    with (FIXTURES_DIR / name).open(encoding="utf-8") as f:
        rows: list[dict[str, Any]] = json.load(f)
        return rows


def _known_micro_region_ids() -> set[str]:
    """Return every MicroRegion.region_id present across the eaws_*.json fixtures."""
    ids: set[str] = set()
    for name in _EAWS_FIXTURE_NAMES:
        for row in _load_fixture(name):
            if row["model"] == "regions.microregion":
                ids.add(row["fields"]["region_id"])
    return ids


def _region_aliases_rows() -> list[dict[str, Any]]:
    """Return the parsed rows from apps/regions/fixtures/region_aliases.json."""
    return _load_fixture("region_aliases.json")


def test_region_aliases_fixture_is_a_nonempty_list() -> None:
    """The fixture parses as JSON and has at least one row."""
    rows = _region_aliases_rows()
    assert isinstance(rows, list)
    assert rows


def test_region_aliases_rows_are_pk_less_natural_key_rows() -> None:
    """Every row is a regions.regionalias fixture row with no explicit pk."""
    for row in _region_aliases_rows():
        assert row["model"] == "regions.regionalias"
        assert "pk" not in row
        assert isinstance(row["fields"]["region"], list)
        assert len(row["fields"]["region"]) == 1
        assert isinstance(row["fields"]["alias_text"], str)
        assert row["fields"]["alias_text"]


def test_region_aliases_reference_only_known_micro_regions() -> None:
    """Every alias's region natural key resolves to a real EAWS MicroRegion.

    A typo'd or stale region_id here would silently produce a RegionAlias
    that never loads (loaddata raises) or, worse, points nowhere useful —
    catching it here is far cheaper than discovering it on a Render deploy.
    """
    known_ids = _known_micro_region_ids()
    unknown = [
        row["fields"]["region"][0]
        for row in _region_aliases_rows()
        if row["fields"]["region"][0] not in known_ids
    ]
    assert not unknown, (
        f"region_aliases.json references unknown region_id(s): {unknown}"
    )


def test_region_aliases_have_no_duplicate_pairs() -> None:
    """No (region_id, alias_text) pair is repeated across the fixture.

    A duplicate pair wouldn't break ``loaddata`` (it would just overwrite
    the same row twice), but it signals a copy-paste error in the curated
    list worth catching explicitly.
    """
    pairs = [
        (row["fields"]["region"][0], row["fields"]["alias_text"])
        for row in _region_aliases_rows()
    ]
    duplicates = {pair for pair in pairs if pairs.count(pair) > 1}
    assert not duplicates, f"Duplicate (region, alias_text) pairs: {duplicates}"
