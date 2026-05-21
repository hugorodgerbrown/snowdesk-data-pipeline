# conftest.py — pytest fixtures for the Météo-France BRA archive test suite.
#
# Provides paths to the committed sample PDFs and the sample index JSON so
# that individual test modules do not hard-code file paths.
"""Shared pytest fixtures for the meteofrance_archive test suite."""

import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

CHABLAIS_PDF = FIXTURES_DIR / "BRA.CHABLAIS.20260521140706.pdf"
MONT_BLANC_PDF = FIXTURES_DIR / "BRA.MONT-BLANC.20260521140702.pdf"
INDEX_JSON = FIXTURES_DIR / "bra.20260521.json"


@pytest.fixture()
def chablais_pdf_path() -> Path:
    """Return the path to the committed CHABLAIS sample PDF."""
    assert CHABLAIS_PDF.exists(), f"Missing fixture: {CHABLAIS_PDF}"
    return CHABLAIS_PDF


@pytest.fixture()
def mont_blanc_pdf_path() -> Path:
    """Return the path to the committed MONT-BLANC sample PDF."""
    assert MONT_BLANC_PDF.exists(), f"Missing fixture: {MONT_BLANC_PDF}"
    return MONT_BLANC_PDF


@pytest.fixture()
def sample_index_json() -> list[dict[str, object]]:
    """Return the parsed bra.20260521.json index fixture."""
    result: list[dict[str, object]] = json.loads(INDEX_JSON.read_text())
    return result
