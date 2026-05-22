# conftest.py — pytest fixtures for the Météo-France BRA archive test suite.
#
# Provides paths to the committed sample PDFs and the sample index JSON so
# that individual test modules do not hard-code file paths.
"""Shared pytest fixtures for the meteofrance_archive test suite.

The ``parsed_*`` fixtures cache the result of ``parse_pdf(...)`` for the
session — without that, every assertion in test_to_caaml_*.py re-runs the
full PDF extraction (each call is ~0.7-2s on this machine), which dominated
the suite runtime before SNOW-spike. Tests that need a mutable copy can
``copy.deepcopy(parsed_mont_blanc)`` themselves; current tests only read.
"""

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

CHABLAIS_PDF = FIXTURES_DIR / "BRA.CHABLAIS.20260521140706.pdf"
MONT_BLANC_PDF = FIXTURES_DIR / "BRA.MONT-BLANC.20260521140702.pdf"
INDEX_JSON = FIXTURES_DIR / "bra.20260521.json"

# The meteofrance-archive scripts directory lives outside the import path
# normally, so each test module has historically inserted it via sys.path.
# Insert it once here so this conftest can import parse_pdf at session scope.
_SCRIPTS_DIR = Path(__file__).parents[3] / "scripts" / "meteofrance-archive"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


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


@pytest.fixture(scope="session")
def _parsed_chablais_cached() -> dict[str, Any]:
    """Parse CHABLAIS once per session. Internal — tests use ``parsed_chablais``."""
    from mf_bra_to_caaml import parse_pdf

    assert CHABLAIS_PDF.exists(), f"Missing fixture: {CHABLAIS_PDF}"
    result: dict[str, Any] | None = parse_pdf(CHABLAIS_PDF)
    assert result is not None
    return result


@pytest.fixture(scope="session")
def _parsed_mont_blanc_cached() -> dict[str, Any]:
    """Parse MONT-BLANC once per session. Internal — tests use ``parsed_mont_blanc``."""
    from mf_bra_to_caaml import parse_pdf

    assert MONT_BLANC_PDF.exists(), f"Missing fixture: {MONT_BLANC_PDF}"
    result: dict[str, Any] | None = parse_pdf(MONT_BLANC_PDF)
    assert result is not None
    return result


@pytest.fixture()
def parsed_chablais(_parsed_chablais_cached: dict[str, Any]) -> dict[str, Any]:
    """Return a deepcopy of the cached CHABLAIS parse result.

    Deepcopy keeps tests isolated from accidental mutation between cases
    while still avoiding the ~0.7s parse cost per test.
    """
    return copy.deepcopy(_parsed_chablais_cached)


@pytest.fixture()
def parsed_mont_blanc(_parsed_mont_blanc_cached: dict[str, Any]) -> dict[str, Any]:
    """Return a deepcopy of the cached MONT-BLANC parse result."""
    return copy.deepcopy(_parsed_mont_blanc_cached)


@pytest.fixture()
def sample_index_json() -> list[dict[str, object]]:
    """Return the parsed bra.20260521.json index fixture."""
    result: list[dict[str, object]] = json.loads(INDEX_JSON.read_text())
    return result
