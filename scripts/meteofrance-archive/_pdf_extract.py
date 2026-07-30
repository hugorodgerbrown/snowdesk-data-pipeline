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
#
# Page 2 layout (approximate y-coordinates, A4 at 72 dpi):
#   0–100:   Header (bulletin title, massif, date)
#   100–215: Wind-history grid: 28 cells (7 days × 4 time slots) per altitude row
#            Row 1 (higher altitude, e.g. 2500m / 4000m): y ≈ 140–165
#            Label "à Xm": y ≈ 160–215
#            Row 2 (lower altitude, e.g. 2000m): y ≈ 175–205
#   215–355: Iso 0°C / rain-snow-line chart
#   355–445: Fresh-snow bar chart (daily cm totals)
#   445–510: Historical danger rating row
#   510–660: Total snow-depth bar chart
#   660–790: Snow-line elevation charts (Versant Nord left, Versant Sud right)
#
# Page-2 spatial constants are calibrated against the CHABLAIS and MONT-BLANC
# fixtures (both A4, 594.96 × 841.92 pt).  The wind-history data grid spans
# the full page width from x ≈ 50 to x ≈ 590.  The snow-line chart splits at
# the horizontal page midpoint: Nord panel x ∈ [60, 265], Sud panel x ∈ [310, 570].
#
# Danger-number box anchor (SNOW-257):
#   The danger numbers (1–5) are rendered inside a rectangular bordered box that
#   consistently appears at approximately x ∈ [106, 167], y ∈ [128, 177] in the
#   CHABLAIS and MONT-BLANC fixtures.  find_danger_box_centre() detects this box
#   from page.curves (a stroke-only, 6-point path in that region) and returns its
#   centre as an anchor for _find_danger_numbers_in_area().  If the box cannot be
#   located the callers fall back to the historical hardcoded bbox.
"""Column-aware pdfplumber crop helpers for BRA PDF parsing."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pdfplumber

logger = logging.getLogger(__name__)

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

# ---------------------------------------------------------------------------
# Page 2 band definitions
# ---------------------------------------------------------------------------
# All coordinates are (top_y, bottom_y) in page-2 coordinate space.
# Calibrated against BRA.CHABLAIS.20260521140706.pdf and
# BRA.MONT-BLANC.20260521140702.pdf (both 594.96 × 841.92 pt A4).

# Wind-history grid: two altitude rows, each containing 28 speed values
# (7 days × 4 time slots: 03h, 09h, 15h, 21h).  Each row's numeric values
# sit at y ≈ 145–165 (row 1, higher altitude) and y ≈ 180–200 (row 2, lower
# altitude).  The altitude label "à Xm" follows the row on the next text line.
BAND_PAGE2_WIND_HISTORY = (130.0, 215.0)

# Fresh-snow bar chart: daily cm totals.  The value labels ("N cm") appear
# inside the chart area.  The x-axis date labels sit just below at y ≈ 342.
BAND_PAGE2_FRESH_SNOW = (355.0, 445.0)
# X-axis date-label band directly below the fresh-snow chart.
BAND_PAGE2_FRESH_SNOW_XAXIS = (338.0, 352.0)

# Snow-line elevation charts: two side-by-side panels.
# Versant Nord (north aspect): x in [60, 265].
# Versant Sud (south aspect): x in [310, 570].
# Numeric elevation labels appear within the chart area.
# The y-axis labels (fixed ticks such as 2400, 1800, 1200, 600, 0) sit at
# x < 60 for Nord and at x < 310 or x > 510 for Sud.
BAND_PAGE2_SNOW_LINE = (670.0, 765.0)
# X-axis date-label band directly below both snow-line panels.
BAND_PAGE2_SNOW_LINE_XAXIS = (769.0, 782.0)

# Spatial split between Nord and Sud snow-line panels (x-coordinate).
PAGE2_SNOW_LINE_SPLIT_X = 285.0
# Plot area x bounds within the Nord panel (excludes fixed y-axis labels at x<60).
PAGE2_SNOW_LINE_NORD_X_START = 60.0
PAGE2_SNOW_LINE_NORD_X_END = 265.0
# Plot area x bounds within the Sud panel (excludes y-axis labels at x<310).
PAGE2_SNOW_LINE_SUD_X_START = 310.0
PAGE2_SNOW_LINE_SUD_X_END = 570.0

# ---------------------------------------------------------------------------
# Danger-number box anchor constants (SNOW-257)
# ---------------------------------------------------------------------------

# The danger-number display box (a stroke-only rectangle on page 1) consistently
# appears in the region x ∈ [90, 200], y ∈ [115, 195] in the two fixture PDFs.
# The search window is expanded by ±20 pt on every side to tolerate template drift
# of up to 20 points (the acceptance criterion from SNOW-257).
_DANGER_BOX_SEARCH_X0 = 70.0
_DANGER_BOX_SEARCH_X1 = 220.0
_DANGER_BOX_SEARCH_TOP = 95.0
_DANGER_BOX_SEARCH_BOTTOM = 215.0
# Minimum dimensions for the danger box (to exclude small artefacts).
_DANGER_BOX_MIN_WIDTH = 40.0
_DANGER_BOX_MIN_HEIGHT = 20.0

# Hardcoded fallback bbox (x0, top, x1, bottom) for _find_danger_numbers_in_area().
# Calibrated from two fixture PDFs; used when the box cannot be located via graphics.
DANGER_NUMBERS_FALLBACK_BBOX = (110.0, 135.0, 170.0, 185.0)

# Offsets from the danger-box centre to the danger-number search bbox.
# The danger numbers occupy the inner half of the box; these offsets trim the
# box edges slightly so stray text (e.g. elevation labels on the boundary) is
# excluded.
_DANGER_NUM_HALF_W = 32.0
_DANGER_NUM_HALF_H = 25.0


def find_danger_box_centre(
    page: "pdfplumber.page.Page",
) -> tuple[float, float] | None:
    """Locate the danger-number display box on page 1 and return its centre.

    The BRA PDF has a bordered rectangle (stroke-only path, 6 control points)
    that contains the current danger-level number(s).  Its position is stable
    across the two fixture massifs (CHABLAIS and MONT-BLANC) but may drift in
    future templates.  This function searches for the rectangle via
    ``page.curves`` and returns the (cx, cy) of the first match.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        ``(centre_x, centre_y)`` of the danger box, or ``None`` if it cannot
        be located.

    """
    candidates = []
    for curve in page.curves:
        x0 = float(curve.get("x0", 0))
        x1 = float(curve.get("x1", 0))
        top = float(curve.get("top", 0))
        bottom = float(curve.get("bottom", 0))

        # Must fall within the search region.
        if not (
            _DANGER_BOX_SEARCH_X0 <= x0 <= _DANGER_BOX_SEARCH_X1
            and _DANGER_BOX_SEARCH_TOP <= top <= _DANGER_BOX_SEARCH_BOTTOM
        ):
            continue

        w = x1 - x0
        h = bottom - top
        if w < _DANGER_BOX_MIN_WIDTH or h < _DANGER_BOX_MIN_HEIGHT:
            continue

        # The box is stroke-only (not filled) and has a rectangular path (~6 pts).
        is_stroke = bool(curve.get("stroke"))
        is_filled = bool(curve.get("fill"))
        npts = len(curve.get("path", []))
        if not is_stroke or is_filled or npts > 10:
            continue

        candidates.append(((x0 + x1) / 2.0, (top + bottom) / 2.0))

    if not candidates:
        return None

    # If multiple candidates, return the one whose centre is closest to the
    # historical known position (136.9, 152.4).
    _KNOWN_CX, _KNOWN_CY = 136.9, 152.4
    candidates.sort(key=lambda c: (c[0] - _KNOWN_CX) ** 2 + (c[1] - _KNOWN_CY) ** 2)
    return candidates[0]


def danger_numbers_bbox(
    page: "pdfplumber.page.Page",
) -> tuple[float, float, float, float]:
    """Return the (x0, top, x1, bottom) bbox for danger-number extraction.

    Attempts to anchor the bbox to the danger-number display box via
    ``find_danger_box_centre()``.  Falls back to ``DANGER_NUMBERS_FALLBACK_BBOX``
    and logs a warning if the box cannot be found.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        ``(x0, top, x1, bottom)`` tuple of floats.

    """
    centre = find_danger_box_centre(page)
    if centre is None:
        logger.warning(
            "Danger-number box not found via graphics; falling back to hardcoded "
            "bbox %s.  Future template drift may cause incorrect danger ratings.",
            DANGER_NUMBERS_FALLBACK_BBOX,
        )
        return DANGER_NUMBERS_FALLBACK_BBOX

    cx, cy = centre
    return (
        cx - _DANGER_NUM_HALF_W,
        cy - _DANGER_NUM_HALF_H,
        cx + _DANGER_NUM_HALF_W,
        cy + _DANGER_NUM_HALF_H,
    )


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


def find_heading_y(
    page: "pdfplumber.page.Page",
    needle: str,
    *,
    after: float = 0.0,
) -> float | None:
    """Return the top y-coordinate of the first line containing ``needle``.

    The BRA layout is **not** vertically fixed: the stability section grows with
    the volume of prose, so the "Qualité de la neige" heading that follows it
    has been observed at y=364, y=399 and y=401 across three bulletins.  Fixed
    y-bands therefore truncate long bulletins, which is half of why 63% of
    archived comment lines were cut (SNOW-559 — the other half was the
    ``crop_left`` column clip).  Callers use this to derive section bounds from
    the headings themselves.

    Args:
        page: The pdfplumber page object.
        needle: Case-insensitive text to look for within a line.
        after: Ignore matches at or above this y-coordinate.  Lets a caller
            skip an earlier occurrence of a repeated phrase.

    Returns:
        The ``top`` coordinate of the matching line, or ``None`` if no line
        below ``after`` contains ``needle``.

    """
    lowered = needle.lower()
    # Group words onto lines by rounded y — pdfplumber reports per-word
    # coordinates that vary by a fraction of a point within a line — but keep
    # the exact minimum top per line, because callers use the returned value as
    # a crop boundary.  Rounding 398.9 up to 399.0 leaves the heading inside a
    # crop that ends there, leaking it into the extracted prose.
    lines: dict[int, list[str]] = {}
    tops: dict[int, float] = {}
    for word in page.extract_words():
        top = float(word["top"])
        if top <= after:
            continue
        key = round(top)
        lines.setdefault(key, []).append(str(word["text"]))
        tops[key] = min(tops.get(key, top), top)
    for key in sorted(lines):
        if lowered in " ".join(lines[key]).lower():
            return tops[key]
    return None


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
