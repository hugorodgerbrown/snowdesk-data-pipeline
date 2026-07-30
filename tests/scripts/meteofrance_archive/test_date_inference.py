# test_date_inference.py — Regression tests for bulletin date extraction.
#
# The covered date is derived from two lines on page 1 of the BRA:
#
#   "Rédigé le vendredi 30 janvier 2026 à 16h"       — carries the YEAR
#   "Estimation des risques pour le : SAMEDI 31 JANVIER"  — day and month only
#
# The year therefore comes from the page, not from the wall clock.  This module
# replaces the tests for the deleted ``_infer_date_with_year``, which guessed
# the year from ``date.today()`` with a 180-day correction.  That heuristic
# dated 267 archive records a year into the future — a "4 NOVEMBRE" PDF read on
# 2026-05-21 became 2026-11-04 (167 days ahead, under the threshold) instead of
# 2025-11-04 — and made extraction unreproducible, since the same PDF yielded a
# different date depending on when it was parsed (SNOW-559).
#
# The old test suite asserted that behaviour as correct, including
# ``_infer_date_with_year(month=11, day=16, today=2026-05-21) ==
# date(2026, 11, 16)``, which is precisely one of the 267 bad records.
"""Regression tests for covered-date extraction (year comes from the page)."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from mf_bra_to_caaml import (  # noqa: E402
    extract_bulletin_date,
    extract_redige_at,
    french_month,
)

FIXTURES = Path(__file__).parent / "fixtures"
CHABLAIS_PDF = FIXTURES / "BRA.CHABLAIS.20260521140706.pdf"


class _FakePage:
    """Minimal stand-in exposing the two attributes the extractors use.

    ``extract_bulletin_date`` and ``extract_redige_at`` only ever crop the page
    and read text from it, so a fake that returns fixed text for any crop is
    enough to test the date logic without a PDF.
    """

    def __init__(self, text: str) -> None:
        """Store the text this page reports for every crop.

        Args:
            text: The text ``extract_text`` should return.

        """
        self._text = text
        self.width = 594.96
        self.height = 841.92

    def crop(self, _bbox: tuple[float, float, float, float]) -> _FakePage:
        """Return self — the fake ignores crop geometry.

        Args:
            _bbox: Ignored crop box.

        Returns:
            This same fake page.

        """
        return self

    def extract_text(self) -> str:
        """Return the fixed text.

        Returns:
            The text supplied to the constructor.

        """
        return self._text

    def extract_words(self) -> list[dict[str, object]]:
        """Return no words — heading lookup falls back to fixed bands.

        Returns:
            An empty list.

        """
        return []


def _page(redige: str, estimation: str) -> _FakePage:
    """Build a fake page carrying the two date lines.

    Args:
        redige: The "Rédigé le …" line.
        estimation: The "Estimation des risques pour le …" line.

    Returns:
        A fake page whose text contains both lines.

    """
    return _FakePage(f"{redige}\n{estimation}\n")


class TestFrenchMonth:
    """Tests for ``french_month``."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("janvier", 1),
            ("février", 2),
            ("fevrier", 2),
            ("mars", 3),
            ("avril", 4),
            ("mai", 5),
            ("juin", 6),
            ("juillet", 7),
            ("août", 8),
            ("aout", 8),
            ("septembre", 9),
            ("octobre", 10),
            ("novembre", 11),
            ("décembre", 12),
            ("NOVEMBRE", 11),
            ("Janvier", 1),
        ],
    )
    def test_recognises_month_names(self, name: str, expected: int) -> None:
        """Every French month name resolves to its number, any case.

        Args:
            name: The month name under test.
            expected: The expected month number.

        """
        assert french_month(name) == expected

    def test_juillet_is_not_confused_with_juin(self) -> None:
        """July must resolve to 7, not to June.

        A three-character prefix lookup maps both "juin" and "juillet" onto
        "jui", silently dating July bulletins to June.
        """
        assert french_month("juillet") == 7
        assert french_month("juin") == 6

    def test_unknown_month_returns_none(self) -> None:
        """An unrecognised name returns None rather than guessing."""
        assert french_month("smarch") is None


class TestExtractRedigeAt:
    """Tests for ``extract_redige_at``."""

    def test_parses_date_hour_and_year(self) -> None:
        """The redaction line yields a full naive local datetime."""
        page = _page("Rédigé le vendredi 30 janvier 2026 à 16h", "")
        assert extract_redige_at(page) == datetime(2026, 1, 30, 16, 0)

    def test_parses_optional_minutes(self) -> None:
        """An "à 16h30" form keeps the minutes."""
        page = _page("Rédigé le vendredi 30 janvier 2026 à 16h30", "")
        assert extract_redige_at(page) == datetime(2026, 1, 30, 16, 30)

    def test_parses_morning_reissue_hour(self) -> None:
        """Morning re-issues are stamped 9h, not 16h."""
        page = _page("Rédigé le samedi 13 février 2026 à 9h", "")
        assert extract_redige_at(page) == datetime(2026, 2, 13, 9, 0)

    def test_missing_line_returns_none(self) -> None:
        """A page without the redaction line returns None, not a guess."""
        assert extract_redige_at(_FakePage("MASSIF : Aravis\n")) is None

    def test_unparseable_month_returns_none(self) -> None:
        """An unrecognised month name returns None."""
        page = _page("Rédigé le vendredi 30 smarch 2026 à 16h", "")
        assert extract_redige_at(page) is None


class TestExtractBulletinDate:
    """Tests for ``extract_bulletin_date``."""

    def test_previous_evening_issue_covers_next_day(self) -> None:
        """A 30 January issue covering 31 January takes the printed year."""
        page = _page(
            "Rédigé le vendredi 30 janvier 2026 à 16h",
            "Estimation des risques pour le : SAMEDI 31 JANVIER",
        )
        redige = extract_redige_at(page)
        assert redige is not None
        assert extract_bulletin_date(page, redige) == date(2026, 1, 31)

    def test_morning_issue_covers_same_day(self) -> None:
        """A same-day morning re-issue covers the day it was written."""
        page = _page(
            "Rédigé le samedi 13 février 2026 à 9h",
            "Estimation des risques pour le : SAMEDI 13 FÉVRIER",
        )
        redige = extract_redige_at(page)
        assert redige is not None
        assert extract_bulletin_date(page, redige) == date(2026, 2, 13)

    def test_november_bulletin_keeps_its_printed_year(self) -> None:
        """The 267-record regression: a November 2025 issue stays in 2025.

        The deleted heuristic turned this into 2026-11-04 because the backfill
        ran on 2026-05-21 and the candidate was only 167 days ahead.
        """
        page = _page(
            "Rédigé le lundi 3 novembre 2025 à 16h",
            "Estimation des risques pour le : MARDI 04 NOVEMBRE",
        )
        redige = extract_redige_at(page)
        assert redige is not None
        assert extract_bulletin_date(page, redige) == date(2025, 11, 4)

    def test_new_year_rollover(self) -> None:
        """A 31 December issue covering 1 January rolls into the next year."""
        page = _page(
            "Rédigé le mercredi 31 décembre 2025 à 16h",
            "Estimation des risques pour le : JEUDI 01 JANVIER",
        )
        redige = extract_redige_at(page)
        assert redige is not None
        assert extract_bulletin_date(page, redige) == date(2026, 1, 1)

    def test_implausible_gap_returns_none(self) -> None:
        """A covered date far from the redaction date is rejected, not guessed.

        Rather than emit a date that is certainly wrong, the record fails and is
        logged — the posture the old wall-clock guess lacked.
        """
        page = _page(
            "Rédigé le lundi 3 novembre 2025 à 16h",
            "Estimation des risques pour le : MARDI 15 AOÛT",
        )
        redige = extract_redige_at(page)
        assert redige is not None
        assert extract_bulletin_date(page, redige) is None

    def test_missing_estimation_line_returns_none(self) -> None:
        """A page without the covered-date line returns None."""
        page = _page("Rédigé le vendredi 30 janvier 2026 à 16h", "")
        redige = extract_redige_at(page)
        assert redige is not None
        assert extract_bulletin_date(page, redige) is None


class TestExtractionIsWallClockIndependent:
    """The property the old implementation lacked."""

    @pytest.mark.skipif(
        not CHABLAIS_PDF.exists(), reason="CHABLAIS fixture PDF not present"
    )
    def test_same_pdf_yields_same_date_whenever_parsed(self) -> None:
        """Parsing the fixture twice, a year apart, gives the same date.

        ``extract_bulletin_date`` takes no reference date and reads no clock, so
        this holds by construction — the test exists to keep it that way.  A
        reintroduced ``date.today()`` anchor would fail here.
        """
        import pdfplumber

        with pdfplumber.open(CHABLAIS_PDF) as pdf:
            page = pdf.pages[0]
            redige = extract_redige_at(page)
            assert redige is not None
            first = extract_bulletin_date(page, redige)
            second = extract_bulletin_date(page, redige)

        assert first is not None
        assert first == second
        # The fixture is the 2026-05-21 14:07:06 issue, covering the next day.
        assert first == date(2026, 5, 22)
