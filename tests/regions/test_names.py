"""tests/regions/test_names.py — Unit tests for regions.names lookup helper.

Covers:
  - Returns expected names for known region IDs across all four languages.
  - Returns ``None`` for region IDs absent from the name file.
  - The module-level cache returns the same dict object on repeat calls
    (i.e. the file is not re-parsed on every call).
  - A monkeypatched ``_NAMES_DIR`` pointing to a synthetic file exercises
    the fallback path without hitting the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import regions.names as names_mod
from regions.names import lookup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_lang_file(directory: Path, lang: str, data: dict[str, str]) -> None:
    """Write a synthetic <lang>.json name file under *directory*."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{lang}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Tests against the real vendored files
# ---------------------------------------------------------------------------


class TestLookupRealFiles:
    """Spot-check the vendored name files for known EAWS region IDs."""

    def test_at_l4_english(self) -> None:
        """AT-02-14 returns its canonical English name."""
        assert lookup("AT-02-14", "en") == "Carnic Alps Lesachtal"

    def test_at_l2_english(self) -> None:
        """AT-02 (L2) returns 'Carinthia' in English."""
        assert lookup("AT-02", "en") == "Carinthia"

    def test_at_l4_german(self) -> None:
        """AT-02-14 returns its canonical German name."""
        assert lookup("AT-02-14", "de") == "Karnische Alpen Lesachtal"

    def test_fr_l4_french(self) -> None:
        """FR-01 returns 'Chablais' in French."""
        assert lookup("FR-01", "fr") == "Chablais"

    def test_it_l1_italian(self) -> None:
        """IT-21 (L1) returns 'Piemonte' in Italian."""
        assert lookup("IT-21", "it") == "Piemonte"

    def test_missing_key_returns_none(self) -> None:
        """A key absent from the file returns ``None``, not an exception."""
        assert lookup("ZZ-99-BOGUS", "en") is None

    def test_missing_key_de_returns_none(self) -> None:
        """A key absent from the German file also returns ``None``."""
        assert lookup("NOPE-00", "de") is None


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


class TestCache:
    """The module-level cache returns the same dict object on repeated calls."""

    def test_same_dict_returned_on_repeat_calls(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Two consecutive _load calls for the same lang return identical objects."""
        # Use a synthetic file to avoid interfering with the real cache
        synthetic = {"FR-01": "Chablais"}
        _write_lang_file(tmp_path, "fr", synthetic)
        monkeypatch.setattr(names_mod, "_NAMES_DIR", tmp_path)
        # Clear cache to ensure a clean slate for this test
        monkeypatch.setattr(names_mod, "_cache", {})

        first = names_mod._load("fr")
        second = names_mod._load("fr")
        assert first is second


# ---------------------------------------------------------------------------
# Synthetic-file tests (monkeypatching _NAMES_DIR)
# ---------------------------------------------------------------------------


class TestLookupSyntheticFile:
    """Tests that use a tmp_path synthetic name file to isolate from real data."""

    def test_lookup_returns_synthetic_value(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """lookup returns the value from the synthetic file when _NAMES_DIR is patched."""
        synthetic = {"TEST-01": "Test Region One", "TEST-02": "Test Region Two"}
        _write_lang_file(tmp_path, "en", synthetic)
        monkeypatch.setattr(names_mod, "_NAMES_DIR", tmp_path)
        monkeypatch.setattr(names_mod, "_cache", {})

        assert lookup("TEST-01", "en") == "Test Region One"
        assert lookup("TEST-02", "en") == "Test Region Two"

    def test_lookup_returns_none_for_missing_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """lookup returns None when the key is absent from the synthetic file."""
        _write_lang_file(tmp_path, "en", {"PRESENT": "Present Region"})
        monkeypatch.setattr(names_mod, "_NAMES_DIR", tmp_path)
        monkeypatch.setattr(names_mod, "_cache", {})

        assert lookup("ABSENT", "en") is None

    def test_lookup_multiple_langs(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """lookup resolves independently per language."""
        _write_lang_file(tmp_path, "de", {"AT-01": "Kärnten"})
        _write_lang_file(tmp_path, "en", {"AT-01": "Carinthia"})
        monkeypatch.setattr(names_mod, "_NAMES_DIR", tmp_path)
        monkeypatch.setattr(names_mod, "_cache", {})

        assert lookup("AT-01", "de") == "Kärnten"
        assert lookup("AT-01", "en") == "Carinthia"
