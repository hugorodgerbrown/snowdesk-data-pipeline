# test_pdf_extract.py — Tests for the _pdf_extract.py column crop helpers.
"""Tests for the pdfplumber column-crop helpers."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from _pdf_extract import (  # noqa: E402
    COLUMN_SPLIT_X,
    crop_full_width,
    crop_left,
    crop_right,
    extract_text_strip,
    extract_words_in_region,
)


class TestCropLeft:
    """Tests for crop_left()."""

    def test_crops_to_left_of_split(self) -> None:
        """crop_left should crop to x=0..COLUMN_SPLIT_X."""
        page = MagicMock()
        page.width = 595.0
        page.crop.return_value = MagicMock()
        crop_left(page, 100.0, 200.0)
        page.crop.assert_called_once_with((0, 100.0, COLUMN_SPLIT_X, 200.0))

    def test_handles_narrow_page(self) -> None:
        """crop_left should not exceed the page width."""
        page = MagicMock()
        page.width = 200.0  # Narrower than COLUMN_SPLIT_X
        page.crop.return_value = MagicMock()
        crop_left(page, 0.0, 100.0)
        called_bbox = page.crop.call_args[0][0]
        assert called_bbox[2] <= 200.0


class TestCropRight:
    """Tests for crop_right()."""

    def test_crops_from_split_to_page_width(self) -> None:
        """crop_right should crop from COLUMN_SPLIT_X to page width."""
        page = MagicMock()
        page.width = 595.0
        page.crop.return_value = MagicMock()
        crop_right(page, 100.0, 200.0)
        page.crop.assert_called_once_with((COLUMN_SPLIT_X, 100.0, 595.0, 200.0))


class TestCropFullWidth:
    """Tests for crop_full_width()."""

    def test_crops_full_width(self) -> None:
        """crop_full_width should crop from x=0 to page width."""
        page = MagicMock()
        page.width = 595.0
        page.crop.return_value = MagicMock()
        crop_full_width(page, 50.0, 150.0)
        page.crop.assert_called_once_with((0, 50.0, 595.0, 150.0))


class TestExtractTextStrip:
    """Tests for extract_text_strip()."""

    def test_returns_stripped_text(self) -> None:
        """Should strip leading/trailing whitespace from extracted text."""
        region = MagicMock()
        region.extract_text.return_value = "  hello world  \n"
        assert extract_text_strip(region) == "hello world"

    def test_returns_empty_string_when_none(self) -> None:
        """Should return empty string when extract_text() returns None."""
        region = MagicMock()
        region.extract_text.return_value = None
        assert extract_text_strip(region) == ""


class TestExtractWordsInRegion:
    """Tests for extract_words_in_region()."""

    def _make_word(
        self, x0: float, top: float, x1: float, bottom: float, text: str
    ) -> dict:
        """Return a mock word dict."""
        return {"x0": x0, "top": top, "x1": x1, "bottom": bottom, "text": text}

    def test_returns_words_within_bbox(self) -> None:
        """Words whose centre is inside the bbox should be included."""
        page = MagicMock()
        # Word centred at (150, 150)
        page.extract_words.return_value = [
            self._make_word(100, 100, 200, 200, "inside"),
            self._make_word(300, 300, 400, 400, "outside"),
        ]
        result = extract_words_in_region(page, 50, 50, 250, 250)
        assert len(result) == 1
        assert result[0]["text"] == "inside"

    def test_excludes_words_outside_bbox(self) -> None:
        """Words whose centre is outside the bbox should be excluded."""
        page = MagicMock()
        page.extract_words.return_value = [
            self._make_word(0, 0, 10, 10, "outside"),
        ]
        result = extract_words_in_region(page, 100, 100, 200, 200)
        assert result == []


class TestIntegrationWithRealPdf:
    """Integration tests using the committed CHABLAIS fixture."""

    def test_crop_left_extracts_text(self, chablais_pdf_path: Path) -> None:
        """crop_left should return a region with extractable text."""
        import pdfplumber

        with pdfplumber.open(chablais_pdf_path) as pdf:
            page = pdf.pages[0]
            region = crop_left(page, 215.0, 360.0)
            text = extract_text_strip(region)
        assert "manteau" in text.lower()

    def test_crop_right_extracts_weather(self, chablais_pdf_path: Path) -> None:
        """crop_right over the weather area should return weather text."""
        import pdfplumber

        with pdfplumber.open(chablais_pdf_path) as pdf:
            page = pdf.pages[0]
            region = crop_right(page, 435.0, 545.0)
            text = extract_text_strip(region)
        assert "vent" in text.lower()
