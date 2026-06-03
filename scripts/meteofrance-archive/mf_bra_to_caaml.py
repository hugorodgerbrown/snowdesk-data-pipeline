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
#   - The SAT→problem-type mapping is an initial reasonable set; the official
#     MF alignment is a separate follow-up ticket.
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
    BAND_STABILITY,
    BAND_WEATHER_TEXT,
    BAND_WIND_TABLE,
    crop_full_width,
    crop_left,
    crop_right,
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
    """Find numeric tokens that represent danger ratings in the compass rose area.

    The danger number(s) appear at approximately x=100–170, y=140–185 on the
    page — between the N/S/E/O compass markers.

    Args:
        page: The page object (page 1 of the BRA PDF).

    Returns:
        List of word dicts for single-digit numeric tokens in the danger zone.

    """
    return [
        w
        for w in extract_words_in_region(page, 110, 135, 170, 185)
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


def extract_highlights(page: "pdfplumber.page.Page") -> str:
    """Extract the bulletin headline (all-caps title line) from page 1.

    Args:
        page: pdfplumber page object for page 1.

    Returns:
        The headline string (e.g. ``"MANTEAU NEIGEUX STABLE."``), or ``""`` if
        not found.

    """
    region = crop_full_width(page, *BAND_DANGER)
    text = extract_text_strip(region)
    # The headline is the first all-caps line after the "Estimation des risques" header.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip the standard header line and the compass markers (N/S/O/E).
        if re.match(r"^Estimation des risques", stripped, re.IGNORECASE):
            continue
        if re.match(r"^(N|S|O|E|\d|Ind|Dep|Dec|Obs)\b", stripped):
            continue
        if stripped.isupper() or re.match(r"^[A-ZÀÉÈ\s]+[.\s]*$", stripped):
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
        problem: dict[str, object] = {
            "problemType": problem_type or "no_distinct_avalanche_problem",
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

    except Exception as exc:  # noqa: BLE001 — pdfplumber raises a wide variety
        # of library-internal exceptions (PDFSyntaxError, struct.error, etc.)
        # depending on the PDF.  Catching broadly here keeps the batch parser
        # moving; the error is logged and the caller receives None.
        logger.error("Failed to parse %s: %s", pdf_path, exc)
        return None

    properties: dict[str, object] = {
        "dangerRatings": danger_ratings,
        "highlights": highlights,
        "avalancheActivity": {
            "highlights": highlights,
            "comment": format_comment_as_html(
                (
                    avalanche_activity["spontaneous"]
                    + "\n"
                    + avalanche_activity["triggered"]
                ).strip()
            ),
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
