# test_pdf_extract.py — Tests for the _pdf_extract.py column crop helpers.
"""Tests for the pdfplumber column-crop helpers."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts" / "meteofrance-archive"))

from _pdf_extract import (  # noqa: E402
    _DANGER_NUM_HALF_H,
    _DANGER_NUM_HALF_W,
    COLUMN_SPLIT_X,
    DANGER_NUMBERS_FALLBACK_BBOX,
    crop_full_width,
    crop_left,
    crop_right,
    danger_numbers_bbox,
    extract_text_strip,
    extract_words_in_region,
    find_danger_box_centre,
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


def _make_curve(
    x0: float, top: float, x1: float, bottom: float, npts: int, stroke: bool, fill: bool
) -> dict:
    """Return a minimal pdfplumber curve dict for testing."""
    return {
        "x0": x0,
        "x1": x1,
        "top": top,
        "bottom": bottom,
        "path": [None] * npts,
        "stroke": stroke,
        "fill": fill,
    }


class TestFindDangerBoxCentre:
    """Tests for find_danger_box_centre() (SNOW-257 refinement 2)."""

    def _make_page(self, curves: list[dict]) -> MagicMock:
        """Return a mock page with the given curves list."""
        page = MagicMock()
        page.curves = curves
        return page

    def test_returns_centre_of_matching_rectangle(self) -> None:
        """Should detect the stroke-only rectangle and return its centre."""
        # Matches: inside search region, large enough, stroke-only, ≤10 path pts.
        page = self._make_page(
            [_make_curve(106.4, 128.1, 167.4, 176.7, npts=6, stroke=True, fill=False)]
        )
        centre = find_danger_box_centre(page)
        assert centre is not None
        cx, cy = centre
        assert abs(cx - 136.9) < 1.0
        assert abs(cy - 152.4) < 1.0

    def test_returns_none_when_no_matching_curve(self) -> None:
        """Should return None when no suitable rectangle is on the page."""
        page = self._make_page([])
        assert find_danger_box_centre(page) is None

    def test_ignores_filled_curves(self) -> None:
        """Filled rectangles are NOT the border box and must be ignored."""
        page = self._make_page(
            [_make_curve(106.4, 128.1, 167.4, 176.7, npts=6, stroke=True, fill=True)]
        )
        assert find_danger_box_centre(page) is None

    def test_ignores_small_curves(self) -> None:
        """Curves smaller than _DANGER_BOX_MIN_WIDTH/HEIGHT are noise, not the box."""
        page = self._make_page(
            [_make_curve(120.0, 140.0, 125.0, 145.0, npts=6, stroke=True, fill=False)]
        )
        assert find_danger_box_centre(page) is None

    def test_ignores_curves_outside_search_region(self) -> None:
        """Rectangles well outside the expected x/y zone should not match."""
        page = self._make_page(
            [_make_curve(400.0, 400.0, 500.0, 470.0, npts=6, stroke=True, fill=False)]
        )
        assert find_danger_box_centre(page) is None

    def test_works_on_chablais_fixture(self, chablais_pdf_path: Path) -> None:
        """Should locate the box on the real CHABLAIS PDF."""
        import pdfplumber

        with pdfplumber.open(chablais_pdf_path) as pdf:
            page = pdf.pages[0]
            centre = find_danger_box_centre(page)
        assert centre is not None
        cx, cy = centre
        # Allow ±5 pt tolerance from the calibrated values (136.9, 152.4).
        assert abs(cx - 136.9) < 5.0
        assert abs(cy - 152.4) < 5.0

    def test_works_on_mont_blanc_fixture(self, mont_blanc_pdf_path: Path) -> None:
        """Should locate the box on the real MONT-BLANC PDF."""
        import pdfplumber

        with pdfplumber.open(mont_blanc_pdf_path) as pdf:
            page = pdf.pages[0]
            centre = find_danger_box_centre(page)
        assert centre is not None
        cx, cy = centre
        assert abs(cx - 136.9) < 5.0
        assert abs(cy - 152.4) < 5.0

    def test_shifted_box_still_detected(self) -> None:
        """Box shifted ±20 points from calibrated position should still be found.

        This is the acceptance-criteria synthetic-shift test: confirms that the
        anchoring logic doesn't break when the template drifts by up to ±20 pt.
        """
        # Shift the box +20 pts right and +20 pts down from calibrated position.
        shifted_x0 = 106.4 + 20.0
        shifted_top = 128.1 + 20.0
        shifted_x1 = 167.4 + 20.0
        shifted_bottom = 176.7 + 20.0
        page = self._make_page(
            [
                _make_curve(
                    shifted_x0,
                    shifted_top,
                    shifted_x1,
                    shifted_bottom,
                    npts=6,
                    stroke=True,
                    fill=False,
                )
            ]
        )
        centre = find_danger_box_centre(page)
        assert centre is not None
        cx, cy = centre
        assert abs(cx - ((shifted_x0 + shifted_x1) / 2)) < 0.5
        assert abs(cy - ((shifted_top + shifted_bottom) / 2)) < 0.5

    def test_shifted_box_minus_20_still_detected(self) -> None:
        """Box shifted -20 pts left and -20 pts up should still be found."""
        shifted_x0 = 106.4 - 20.0
        shifted_top = 128.1 - 10.0  # stay above BAND_DANGER top (82)
        shifted_x1 = 167.4 - 20.0
        shifted_bottom = 176.7 - 10.0
        page = self._make_page(
            [
                _make_curve(
                    shifted_x0,
                    shifted_top,
                    shifted_x1,
                    shifted_bottom,
                    npts=6,
                    stroke=True,
                    fill=False,
                )
            ]
        )
        centre = find_danger_box_centre(page)
        assert centre is not None


class TestDangerNumbersBbox:
    """Tests for danger_numbers_bbox() (SNOW-257 refinement 2)."""

    def test_falls_back_to_hardcoded_when_box_not_found(self) -> None:
        """When the danger box cannot be located, the hardcoded fallback is returned."""
        page = MagicMock()
        page.curves = []
        bbox = danger_numbers_bbox(page)
        assert bbox == DANGER_NUMBERS_FALLBACK_BBOX

    def test_bbox_is_centred_on_box_when_found(self) -> None:
        """When the danger box is located, the bbox is centred on its centre."""
        page = MagicMock()
        page.curves = [
            _make_curve(106.4, 128.1, 167.4, 176.7, npts=6, stroke=True, fill=False)
        ]
        x0, top, x1, bottom = danger_numbers_bbox(page)
        # Expected centre: (136.9, 152.4)
        expected_cx = (106.4 + 167.4) / 2
        expected_cy = (128.1 + 176.7) / 2
        assert abs(((x0 + x1) / 2) - expected_cx) < 0.5
        assert abs(((top + bottom) / 2) - expected_cy) < 0.5
        # Half-widths must match the module constants.
        assert abs((x1 - x0) / 2 - _DANGER_NUM_HALF_W) < 0.1
        assert abs((bottom - top) / 2 - _DANGER_NUM_HALF_H) < 0.1

    def test_fallback_emits_warning(self) -> None:
        """Should log a warning when falling back to hardcoded coordinates."""
        page = MagicMock()
        page.curves = []
        with patch("_pdf_extract.logger") as mock_log:
            danger_numbers_bbox(page)
        assert mock_log.warning.called


# ---------------------------------------------------------------------------
# Headline detection unit tests (SNOW-257 refinement 1)
# These test _is_headline_line() from mf_bra_to_caaml directly to verify that
# the fragile prefix-based filtering is no longer needed.
# ---------------------------------------------------------------------------

from mf_bra_to_caaml import _is_headline_line  # noqa: E402


class TestIsHeadlineLine:
    """Unit tests for _is_headline_line() (SNOW-257 refinement 1)."""

    def test_all_caps_multi_word_is_headline(self) -> None:
        """All-caps multi-word lines are valid headlines."""
        assert _is_headline_line("MANTEAU NEIGEUX STABLE.") is True

    def test_all_caps_accented_is_headline(self) -> None:
        """All-caps lines with French accented characters are valid headlines."""
        assert _is_headline_line("PLAQUES À VENT À TRÈS HAUTES ALTITUDES") is True

    def test_sentence_case_ending_with_period_is_headline(self) -> None:
        """Sentence-case lines ending with '.' are valid headlines."""
        assert _is_headline_line("Manteau neigeux stable.") is True

    def test_single_compass_marker_is_not_headline(self) -> None:
        """A lone compass marker (< 2 tokens) is not a headline."""
        assert _is_headline_line("N") is False
        assert _is_headline_line("S") is False

    def test_two_compass_markers_are_not_headline(self) -> None:
        """'O E' (all single-char tokens) is not a headline."""
        assert _is_headline_line("O E") is False
        assert _is_headline_line("N S") is False

    def test_single_digit_is_not_headline(self) -> None:
        """A bare danger level digit is not a headline."""
        assert _is_headline_line("1") is False
        assert _is_headline_line("3") is False

    def test_two_digits_are_not_headline(self) -> None:
        """Two bare digits side by side are not a headline."""
        assert _is_headline_line("1 2") is False
        assert _is_headline_line("2 3") is False

    def test_mixed_digit_and_word_is_not_headline(self) -> None:
        """A line starting with a digit and then text is not an all-caps headline.

        Lines like '1 Départs spontanés' should not match — they start with a
        digit, not an uppercase letter, so the sentence-case branch does not fire.
        The all-caps branch also does not fire because '1' breaks the pattern.
        """
        assert _is_headline_line("1 Départs spontanés") is False

    def test_empty_string_is_not_headline(self) -> None:
        """Empty strings are not headlines."""
        assert _is_headline_line("") is False

    def test_lowercase_multi_word_is_not_headline(self) -> None:
        """Lowercase multi-word lines that don't end in '.' are not headlines."""
        assert _is_headline_line("indices de risque faible") is False
