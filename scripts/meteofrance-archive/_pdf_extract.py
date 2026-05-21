# _pdf_extract.py — Column-aware pdfplumber extraction helpers.
#
# BRA PDFs use a two-column layout on page 1 (left: snowpack/stability narrative;
# right: weather summary + wind table).  Page 2 is a historical chart grid.
# These helpers use page.crop() to isolate regions before extracting text, which
# significantly reduces noise from the adjacent column bleeding into extractions.
#
# Page dimensions are always 595.0 x 841.9 points (A4 at 72 dpi).
# Column split: roughly x=280 points.
# Vertical layout (approximate y-coordinates, top=0 in pdfplumber):
#   0–80:    Header (title, massif, date)
#   80–215:  Danger rating box + compass rose + highlights
#   215–360: Snowpack stability + SAT
#   360–510: Snow quality (left) / Weather summary + wind table (right)
#   510–680: Snow depth chart (left) / Tendency (right)
#   680–800: Historical snow depth bars
"""Column-aware pdfplumber crop helpers for BRA PDF parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pdfplumber

# Approximate column split x-coordinate (points).
COLUMN_SPLIT_X = 280.0

# Standard A4 PDF width in points.
PAGE_WIDTH = 594.96

# Vertical band definitions (top, bottom) in page coordinates.
BAND_HEADER = (0.0, 82.0)
BAND_DANGER = (82.0, 220.0)
BAND_STABILITY = (220.0, 360.0)
BAND_QUALITY = (355.0, 510.0)
BAND_SNOW_DEPTH = (500.0, 680.0)
BAND_HISTORY_CHART = (620.0, 840.0)

# Right column sub-regions.
BAND_WEATHER_TEXT = (355.0, 435.0)
# Wind table extends from iso/pluie-neige header down to below the second wind row.
# Different massifs place the second wind row at different y-coordinates (up to ~535).
BAND_WIND_TABLE = (435.0, 545.0)


def crop_left(
    page: "pdfplumber.page.Page", top: float, bottom: float
) -> "pdfplumber.page.Page":
    """Return a cropped region from the left column of the given page.

    Args:
        page: The pdfplumber page object.
        top: Top y-coordinate of the region (points from top of page).
        bottom: Bottom y-coordinate of the region.

    Returns:
        A cropped pdfplumber page object.

    """
    w = float(page.width)
    split = min(COLUMN_SPLIT_X, w)
    return page.crop((0, top, split, bottom))


def crop_right(
    page: "pdfplumber.page.Page", top: float, bottom: float
) -> "pdfplumber.page.Page":
    """Return a cropped region from the right column of the given page.

    Args:
        page: The pdfplumber page object.
        top: Top y-coordinate of the region.
        bottom: Bottom y-coordinate of the region.

    Returns:
        A cropped pdfplumber page object.

    """
    w = float(page.width)
    return page.crop((COLUMN_SPLIT_X, top, w, bottom))


def crop_full_width(
    page: "pdfplumber.page.Page", top: float, bottom: float
) -> "pdfplumber.page.Page":
    """Return a full-width crop of the given page between top and bottom.

    Args:
        page: The pdfplumber page object.
        top: Top y-coordinate of the region.
        bottom: Bottom y-coordinate of the region.

    Returns:
        A cropped pdfplumber page object.

    """
    w = float(page.width)
    return page.crop((0, top, w, bottom))


def extract_text_strip(region: "pdfplumber.page.Page") -> str:
    """Extract text from a region, returning an empty string if None.

    Args:
        region: A (possibly cropped) pdfplumber page object.

    Returns:
        Stripped text string, or ``""`` if no text was found.

    """
    text = region.extract_text()
    return text.strip() if text else ""


def extract_words_in_region(
    page: "pdfplumber.page.Page",
    x0: float,
    top: float,
    x1: float,
    bottom: float,
) -> list[dict[str, object]]:
    """Return all words whose centre falls within the given bounding box.

    Args:
        page: The pdfplumber page object.
        x0: Left boundary.
        top: Top boundary.
        x1: Right boundary.
        bottom: Bottom boundary.

    Returns:
        List of word dicts (from pdfplumber) within the bounds.

    """
    words = page.extract_words()
    results = []
    for w in words:
        cx = (float(w["x0"]) + float(w["x1"])) / 2
        cy = (float(w["top"]) + float(w["bottom"])) / 2
        if x0 <= cx <= x1 and top <= cy <= bottom:
            results.append(w)
    return results
