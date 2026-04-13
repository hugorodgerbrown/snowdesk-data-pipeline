"""
scripts/validate_regions_csv.py — Validates the EAWS regions CSV file.

Reads all rows from docs/eaws_regions_ch.csv and validates each row's
region_id format, non-empty name/slug, centre JSON structure, and
boundary GeoJSON Polygon structure. Reports all errors and duplicate
region_ids.
"""

import csv
import json
import logging
import re
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REGION_ID_PATTERN = re.compile(r"^CH-\d{4}$")
SLUG_PATTERN = re.compile(r"^[a-z0-9_-]+$")

CSV_PATH = Path(__file__).resolve().parent.parent / "docs" / "eaws_regions_ch.csv"


def _validate_lon_lat(
    value: object, name: str, row_num: int, lo: float, hi: float
) -> list[str]:
    """
    Validate that a coordinate value is a number within a given range.

    Args:
        value: The coordinate value to check (untrusted JSON, so typed as object).
        name: Human-readable name for error messages (e.g. "centre.lon").
        row_num: 1-based row index for error reporting.
        lo: Minimum allowed value (inclusive).
        hi: Maximum allowed value (inclusive).

    Returns:
        A list of error strings (empty if valid).

    """
    if not isinstance(value, (int, float)):
        return [f"Row {row_num}: {name} must be a float, got {type(value)}"]
    if not (lo <= value <= hi):
        return [f"Row {row_num}: {name} out of range [{lo}, {hi}]: {value}"]
    return []


def validate_centre(value: str, row_num: int) -> list[str]:
    """
    Validate that the centre field is valid JSON with lon/lat float keys.

    Args:
        value: Raw string value from the CSV.
        row_num: 1-based row index for error reporting.

    Returns:
        A list of error strings (empty if valid).

    """
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        return [f"Row {row_num}: centre is not valid JSON: {exc}"]
    if not isinstance(data, dict):
        return [f"Row {row_num}: centre must be a JSON object"]
    errors: list[str] = []
    for key in ("lon", "lat"):
        if key not in data:
            errors.append(f"Row {row_num}: centre missing {key!r} key")
    if errors:
        return errors
    errors += _validate_lon_lat(data["lon"], "centre.lon", row_num, -180, 180)
    errors += _validate_lon_lat(data["lat"], "centre.lat", row_num, -90, 90)
    return errors


def _validate_ring(ring: object, ring_idx: int, row_num: int) -> list[str]:
    """
    Validate a single GeoJSON ring (array of coordinate pairs).

    Args:
        ring: The ring value to validate.
        ring_idx: Index of this ring within coordinates (for error messages).
        row_num: 1-based row index for error reporting.

    Returns:
        A list of error strings (empty if valid).

    """
    errors: list[str] = []
    if not isinstance(ring, list) or len(ring) == 0:
        return [
            f"Row {row_num}: boundary.coordinates[{ring_idx}] must be a non-empty array"
        ]
    for pt_idx, pt in enumerate(ring):
        loc = f"coords[{ring_idx}][{pt_idx}]"
        if not isinstance(pt, list) or len(pt) < 2:
            errors.append(f"Row {row_num}: {loc} must be a [lon, lat] pair")
            continue
        errors += _validate_lon_lat(pt[0], f"{loc} lon", row_num, -180, 180)
        errors += _validate_lon_lat(pt[1], f"{loc} lat", row_num, -90, 90)
    return errors


def validate_boundary(value: str, row_num: int) -> list[str]:
    """
    Validate that the boundary field is a valid GeoJSON Polygon.

    Checks type == "Polygon", coordinates is an array of rings, each ring
    is an array of [lon, lat] pairs, and all coordinates are valid floats.

    Args:
        value: Raw string value from the CSV.
        row_num: 1-based row index for error reporting.

    Returns:
        A list of error strings (empty if valid).

    """
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        return [f"Row {row_num}: boundary is not valid JSON: {exc}"]
    if not isinstance(data, dict):
        return [f"Row {row_num}: boundary must be a JSON object"]
    errors: list[str] = []
    if data.get("type") != "Polygon":
        errors.append(
            f"Row {row_num}: boundary type must be 'Polygon', got {data.get('type')!r}"
        )
    coords = data.get("coordinates")
    if not isinstance(coords, list) or len(coords) == 0:
        errors.append(f"Row {row_num}: boundary.coordinates must be a non-empty array")
        return errors
    for ring_idx, ring in enumerate(coords):
        errors += _validate_ring(ring, ring_idx, row_num)
    return errors


def _validate_row(row: dict, row_num: int, seen_ids: dict[str, int]) -> list[str]:
    """
    Validate a single CSV row and check for duplicate region_ids.

    Args:
        row: Dict of column name -> value for this row.
        row_num: 1-based row index for error reporting.
        seen_ids: Mapping of region_id -> row_num for duplicate detection.

    Returns:
        A list of error strings (empty if valid).

    """
    errors: list[str] = []
    region_id = row.get("region_id", "").strip()
    region_name = row.get("region_name", "").strip()
    slug = row.get("slug", "").strip()

    if not REGION_ID_PATTERN.match(region_id):
        errors.append(
            f"Row {row_num}: invalid region_id {region_id!r} "
            f"(expected CH-XXXX with 4 digits)"
        )
    if region_id in seen_ids:
        errors.append(
            f"Row {row_num}: duplicate region_id {region_id!r} "
            f"(first seen at row {seen_ids[region_id]})"
        )
    else:
        seen_ids[region_id] = row_num
    if not region_name:
        errors.append(f"Row {row_num}: region_name is empty")
    if not slug:
        errors.append(f"Row {row_num}: slug is empty")
    elif not SLUG_PATTERN.match(slug):
        errors.append(f"Row {row_num}: slug {slug!r} contains invalid characters")
    errors += validate_centre(row.get("centre", "").strip(), row_num)
    errors += validate_boundary(row.get("boundary", "").strip(), row_num)
    return errors


def validate_csv(csv_path: Path) -> bool:
    """
    Read the CSV and validate every row.

    Prints all validation errors and duplicate region_ids. Returns True
    if the file is fully valid, False otherwise.

    Args:
        csv_path: Absolute path to the CSV file.

    Returns:
        True if all rows are valid, False if any errors were found.

    """
    all_errors: list[str] = []
    seen_ids: dict[str, int] = {}
    row_count = 0

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row_num, row in enumerate(reader, start=2):  # 2: header is row 1
            row_count += 1
            all_errors.extend(_validate_row(row, row_num, seen_ids))

    logger.info("Validated %d rows", row_count)
    if all_errors:
        logger.error("Found %d validation error(s):", len(all_errors))
        for err in all_errors:
            logger.error("  %s", err)
        return False
    logger.info("All %d rows are valid. No errors found.", row_count)
    return True


if __name__ == "__main__":
    success = validate_csv(CSV_PATH)
    sys.exit(0 if success else 1)
