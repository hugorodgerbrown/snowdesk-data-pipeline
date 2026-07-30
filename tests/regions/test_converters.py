"""
tests/regions/test_converters.py — Unit tests for RegionIdConverter.

Covers:
  - The regex matches every region ID stored in the EAWS fixtures (both
    as stored, e.g. ``CH-4115``, and in their canonical lowercase form).
  - The regex rejects common bot-scanner probes that would otherwise consume
    a DB lookup inside the view.
  - ``to_python`` and ``to_url`` are identity round-trips.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from apps.regions.converters import RegionIdConverter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent.parent / "apps" / "regions" / "fixtures"
)

_FIXTURE_FILES = [
    _FIXTURE_DIR / "eaws_AT.json",
    _FIXTURE_DIR / "eaws_CH.json",
    _FIXTURE_DIR / "eaws_FR.json",
    _FIXTURE_DIR / "eaws_IT.json",
]


def _load_fixture_region_ids() -> list[str]:
    """Return all ``region_id`` values found in the EAWS fixture files."""
    ids: list[str] = []
    for path in _FIXTURE_FILES:
        data = json.loads(path.read_text())
        for item in data:
            if "fields" in item and "region_id" in item["fields"]:
                ids.append(item["fields"]["region_id"])
    return ids


# ---------------------------------------------------------------------------
# Regex tests
# ---------------------------------------------------------------------------


class TestRegionIdConverterRegex:
    """The regex matches real region IDs and rejects probe strings."""

    def test_regex_matches_all_fixture_ids_as_stored(self) -> None:
        """Every fixture region_id matches the converter regex as stored."""
        ids = _load_fixture_region_ids()
        assert len(ids) > 0, "No fixture IDs loaded — check fixture paths"
        failed = [rid for rid in ids if not re.fullmatch(RegionIdConverter.regex, rid)]
        assert failed == [], f"Fixture IDs that failed regex: {failed}"

    def test_regex_matches_all_fixture_ids_lowercased(self) -> None:
        """Every fixture region_id also matches when converted to lowercase."""
        ids = _load_fixture_region_ids()
        failed = [
            rid for rid in ids if not re.fullmatch(RegionIdConverter.regex, rid.lower())
        ]
        assert failed == [], f"Lowercased fixture IDs that failed regex: {failed}"

    @pytest.mark.parametrize(
        "probe",
        [
            "admin",
            ".git",
            "sqlite.db",
            "wp-login",
            "wp-admin",
            "phpmyadmin",
            "env",
            "config.json",
        ],
    )
    def test_regex_rejects_scanner_probes(self, probe: str) -> None:
        """Common bot-scanner probes do not match the converter regex."""
        assert re.fullmatch(RegionIdConverter.regex, probe) is None, (
            f"Probe '{probe}' should have been rejected but matched."
        )


# ---------------------------------------------------------------------------
# Identity method tests
# ---------------------------------------------------------------------------


class TestRegionIdConverterIdentity:
    """``to_python`` and ``to_url`` are identity functions."""

    @pytest.mark.parametrize(
        "value",
        ["CH-4115", "ch-4115", "AT-02-01-01", "FR-52", "IT-32-BZ-01"],
    )
    def test_to_python_is_identity(self, value: str) -> None:
        """``to_python`` returns its input unchanged."""
        converter = RegionIdConverter()
        assert converter.to_python(value) == value

    @pytest.mark.parametrize(
        "value",
        ["CH-4115", "ch-4115", "AT-02-01-01", "FR-52", "IT-32-BZ-01"],
    )
    def test_to_url_is_identity(self, value: str) -> None:
        """``to_url`` returns its input unchanged."""
        converter = RegionIdConverter()
        assert converter.to_url(value) == value
