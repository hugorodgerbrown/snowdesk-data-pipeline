# mf_bra_to_caaml.py — Stage 5 of the Météo-France BRA backfill pipeline.
#
# Parses BRA PDF files and emits one NDJSON record per bulletin in CAAML 6.0
# GeoJSON Feature format.  Supports a --dry-run flag that prints a field-
# coverage report instead of writing the NDJSON file.
#
# Known limitations (tracked as follow-ups):
#   - Per-day historical danger ratings on page 2 are not parsed; only the
#     current-day rating is extracted from page 1.
#   - Snow-depth/cover series and fresh-snow series on page 2 are partially
#     extracted but may miss values when text rendering is fragmented.
#   - Trailing None in last-day fresh snow: when the value is 0 the chart
#     sometimes omits the label entirely.  The missing value is left as None
#     rather than assumed to be 0.
#   - The SAT→problem-type mapping is in _sat_mapping.py; see that module's
#     docstring for citation details (SNOW-258).
#   - Page 2 position-based chart parsing is sensitive to layout drift across
#     massifs and seasons.  The --dry-run report helps spot regressions.
#   - Wind direction is not present in the PDF (only wind speed is given).
#   - Avalanche problem elevation/aspect detail is extracted from free text;
#     structured parsing is incomplete for all edge cases.
"""Stage 5 — Parse BRA PDFs and emit CAAML 6.0 NDJSON."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

import pdfplumber
from _caaml import build_envelope
from _fr_prose import (
    extract_aspects,
    extract_avalanche_size,
    extract_elevation,
    extract_frequency,
)
from _massifs import slugify
from _pdf_extract import (
    BAND_DANGER,
    BAND_PAGE2_FRESH_SNOW,
    BAND_PAGE2_FRESH_SNOW_XAXIS,
    BAND_PAGE2_SNOW_LINE,
    BAND_PAGE2_WIND_HISTORY,
    BAND_STABILITY,
    BAND_WEATHER_TEXT,
    BAND_WIND_TABLE,
    PAGE2_SNOW_LINE_NORD_X_END,
    PAGE2_SNOW_LINE_NORD_X_START,
    PAGE2_SNOW_LINE_SUD_X_END,
    PAGE2_SNOW_LINE_SUD_X_START,
    crop_full_width,
    crop_left,
    crop_right,
    danger_numbers_bbox,
    extract_text_strip,
    extract_words_in_region,
)
from _sat_mapping import sat_to_problem_type

from bulletins.services.meteofrance_translator import format_comment_as_html

logger = logging.getLogger(__name__)

# Danger level name → integer mapping (EAWS scale 1–5, 0 = no rating).
DANGER_LEVELS: dict[str, int] = {
    "faible": 1,
    "limité": 2,
    "limite": 2,
    "marqué": 3,
    "marque": 3,
    "fort": 4,
    "très fort": 5,
    "tres fort": 5,
}

# French month abbreviations → month number.
FRENCH_MONTHS: dict[str, int] = {
    "jan": 1,
    "fév": 2,
    "fev": 2,
    "mar": 3,
    "avr": 4,
    "mai": 5,
    "jun": 6,
    "jui": 6,
    "jul": 7,
    "aoû": 8,
    "aou": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "déc": 12,
    "dec": 12,
}


# ---------------------------------------------------------------------------
# Danger rating extraction
# ---------------------------------------------------------------------------


def _parse_single_danger_rating(danger_text: str) -> int | None:
    """Parse a single-line danger rating from the 'Indice de risque X' line.

    Args:
        danger_text: Full text from the danger area containing 'Indice de risque'.

    Returns:
        Integer danger level (1–5), or None if not found.

    """
    match = re.search(r"indice de risque\s+(.+?)[\.\n]", danger_text, re.IGNORECASE)
    if not match:
        return None
    label = match.group(1).strip().lower()
    return DANGER_LEVELS.get(label)


def _find_danger_numbers_in_area(
    page: "pdfplumber.page.Page",
) -> list[dict[str, object]]:
    """Find numeric tokens that represent danger ratings in the danger-number box.

    The danger number(s) are rendered inside a bordered rectangle on page 1.
    The search bbox is anchored to that rectangle via
    ``danger_numbers_bbox()`` (which reads page graphics) so the extraction
    remains correct even if the box shifts by ±20 points from its calibrated
    position.  If the rectangle cannot be located the hardcoded fallback bbox
    ``(110, 135, 170, 185)`` is used and a warning is logged.

    Args:
        page: The page object (page 1 of the BRA PDF).

    Returns:
        List of word dicts for single-digit numeric tokens in the danger zone.

    """
    x0, top, x1, bottom = danger_numbers_bbox(page)
    return [
        w
        for w in extract_words_in_region(page, x0, top, x1, bottom)
        if re.match(r"^\d$", str(w["text"]))
    ]


def extract_danger_ratings(page: "pdfplumber.page.Page") -> list[dict[str, object]]:
    """Extract dangerRatings from page 1 of a BRA PDF.

    Handles both single-level ratings and split-elevation ratings (where the
    bulletin shows different danger levels above and below an elevation threshold).

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        List of dangerRating dicts in CAAML 6.0 format.  Each dict has keys:
        ``mainValue`` (danger level label), ``elevation``, and ``valid``.

    """
    danger_region = crop_full_width(page, *BAND_DANGER)
    danger_text = extract_text_strip(danger_region)

    # Look for the elevation split marker (e.g. "3600m" between N and S markers).
    elev_match = re.search(r"(\d{3,4})\s*m\b", danger_text)
    danger_numbers = _find_danger_numbers_in_area(page)

    if not danger_numbers:
        # Fallback: parse from text.
        level = _parse_single_danger_rating(danger_text)
        if level is None:
            return []
        return [
            {
                "mainValue": _level_to_label(level),
                "elevation": {"lowerBound": None, "upperBound": None},
                "valid": True,
            }
        ]

    if len(danger_numbers) == 1 and not elev_match:
        # Single-level rating.
        level = int(str(danger_numbers[0]["text"]))
        return [
            {
                "mainValue": _level_to_label(level),
                "elevation": {"lowerBound": None, "upperBound": None},
                "valid": True,
            }
        ]

    # Split-elevation rating.  The PDF shows above elevation: danger N, below: danger M.
    # Numbers sorted top-to-bottom: higher y = lower on page = lower elevation band.
    if elev_match:
        elev_m = int(elev_match.group(1))
    else:
        elev_m = None

    # Sort by vertical position: smaller top = higher on page = above elevation.
    sorted_nums = sorted(danger_numbers, key=lambda w: float(w["top"]))

    ratings = []
    if len(sorted_nums) >= 1:
        upper_level = int(str(sorted_nums[0]["text"]))
        ratings.append(
            {
                "mainValue": _level_to_label(upper_level),
                "elevation": {
                    "lowerBound": elev_m,
                    "upperBound": None,
                },
                "valid": True,
            }
        )
    if len(sorted_nums) >= 2:
        # Take the leftmost (smallest x0) of the lower-band numbers as the
        # primary current rating.  The rightmost may indicate "evolving to"
        # (e.g. "1 2" means currently 1, evolving to 2).
        lower_band_nums = sorted_nums[1:]
        leftmost = min(lower_band_nums, key=lambda w: float(w["x0"]))
        lower_level = int(str(leftmost["text"]))
        ratings.append(
            {
                "mainValue": _level_to_label(lower_level),
                "elevation": {
                    "lowerBound": None,
                    "upperBound": elev_m,
                },
                "valid": True,
            }
        )
    return ratings


def _level_to_label(level: int) -> str:
    """Convert an integer danger level to an EAWS label string.

    Args:
        level: Integer 1–5 (or 0 for no rating).

    Returns:
        EAWS label string (e.g. ``"low"``, ``"moderate"``).

    """
    mapping = {
        0: "no_rating",
        1: "low",
        2: "moderate",
        3: "considerable",
        4: "high",
        5: "very_high",
    }
    return mapping.get(level, "no_rating")


# ---------------------------------------------------------------------------
# Highlights / bulletin title extraction
# ---------------------------------------------------------------------------


def _is_headline_line(stripped: str) -> bool:
    """Return True if ``stripped`` looks like a bulletin headline.

    A headline is either (a) all-uppercase (French accented capitals included) or
    (b) sentence-case ending with a period.  Single-character-token lines (compass
    markers such as "O E", "N S") and pure-digit lines ("1 2") are rejected.

    Args:
        stripped: A stripped text line.

    Returns:
        True if the line is a valid headline candidate.

    """
    tokens = stripped.split()
    # Require at least two tokens so lone compass markers ("N") are excluded.
    if len(tokens) < 2:
        return False
    # Skip lines where every token is a single character (e.g. "O E", "N S").
    if all(len(t) == 1 for t in tokens):
        return False
    # Skip lines where every token is a single digit (e.g. "1 2").
    if all(re.match(r"^\d$", t) for t in tokens):
        return False
    # All-caps line (French accented capitals included).
    if re.match(r"^[A-ZÀÂÄÉÈÊËÎÏÔÙÛÜÇ\s''\-,À-ÖÀ-ÖØ-Þ]+$", stripped):
        return True
    # Sentence-case headline ending with a period.
    return bool(stripped.endswith(".") and stripped[0].isupper())


def extract_highlights(page: "pdfplumber.page.Page") -> str:
    """Extract the bulletin headline from page 1 using positional detection.

    The headline is the first substantive line that appears immediately after the
    validity-date line ("Estimation des risques pour le : ...").  It is identified
    by ``_is_headline_line()``: all-uppercase or sentence-case ending in a period,
    with at least two tokens and no pure compass-marker content.

    This positional approach is more robust than prefix-based filtering: it does not
    rely on the headline being preceded by specific tokens (``Ind``, ``Dep``, etc.)
    that may vary across massifs or template revisions.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        The headline string (e.g. ``"MANTEAU NEIGEUX STABLE."``), or ``""`` if
        not found.

    """
    region = crop_full_width(page, *BAND_DANGER)
    text = extract_text_strip(region)

    # Locate the validity-date line; the headline immediately follows it.
    after_date_line = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^Estimation des risques pour le", stripped, re.IGNORECASE):
            after_date_line = True
            continue
        if after_date_line and _is_headline_line(stripped):
            return stripped

    return ""


# ---------------------------------------------------------------------------
# Avalanche activity / snowpack narrative extraction
# ---------------------------------------------------------------------------


def extract_avalanche_activity(page: "pdfplumber.page.Page") -> dict[str, str]:
    """Extract avalanche activity descriptions from the stability section.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        Dict with keys ``"spontaneous"`` and ``"triggered"``, each containing
        the relevant prose text.

    """
    left_region = crop_left(page, *BAND_STABILITY)
    text = extract_text_strip(left_region)

    spontaneous = ""
    triggered = ""

    # Split on "Déclenchements provoqués" to get the two halves.
    _triggered_pat = r"Déclenchements?\s+provoqués?\s*:"
    parts = re.split(_triggered_pat, text, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        before = parts[0]
        triggered_text = parts[1].strip()
        # Extract spontaneous from before the split.
        _spontaneous_pat = r"Départs?\s+spontanés?\s*:"
        sp_parts = re.split(_spontaneous_pat, before, maxsplit=1, flags=re.IGNORECASE)
        if len(sp_parts) == 2:
            spontaneous = sp_parts[1].strip()
        triggered = triggered_text

    return {"spontaneous": spontaneous, "triggered": triggered}


def extract_snowpack_comment(page: "pdfplumber.page.Page") -> str:
    """Extract the snowpack quality narrative from the left column.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        Prose text describing snow quality conditions, or ``""`` if not found.

    """
    left_region = crop_left(page, *BAND_STABILITY)
    text = extract_text_strip(left_region)
    # The snowpack section follows "Stabilité du manteau neigeux".
    _snowpack_pat = r"Stabilité du manteau neigeux\s*(.+?)(?=Situation avalancheuse|$)"
    match = re.search(_snowpack_pat, text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# SAT (Situation Avalancheuse Typique) extraction
# ---------------------------------------------------------------------------


def extract_sat_labels(page: "pdfplumber.page.Page") -> list[str]:
    """Extract SAT labels from page 1.

    The SAT line reads: "Situation avalancheuse typique : label1 label2 ..."
    Multiple labels indicate a mixed situation.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        List of raw SAT label strings.

    """
    region = crop_full_width(page, *BAND_STABILITY)
    text = extract_text_strip(region)
    match = re.search(
        r"Situation avalancheuse typique\s*:\s*(.+?)(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return []
    raw = match.group(1).strip()
    # Multiple labels are separated by spaces; we split on known label boundaries.
    # Known pairs: "neige ventée neige humide" → ["neige ventée", "neige humide"]
    # Known labels start with "neige", "couche", "plaques", "glissement", etc.
    label_starts = [
        "neige fraîche",
        "neige fraiche",
        "neige récente",
        "neige recente",
        "neige ventée",
        "neige ventee",
        "neige humide",
        "plaques à vent",
        "plaques a vent",
        "couche fragile persistante",
        "couche fragile enfouie",
        "couche fragile",
        "glissement",
        "redoux",
        "fonte",
    ]
    raw_lower = raw.lower()
    found: list[str] = []
    for label in label_starts:
        if label in raw_lower:
            found.append(label)
    if not found:
        # Fall back to returning the raw text as a single label.
        return [raw]
    return found


def extract_avalanche_problems(page: "pdfplumber.page.Page") -> list[dict[str, object]]:
    """Extract avalanche problems from SAT labels and stability prose.

    Each SAT label maps to a CAAML avalanche problem type.  Aspect,
    elevation, size, and frequency are extracted from the combined stability
    narrative (spontaneous + triggered activity text) and applied to every
    problem equally — the BRA prose does not label paragraphs per problem.
    When a bulletin has two problems, both receive the same prose-derived
    fields; this is the correct best-effort interpretation of a single
    undifferentiated prose block.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        List of avalancheProblem dicts in CAAML 6.0 format.  Each dict has
        keys ``problemType``, ``satLabel``, ``order``, ``aspects``,
        ``elevation``, ``avalancheSize``, and ``frequency``.

    """
    labels = extract_sat_labels(page)
    activity = extract_avalanche_activity(page)
    prose = (activity["spontaneous"] + "\n" + activity["triggered"]).strip()

    aspects = extract_aspects(prose)
    elevation = extract_elevation(prose) or {}
    size = extract_avalanche_size(prose)
    frequency = extract_frequency(prose)

    problems: list[dict[str, object]] = []
    seen_types: set[str | None] = set()
    for i, label in enumerate(labels):
        problem_type = sat_to_problem_type(label)
        if problem_type in seen_types:
            continue
        seen_types.add(problem_type)
        # sat_to_problem_type returns None for unknown labels (not in the
        # mapping table) and for labels with no EAWS equivalent.  Both
        # cases are preserved as a distinct CAAML problemType of
        # "no_distinct_avalanche_problem" here — we do NOT silently default
        # arbitrary unknown labels to no_distinct_avalanche_problem without
        # a log warning so the caller has visibility of unrecognised labels.
        if problem_type is None:
            logger.warning(
                "SAT label %r has no EAWS problem-type mapping; "
                "using no_distinct_avalanche_problem",
                label,
            )
            problem_type = "no_distinct_avalanche_problem"
        problem: dict[str, object] = {
            "problemType": problem_type,
            "satLabel": label,
            "order": i + 1,
            "aspects": aspects,
            "elevation": elevation,
        }
        if size is not None:
            problem["avalancheSize"] = size
        if frequency is not None:
            problem["frequency"] = frequency
        problems.append(problem)
    return problems


# ---------------------------------------------------------------------------
# Weather / wind extraction
# ---------------------------------------------------------------------------


def extract_weather_text(page: "pdfplumber.page.Page") -> str:
    """Extract the weather summary text from the right column.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        Weather prose text, or ``""`` if not found.

    """
    region = crop_right(page, *BAND_WEATHER_TEXT)
    return extract_text_strip(region)


def _parse_wind_row(line: str) -> tuple[str, list[int | None]] | None:
    """Parse a single wind table row into (altitude_label, speeds).

    Speeds are [speed_nuit, matin, apm, soir].

    Args:
        line: A single text line from the wind table area, e.g.
            ``"Vent 2000 m 5 km/h 0 km/h 0 km/h 5 km/h"``.

    Returns:
        A tuple of ``(altitude_label, speeds)`` where speeds is a list of 4
        integer values (or None for missing), or None if the line doesn't match.

    """
    # Match: "Vent XXXX m  N km/h  M km/h  P km/h  Q km/h"
    match = re.match(
        r"Vent\s+(\d{3,5})\s*m\s+([\d.]+)\s*km/h\s+([\d.]+)\s*km/h\s+([\d.]+)\s*km/h\s+([\d.]+)\s*km/h",
        line.strip(),
        re.IGNORECASE,
    )
    if not match:
        return None
    altitude = match.group(1)
    speeds: list[int | None] = [int(float(match.group(i))) for i in range(2, 6)]
    return f"{altitude}m", speeds


def extract_wind_table(page: "pdfplumber.page.Page") -> list[dict[str, object]]:
    """Extract the 4-slot wind speed table (nuit/matin/après-midi/soir).

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        List of wind entry dicts with keys ``altitude`` and ``speeds``
        (list of 4 int/None values for nuit/matin/après-midi/soir).

    """
    region = crop_right(page, *BAND_WIND_TABLE)
    text = extract_text_strip(region)
    entries: list[dict[str, object]] = []
    for line in text.splitlines():
        result = _parse_wind_row(line)
        if result:
            altitude_label, speeds = result
            entries.append({"altitude": altitude_label, "speeds": speeds})
    return entries


def extract_isotherm(page: "pdfplumber.page.Page") -> list[int | None]:
    """Extract the 4-slot isotherm 0°C altitude values.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        List of 4 integer altitude values (nuit/matin/après-midi/soir),
        with None for missing values.

    """
    region = crop_right(page, *BAND_WIND_TABLE)
    text = extract_text_strip(region)
    # The isotherm line reads: "Iso 0 °C 4 000 m 4 000 m 4 000 m 4 100 m"
    # Altitudes may be written with a space thousands separator (e.g. "4 000").
    match = re.search(
        r"Iso\s*0\s*°C\s+(.+?)(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return []
    values_str = match.group(1)
    # Extract all "N NNN m" or "NNNN m" patterns (handle space thousands separator).
    values: list[int | None] = []
    for m in re.finditer(r"(\d[\d\s]{2,4})\s*m\b", values_str):
        # Strip internal spaces to parse "4 000" as 4000.
        alt = int(m.group(1).replace(" ", ""))
        values.append(alt)
    return values[:4]  # Cap at 4 time slots.


# ---------------------------------------------------------------------------
# Page-2 historical data extraction
# ---------------------------------------------------------------------------


def extract_page2_wind_history(
    page2: "pdfplumber.page.Page",
) -> dict[str, list[int]]:
    """Extract the 7-day wind-history grid from page 2 of a BRA PDF.

    Page 2 contains a 28-cell wind-speed grid (7 days × 4 time slots: 03h,
    09h, 15h, 21h) for two altitude levels.  The values are listed in a
    single text row per altitude, immediately followed by an "à Xm" label on
    the next line.

    Units: km/h (integer).

    Args:
        page2: pdfplumber page object for page 2.

    Returns:
        Dict keyed by altitude label (e.g. ``"2500m"``, ``"2000m"``,
        ``"4000m"``), each mapping to a list of 28 integer wind speeds
        (oldest day 03h first, newest day 21h last).  Returns ``{}`` if no
        wind rows are found.

    """
    region = crop_full_width(page2, *BAND_PAGE2_WIND_HISTORY)
    text = extract_text_strip(region)
    if not text:
        return {}

    result: dict[str, list[int]] = {}
    lines = text.splitlines()
    pending_speeds: list[int] | None = None

    for line in lines:
        stripped = line.strip()
        # Check for altitude label "à Xm" that follows a wind row.
        alt_match = re.match(r"^à\s+(\d{3,5})\s*m\s*$", stripped, re.IGNORECASE)
        if alt_match and pending_speeds is not None:
            alt_label = f"{alt_match.group(1)}m"
            result[alt_label] = pending_speeds
            pending_speeds = None
            continue

        # Check for a wind-data row: "Vent (km/h) N N N N ..."
        wind_match = re.match(r"Vent\s*\(km/h\)\s+(.+)", stripped, re.IGNORECASE)
        if wind_match:
            nums_str = wind_match.group(1)
            speeds = [int(t) for t in nums_str.split() if re.match(r"^\d+$", t)]
            if len(speeds) == 28:
                pending_speeds = speeds
            else:
                logger.warning(
                    "Wind row has %d values (expected 28): %r", len(speeds), stripped
                )
            continue

    return result


def _parse_cm_value_pairs(
    words: list[dict[str, object]],
) -> list[tuple[int, float]]:
    """Scan a list of words for "N cm" pairs and return (value, centre-x) tuples.

    Args:
        words: Word dicts from pdfplumber, sorted left-to-right by x0.

    Returns:
        List of ``(integer_value, label_centre_x)`` tuples.

    """
    result: list[tuple[int, float]] = []
    for i, w in enumerate(words):
        if str(w["text"]) == "cm" and i > 0:
            prev = words[i - 1]
            try:
                val = int(str(prev["text"]))
                label_cx = (float(prev["x0"]) + float(w["x1"])) / 2.0
                result.append((val, label_cx))
            except ValueError:
                pass
    return result


def _nearest_col_idx(cx: float, col_centres: list[float]) -> int:
    """Return the index of the column centre nearest to ``cx``.

    Args:
        cx: X coordinate of the label centre.
        col_centres: Ordered list of column centre x-coordinates.

    Returns:
        Zero-based index into ``col_centres``.

    """
    return min(range(len(col_centres)), key=lambda i: abs(cx - col_centres[i]))


def extract_page2_fresh_snow(
    page2: "pdfplumber.page.Page",
) -> list[int]:
    """Extract the 7-day fresh-snowfall series from page 2.

    The fresh-snow bar chart labels each bar with its daily accumulation in
    centimetres.  Bars with zero snowfall are sometimes unlabelled; the
    extractor treats missing labels as 0 cm.

    Units: cm (integer).

    Args:
        page2: pdfplumber page object for page 2.

    Returns:
        List of 7 integer cm values, one per day (oldest to newest).
        Missing labels are filled with 0.

    """
    # Build day-column centres from the x-axis date labels.
    xaxis_words = extract_words_in_region(
        page2,
        x0=35.0,
        top=BAND_PAGE2_FRESH_SNOW_XAXIS[0],
        x1=float(page2.width),
        bottom=BAND_PAGE2_FRESH_SNOW_XAXIS[1],
    )
    date_cx: list[float] = []
    for w in sorted(xaxis_words, key=lambda w: float(w["x0"])):
        text = str(w["text"])
        if re.match(r"^\d{2}/\d{2}$", text):
            date_cx.append((float(w["x0"]) + float(w["x1"])) / 2.0)

    if len(date_cx) != 7:
        logger.warning(
            "Fresh-snow x-axis: expected 7 date labels, found %d", len(date_cx)
        )
        return []

    # Collect (value, label_cx) pairs from the chart area.
    chart_words = extract_words_in_region(
        page2,
        x0=35.0,
        top=BAND_PAGE2_FRESH_SNOW[0],
        x1=float(page2.width),
        bottom=BAND_PAGE2_FRESH_SNOW[1],
    )
    value_pairs = _parse_cm_value_pairs(
        sorted(chart_words, key=lambda w: float(w["x0"]))
    )

    # Assign each labeled value to its nearest day column.
    day_values: dict[int, int] = {}
    for val, label_cx in value_pairs:
        day_idx = _nearest_col_idx(label_cx, date_cx)
        if day_idx not in day_values:
            day_values[day_idx] = val

    # Fill all 7 days, defaulting missing labels to 0.
    return [day_values.get(i, 0) for i in range(7)]


def _snow_line_col_centres(x_start: float, x_end: float, n: int = 7) -> list[float]:
    """Return n evenly-spaced column centres within the given x range.

    Args:
        x_start: Left edge of the chart plot area.
        x_end: Right edge of the chart plot area.
        n: Number of day columns (default 7).

    Returns:
        List of n column-centre x-coordinates.

    """
    col_width = (x_end - x_start) / n
    return [x_start + col_width * (i + 0.5) for i in range(n)]


def _assign_snow_line_to_cols(
    values_cx: list[tuple[int, float]],
    col_centres: list[float],
) -> list[int | None]:
    """Assign elevation labels to the nearest column, returning min per column.

    When multiple labels fall in the same column (e.g. upper and lower snow
    cover bounds), the minimum (lowest elevation with snow) is kept.

    Args:
        values_cx: List of ``(elevation_m, label_centre_x)`` pairs.
        col_centres: Ordered list of day-column centre x-coordinates.

    Returns:
        List of ``len(col_centres)`` items: integer metres or ``None``.

    """
    col_data: dict[int, list[int]] = {i: [] for i in range(len(col_centres))}
    for val, cx in values_cx:
        col_idx = _nearest_col_idx(cx, col_centres)
        col_data[col_idx].append(val)
    return [min(v) if v else None for v in col_data.values()]


def extract_page2_snow_line(
    page2: "pdfplumber.page.Page",
) -> dict[str, list[int | None]]:
    """Extract the 7-day snow-line elevation series from page 2.

    Page 2 carries two side-by-side line charts showing the lowest elevation
    with continuous snow cover ("Limite de l'enneigement") for north-facing
    (Versant Nord) and south-facing (Versant Sud) aspects.

    Each chart labels data points with their altitude in metres.  When a day's
    value equals the previous day (i.e. the line is flat), the label is often
    omitted; such days are returned as ``None``.

    Units: metres (integer).

    Args:
        page2: pdfplumber page object for page 2.

    Returns:
        Dict with keys ``"north"`` and ``"south"``, each a list of 7 values
        (``int`` metres or ``None`` when unlabelled).  Day 0 = oldest,
        day 6 = most recent.

    """
    # Collect all numeric elevation labels in the snow-line chart band.
    all_words = extract_words_in_region(
        page2,
        x0=PAGE2_SNOW_LINE_NORD_X_START,
        top=BAND_PAGE2_SNOW_LINE[0],
        x1=PAGE2_SNOW_LINE_SUD_X_END,
        bottom=BAND_PAGE2_SNOW_LINE[1],
    )

    nord_pairs: list[tuple[int, float]] = []
    sud_pairs: list[tuple[int, float]] = []

    for w in all_words:
        try:
            val = int(str(w["text"]))
        except ValueError:
            continue
        if val < 100:
            continue  # Skip zero / tiny y-axis ticks.
        cx = (float(w["x0"]) + float(w["x1"])) / 2.0
        if PAGE2_SNOW_LINE_NORD_X_START <= cx <= PAGE2_SNOW_LINE_NORD_X_END:
            nord_pairs.append((val, cx))
        elif PAGE2_SNOW_LINE_SUD_X_START <= cx <= PAGE2_SNOW_LINE_SUD_X_END:
            sud_pairs.append((val, cx))

    nord_cols = _snow_line_col_centres(
        PAGE2_SNOW_LINE_NORD_X_START, PAGE2_SNOW_LINE_NORD_X_END
    )
    sud_cols = _snow_line_col_centres(
        PAGE2_SNOW_LINE_SUD_X_START, PAGE2_SNOW_LINE_SUD_X_END
    )

    return {
        "north": _assign_snow_line_to_cols(nord_pairs, nord_cols),
        "south": _assign_snow_line_to_cols(sud_pairs, sud_cols),
    }


# ---------------------------------------------------------------------------
# Tendency extraction
# ---------------------------------------------------------------------------

TENDENCY_LABELS = {
    "stationnaire": "steady",
    "hausse": "increasing",
    "baisse": "decreasing",
    "en baisse": "decreasing",
    "en hausse": "increasing",
}


def extract_tendency(page: "pdfplumber.page.Page") -> dict[str, object]:
    """Extract the tendency (next-day outlook) from page 1.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        Dict with keys ``"dangerRating"`` (int danger level) and ``"comment"``
        (prose text), or ``{}`` if not found.

    """
    W = float(page.width)
    region = page.crop((W * 0.75, 500, W, 680))
    text = extract_text_strip(region)

    # Find the "Indice de risque X" line in the tendency block.
    danger_match = re.search(r"Indice de risque\s+(\S+)", text, re.IGNORECASE)
    tendency_label = None
    for key, value in TENDENCY_LABELS.items():
        if key in text.lower():
            tendency_label = value
            break

    if not danger_match:
        return {}

    label = danger_match.group(1).strip().lower().rstrip(".")
    level = DANGER_LEVELS.get(label, 0)

    return {
        "dangerRating": _level_to_label(level),
        "tendencyType": tendency_label or "steady",
        "comment": format_comment_as_html(text),
    }


# ---------------------------------------------------------------------------
# Bulletin date extraction
# ---------------------------------------------------------------------------


def _infer_date_with_year(month: int, day: int, today: date) -> date | None:
    """Infer the full calendar date from a month/day pair and a reference date.

    Assumes the current year, then steps back one year if the resulting date
    is 180 or more days in the future relative to ``today``.  This handles the
    season boundary: when today is 2026-05-21 a "17 NOV" in-PDF date would
    produce 2026-11-17 (180 days ahead), which belongs to the previous season
    and is corrected to 2025-11-17.  The threshold is ``>= 180`` — not
    ``> 180`` — so that the exact boundary day is also caught.

    Args:
        month: Calendar month (1–12).
        day: Calendar day (1–31).
        today: The reference date (normally ``date.today()``).

    Returns:
        The inferred :class:`datetime.date`, or ``None`` if the combination of
        month and day is invalid (e.g. 30 February).

    """
    try:
        candidate = date(today.year, month, day)
        if (candidate - today).days >= 180:
            candidate = date(today.year - 1, month, day)
        return candidate
    except ValueError:
        return None


def extract_bulletin_date(page: "pdfplumber.page.Page") -> date | None:
    """Extract the validity date from the 'Estimation des risques pour le' line.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        The bulletin validity date, or None if parsing fails.

    """
    region = crop_full_width(page, *BAND_DANGER)
    text = extract_text_strip(region)
    # "Estimation des risques pour le : VENDREDI 22 MAI"
    match = re.search(
        r"Estimation des risques pour le\s*:\s*\w+\s+(\d+)\s+(\w+)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    day = int(match.group(1))
    month_str = match.group(2).lower()[:3]
    month = FRENCH_MONTHS.get(month_str)
    if month is None:
        return None
    today = date.today()
    return _infer_date_with_year(month, day, today)


def extract_massif_name(page: "pdfplumber.page.Page") -> str:
    """Extract the massif name from the 'MASSIF : X' header line.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        Massif name as it appears in the PDF (title case), or ``""`` if not found.

    """
    region = crop_full_width(page, 40, 80)
    text = extract_text_strip(region)
    match = re.search(r"MASSIF\s*:\s*(.+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# Full-bulletin parser
# ---------------------------------------------------------------------------


def parse_pdf(pdf_path: Path) -> dict[str, object] | None:
    """Parse a single BRA PDF and return a CAAML 6.0 Feature dict.

    Args:
        pdf_path: Path to the BRA PDF file.

    Returns:
        A GeoJSON Feature dict, or None if the PDF could not be parsed.

    """
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                logger.warning("Empty PDF: %s", pdf_path)
                return None
            page1 = pdf.pages[0]

            massif_raw = extract_massif_name(page1)
            massif_slug = (
                slugify(massif_raw) if massif_raw else _slug_from_filename(pdf_path)
            )

            bulletin_date = extract_bulletin_date(page1)
            if bulletin_date is None:
                logger.warning("Could not extract bulletin date from %s", pdf_path)
                bulletin_date = _date_from_filename(pdf_path)

            if bulletin_date is None:
                logger.error(
                    "Cannot determine bulletin date for %s; skipping", pdf_path
                )
                return None

            danger_ratings = extract_danger_ratings(page1)
            highlights = extract_highlights(page1)
            avalanche_activity = extract_avalanche_activity(page1)
            snowpack_comment = extract_snowpack_comment(page1)
            sat_labels = extract_sat_labels(page1)
            avalanche_problems = extract_avalanche_problems(page1)
            weather_text = extract_weather_text(page1)
            wind_table = extract_wind_table(page1)
            isotherm = extract_isotherm(page1)
            tendency = extract_tendency(page1)

            # Page 2: 7-day historical data (wind speed grid, fresh snow, snow line).
            # Single-page PDFs emit an empty dict so downstream code can
            # always access historical without a key-existence check.
            if len(pdf.pages) > 1:
                page2 = pdf.pages[1]
                try:
                    historical: dict[str, object] = {
                        "wind": extract_page2_wind_history(page2),
                        "freshSnow": extract_page2_fresh_snow(page2),
                        "snowLine": extract_page2_snow_line(page2),
                    }
                except Exception as exc2:  # noqa: BLE001
                    logger.warning(
                        "Page-2 extraction failed for %s: %s", pdf_path, exc2
                    )
                    historical = {}
            else:
                historical = {}

    except Exception as exc:  # noqa: BLE001 — pdfplumber raises a wide variety
        # of library-internal exceptions (PDFSyntaxError, struct.error, etc.)
        # depending on the PDF.  Catching broadly here keeps the batch parser
        # moving; the error is logged and the caller receives None.
        logger.error("Failed to parse %s: %s", pdf_path, exc)
        return None

    # Build the concatenated avalanche activity comment (backward-compat field).
    _combined_activity = (
        avalanche_activity["spontaneous"] + "\n" + avalanche_activity["triggered"]
    ).strip()

    properties: dict[str, object] = {
        "dangerRatings": danger_ratings,
        "highlights": highlights,
        "avalancheActivity": {
            "highlights": highlights,
            "comment": format_comment_as_html(_combined_activity),
        },
        "snowpackStructure": {
            "comment": format_comment_as_html(snowpack_comment),
        },
        "avalancheProblems": avalanche_problems,
        "tendency": [tendency] if tendency else [],
        "weather": {
            "comment": weather_text,
            "isotherm0": isotherm,
            "wind": wind_table,
        },
        "customData": {
            "MF": {
                "massif": massif_slug,
                "date": bulletin_date.isoformat(),
                "typicalAvalancheSituations": sat_labels,
                "source_file": pdf_path.name,
                "historical": historical,
                # Preserve the spontaneous/triggered split (SNOW-257).
                # The concatenated ``avalancheActivity.comment`` is kept for
                # backward compatibility; these two keys carry the split form.
                "spontaneous": format_comment_as_html(
                    avalanche_activity["spontaneous"]
                ),
                "triggered": format_comment_as_html(avalanche_activity["triggered"]),
            }
        },
    }

    return build_envelope(
        massif=massif_slug,
        bulletin_date=bulletin_date,
        properties=properties,
    )


def _slug_from_filename(pdf_path: Path) -> str:
    """Derive the massif slug from the PDF filename (BRA.{MASSIF}.*.pdf).

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Massif slug string, or ``"UNKNOWN"`` if the filename doesn't match.

    """
    parts = pdf_path.stem.split(".")
    if len(parts) >= 2:
        return parts[1].upper()
    return "UNKNOWN"


def _date_from_filename(pdf_path: Path) -> date | None:
    """Derive a date from the PDF filename (BRA.{MASSIF}.{YYYYMMDD}*.pdf).

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        Date object, or None if not parseable.

    """
    match = re.search(r"(\d{8})", pdf_path.stem)
    if match:
        date_str = match.group(1)
        try:
            return date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Dry-run field-coverage report
# ---------------------------------------------------------------------------

# The 24 checks used by the field-coverage report.
COVERAGE_CHECKS: list[tuple[str, str]] = [
    ("dangerRatings", "danger ratings present"),
    ("dangerRatings[0].mainValue", "danger rating mainValue"),
    ("highlights", "highlights/headline"),
    ("avalancheActivity.comment", "avalanche activity comment"),
    ("snowpackStructure.comment", "snowpack structure comment"),
    ("avalancheProblems", "avalanche problems list"),
    ("avalancheProblems[0].problemType", "primary problem type"),
    ("weather.comment", "weather comment"),
    ("weather.isotherm0", "isotherm 0°C values"),
    ("weather.wind", "wind table present"),
    ("weather.wind[0].altitude", "wind altitude label"),
    ("weather.wind[0].speeds", "wind speeds array"),
    ("tendency", "tendency block present"),
    ("tendency[0].dangerRating", "tendency danger rating"),
    ("tendency[0].tendencyType", "tendency type"),
    ("customData.MF.massif", "MF massif slug"),
    ("customData.MF.date", "MF date"),
    ("customData.MF.typicalAvalancheSituations", "SAT labels"),
    ("properties.regions", "regions list"),
    ("properties.validTime", "validTime block"),
    ("properties.lang", "language tag"),
    ("customData.MF.source_file", "source filename"),
    ("dangerRatings[0].elevation", "elevation in rating"),
    ("avalancheProblems[0].satLabel", "SAT label in problem"),
    ("avalancheProblems[0].aspects", "aspects in primary problem"),
    ("avalancheProblems[0].elevation", "elevation in primary problem"),
    ("avalancheProblems[0].avalancheSize", "avalanche size in primary problem"),
    ("avalancheProblems[0].frequency", "frequency in primary problem"),
    ("customData.MF.historical", "historical block present"),
    ("customData.MF.historical.wind", "historical wind grid"),
    ("customData.MF.historical.freshSnow", "historical fresh-snow series"),
    ("customData.MF.historical.snowLine", "historical snow-line block"),
    ("customData.MF.historical.snowLine.north", "historical snow-line north"),
    ("customData.MF.historical.snowLine.south", "historical snow-line south"),
    ("customData.MF.spontaneous", "spontaneous avalanche activity (split)"),
    ("customData.MF.triggered", "triggered avalanche activity (split)"),
]


def _resolve_dict_path(obj: dict[str, object], path: str) -> object:
    """Resolve a dot-or-bracket path segment from a dict node.

    Args:
        obj: Dict to look up.
        path: Remaining path string starting with a key name.

    Returns:
        Resolved value, or None if any segment is missing.

    """
    if path in obj:
        return obj[path]
    # Split on first dot or bracket.
    bracket = path.find("[")
    dot = path.find(".")
    if bracket >= 0 and (dot < 0 or bracket < dot):
        key, rest = path[:bracket], path[bracket:]
        return _get_nested(obj.get(key), rest)
    if dot >= 0:
        key, rest = path[:dot], path[dot + 1 :]
        return _get_nested(obj.get(key), rest)
    return None


def _get_nested(obj: object, path: str) -> object:
    """Recursively resolve a dot/index path against obj.

    Args:
        obj: Current node (dict, list, or scalar).
        path: Remaining path string to resolve.

    Returns:
        Resolved value, or None if any segment is missing.

    """
    if not path:
        return obj
    if isinstance(obj, dict):
        return _resolve_dict_path(obj, path)
    if isinstance(obj, list) and path.startswith("["):
        end = path.find("]")
        idx = int(path[1:end])
        rest = path[end + 1 :].lstrip(".")
        if idx < len(obj):
            return _get_nested(obj[idx], rest)
    return None


def _check_field(envelope: dict[str, object], check_path: str) -> bool:
    """Return True if the field at check_path has a non-empty value.

    Supports simple dot-notation and [0] indexing.

    Args:
        envelope: The parsed CAAML Feature dict.
        check_path: Dot-separated path string, e.g. ``"dangerRatings[0].mainValue"``.

    Returns:
        True if the field is present and non-empty.

    """
    props = envelope.get("properties", {})
    if not isinstance(props, dict):
        return False
    # Try against properties first, then root envelope.
    for root in [props, envelope]:
        val = _get_nested(root, check_path)
        if val is not None and val != "" and val != [] and val != {}:
            return True
    return False


def generate_coverage_report(envelopes: list[dict[str, object]]) -> str:
    """Generate an ASCII field-coverage report for a list of parsed bulletins.

    Args:
        envelopes: List of CAAML Feature dicts.

    Returns:
        Multi-line string report.

    """
    if not envelopes:
        return "No bulletins parsed.\n"

    n = len(envelopes)
    lines: list[str] = [f"Field coverage report — {n} bulletin(s)\n", "=" * 60]
    for check_path, label in COVERAGE_CHECKS:
        count = sum(1 for env in envelopes if _check_field(env, check_path))
        pct = count * 100 // n
        bar_len = pct // 5
        bar = "#" * bar_len + "-" * (20 - bar_len)
        lines.append(f"  {label:<40s}  [{bar}] {pct:3d}% ({count}/{n})")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


# Django-style verbosity → stdlib logging level.
_LOG_LEVEL: dict[int, int] = {
    0: logging.WARNING,
    1: logging.INFO,
    2: logging.DEBUG,
}


def _envelope_source_file(envelope: dict[str, object]) -> str | None:
    """Return the source PDF filename recorded in an envelope, if any.

    The source file is written into ``properties.customData.MF.source_file``
    by ``parse_pdf``; this helper is the inverse lookup used for resume
    dedup keys.

    Args:
        envelope: A parsed CAAML GeoJSON Feature dict.

    Returns:
        The ``source_file`` string, or ``None`` if the path is missing.

    """
    props = envelope.get("properties")
    if not isinstance(props, dict):
        return None
    custom = props.get("customData")
    if not isinstance(custom, dict):
        return None
    mf = custom.get("MF")
    if not isinstance(mf, dict):
        return None
    value = mf.get("source_file")
    return value if isinstance(value, str) else None


def load_completed_sources(output_path: Path) -> set[str]:
    """Return the set of PDF filenames already represented in ``output_path``.

    This is the resume primitive: by reading the existing NDJSON output we
    can skip any PDF whose envelope has already been written, making the
    parser idempotent across restarts.

    The function is crash-tolerant: if the file ends in a partial or
    malformed JSON line (e.g. the previous run was killed mid-write), the
    tail is truncated at the last newline that bounds a parseable record so
    the next streamed write resumes from a clean boundary.

    Args:
        output_path: NDJSON file to inspect.  If absent, an empty set is
            returned and the file is left untouched.

    Returns:
        Set of ``source_file`` strings already on disk.

    """
    if not output_path.exists():
        return set()

    completed: set[str] = set()
    last_good_offset = 0
    truncated = False
    with output_path.open("rb") as fh:
        while True:
            line_start = fh.tell()
            raw = fh.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                # Final line was never terminated — definitely a partial
                # write.  Drop everything from the start of this line.
                truncated = True
                break
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "Truncating malformed line in %s at offset %d",
                    output_path,
                    line_start,
                )
                truncated = True
                break
            source = _envelope_source_file(envelope)
            if source:
                completed.add(source)
            last_good_offset = fh.tell()

    if truncated:
        with output_path.open("r+b") as fh:
            fh.truncate(last_good_offset)

    return completed


def _run_dry(pdf_files: list[Path]) -> dict[str, int]:
    """Parse every PDF and emit the coverage report — no NDJSON written.

    Args:
        pdf_files: Sorted list of BRA PDFs in the input directory.

    Returns:
        Dict with counts: ``parsed``, ``failed``, ``skipped`` (always 0
        for dry-run — the coverage report is a full re-sweep).

    """
    envelopes: list[dict[str, object]] = []
    failed = 0
    for pdf_path in pdf_files:
        logger.info("  Parsing %s...", pdf_path.name)
        envelope = parse_pdf(pdf_path)
        if envelope is None:
            failed += 1
            logger.info("  FAILED: %s", pdf_path.name)
        else:
            envelopes.append(envelope)
    logger.info(generate_coverage_report(envelopes))
    return {"parsed": len(envelopes), "failed": failed, "skipped": 0}


def _run_streaming(
    pdf_files: list[Path], output_path: Path, *, resume: bool
) -> dict[str, int]:
    """Stream-parse PDFs, appending one envelope per line to ``output_path``.

    Each successful parse is followed by ``fh.flush()`` so an interrupted
    process leaves a valid NDJSON file on disk for the next run to resume
    from.  When ``resume`` is True the existing file is consulted to skip
    PDFs already represented; when False the file is overwritten.

    Args:
        pdf_files: Sorted list of BRA PDFs to consider.
        output_path: NDJSON file to write.
        resume: If True, append and skip; if False, truncate and re-parse.

    Returns:
        Dict with counts: ``parsed``, ``failed``, ``skipped``.

    """
    if resume:
        completed = load_completed_sources(output_path)
        if completed:
            logger.info(
                "Resuming: %d bulletin(s) already in %s",
                len(completed),
                output_path,
            )
        mode = "a"
    else:
        completed = set()
        mode = "w"

    parsed = 0
    failed = 0
    skipped = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open(mode, encoding="utf-8") as fh:
        for pdf_path in pdf_files:
            if pdf_path.name in completed:
                skipped += 1
                logger.debug("  Skipping %s (already done)", pdf_path.name)
                continue
            logger.info("  Parsing %s...", pdf_path.name)
            envelope = parse_pdf(pdf_path)
            if envelope is None:
                failed += 1
                logger.info("  FAILED: %s", pdf_path.name)
                continue
            fh.write(json.dumps(envelope, ensure_ascii=False) + "\n")
            fh.flush()
            completed.add(pdf_path.name)
            parsed += 1

    logger.info(
        "Done. parsed=%d skipped=%d failed=%d → %s",
        parsed,
        skipped,
        failed,
        output_path,
    )
    return {"parsed": parsed, "failed": failed, "skipped": skipped}


def run(
    *,
    input_dir: Path,
    output_path: Path,
    dry_run: bool,
    resume: bool = True,
) -> dict[str, int]:
    """Run the PDF-to-CAAML parser over all PDFs in ``input_dir``.

    Envelopes are streamed to ``output_path`` one line at a time, with an
    explicit ``flush()`` after each write so an interrupted run leaves a
    valid NDJSON file behind.  Re-running the parser against the same
    output is idempotent: any PDF whose ``source_file`` already appears in
    the file is skipped.  Pass ``resume=False`` to force a fresh write
    (the existing file is overwritten).

    ``dry_run=True`` always re-parses every PDF in the directory regardless
    of ``resume``, because the coverage report needs a full sweep to be
    meaningful and no output file is touched.

    Logging is the caller's responsibility — configure ``logging`` at the
    desired level before calling this function (``main`` does so based on
    the ``--verbosity`` CLI flag).

    Args:
        input_dir: Directory containing BRA PDF files.
        output_path: Path to write the NDJSON output.
        dry_run: If True, print coverage report instead of writing NDJSON.
        resume: If True (default), skip PDFs whose envelopes are already
            in ``output_path`` and append new envelopes.  If False,
            truncate ``output_path`` and re-parse everything.

    Returns:
        Dict with counts: ``parsed`` (newly written this run), ``failed``
        (PDFs that returned ``None`` from ``parse_pdf``), ``skipped``
        (PDFs already present in the output and not re-parsed).

    """
    pdf_files = sorted(input_dir.glob("BRA.*.pdf"))
    if not pdf_files:
        logger.warning("No BRA PDF files found in %s", input_dir)
        return {"parsed": 0, "failed": 0, "skipped": 0}

    if dry_run:
        return _run_dry(pdf_files)
    return _run_streaming(pdf_files, output_path, resume=resume)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed namespace.

    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        default=".",
        help="Directory containing BRA PDF files (default: current dir)",
    )
    parser.add_argument(
        "--output",
        default="bulletins.ndjson",
        help="Output NDJSON file (default: bulletins.ndjson)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print field-coverage report instead of writing NDJSON",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help=(
            "Overwrite the output file instead of appending to it.  "
            "Default behaviour is to resume — skip PDFs whose envelopes "
            "are already in the output file and append the rest."
        ),
    )
    parser.add_argument(
        "--verbosity",
        type=int,
        default=1,
        choices=[0, 1, 2],
        help="Output verbosity (0=warning, 1=info, 2=debug)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the PDF-to-CAAML parser.

    Args:
        argv: Optional argument list for testing.

    Returns:
        Exit code (0 = success, 1 = some failures).

    """
    args = _parse_args(argv)
    logging.basicConfig(level=_LOG_LEVEL.get(args.verbosity, logging.INFO))
    input_dir = Path(args.input)
    output_path = Path(args.output)

    if not input_dir.is_dir():
        logger.error("Error: input directory not found: %s", input_dir)
        return 1

    counts = run(
        input_dir=input_dir,
        output_path=output_path,
        dry_run=args.dry_run,
        resume=not args.no_resume,
    )

    return 1 if counts["failed"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
