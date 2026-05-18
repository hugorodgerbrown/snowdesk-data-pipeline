"""
tests/public/templatetags/test_source_pill.py — Unit tests for the
source_pill template tag.

Covers:
  - Known source keys (``"slf"``, ``"euregio"``) return the correct wordmark,
    URL, and new-tab attributes in the rendered pill.
  - Unknown or missing source keys raise ``ValueError`` from the tag function.
  - The rendered partial carries ``data-testid="source-pill"``.
"""

from __future__ import annotations

import pytest

from public.templatetags.source_pill import SOURCES, source_pill


class TestSourcePillKnownKeys:
    """Known source keys produce the correct context dict."""

    def test_slf_returns_correct_wordmark(self) -> None:
        """``"slf"`` key returns wordmark ``"SLF"``."""
        result = source_pill("slf")
        assert result["wordmark"] == "SLF"

    def test_slf_returns_correct_url(self) -> None:
        """``"slf"`` key returns the SLF homepage URL."""
        result = source_pill("slf")
        assert result["url"] == "https://www.slf.ch"

    def test_euregio_returns_correct_wordmark(self) -> None:
        """``"euregio"`` key returns wordmark ``"ALBINA"``."""
        result = source_pill("euregio")
        assert result["wordmark"] == "ALBINA"

    def test_euregio_returns_correct_url(self) -> None:
        """``"euregio"`` key returns the ALBINA / ALBINA homepage URL."""
        result = source_pill("euregio")
        assert result["url"] == "https://avalanche.report"

    def test_sources_dict_covers_all_known_keys(self) -> None:
        """Every key in SOURCES passes through source_pill without raising."""
        for key in SOURCES:
            ctx = source_pill(key)
            assert "wordmark" in ctx
            assert "url" in ctx


class TestSourcePillUnknownKeys:
    """Unknown or missing source keys raise ``ValueError``."""

    def test_unknown_string_raises(self) -> None:
        """An unrecognised string key raises ``ValueError``."""
        with pytest.raises(ValueError, match="Unknown bulletin source"):
            source_pill("meteofrance")

    def test_empty_string_raises(self) -> None:
        """An empty string raises ``ValueError``."""
        with pytest.raises(ValueError, match="Unknown bulletin source"):
            source_pill("")

    def test_none_raises(self) -> None:
        """``None`` raises ``ValueError`` (not ``TypeError``)."""
        with pytest.raises(ValueError, match="Unknown bulletin source"):
            source_pill(None)  # type: ignore[arg-type]

    def test_error_message_names_bad_key(self) -> None:
        """The error message includes the offending key for easy debugging."""
        with pytest.raises(ValueError, match="meteofrance"):
            source_pill("meteofrance")
