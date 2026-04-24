"""
scripts/build_regions_fixture.py — Generates pipeline/fixtures/regions.json.

Reads docs/eaws_regions_ch.csv and produces a Django fixture file for the
pipeline.Region model. Each record omits pk and uuid (so Django assigns them)
and sets created_at/updated_at to 2026-04-13T00:00:00Z to match the existing
resorts.json fixture pattern.

Boundary polygon rings are defensively closed (first position appended as
last if missing) so the fixture always satisfies RFC 7946 §3.1.6, even if
a hand-edited CSV row forgets the closing vertex.
"""

import csv
import json
import logging
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "docs" / "eaws_regions_ch.csv"
FIXTURE_PATH = REPO_ROOT / "pipeline" / "fixtures" / "regions.json"

CREATED_AT = "2026-04-13T00:00:00Z"
UPDATED_AT = "2026-04-13T00:00:00Z"


def _close_polygon_rings(boundary: dict[str, Any]) -> dict[str, Any]:
    """
    Return a copy of ``boundary`` with every linear ring closed.

    GeoJSON (RFC 7946 §3.1.6) requires each ring's first and last positions
    to be identical. If the CSV row is missing the closing vertex, this
    helper appends a copy of the first coordinate so the downstream map
    line layer draws the full outline without a visible gap.

    Args:
        boundary: A GeoJSON Polygon geometry object.

    Returns:
        A new boundary dict with closed rings; non-polygon input is
        returned unchanged.

    """
    if boundary.get("type") != "Polygon":
        return boundary
    rings = boundary.get("coordinates", [])
    closed_rings: list[list[list[float]]] = []
    for ring in rings:
        if isinstance(ring, list) and len(ring) >= 2 and ring[0] != ring[-1]:
            closed_rings.append([*ring, ring[0]])
        else:
            closed_rings.append(ring)
    return {**boundary, "coordinates": closed_rings}


def build_fixture(csv_path: Path, fixture_path: Path) -> None:
    """
    Read the CSV and write a Django JSON fixture for pipeline.Region.

    Args:
        csv_path: Path to the source CSV file.
        fixture_path: Destination path for the generated JSON fixture.

    """
    records: list[dict] = []

    with csv_path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            centre = json.loads(row["centre"])
            boundary = _close_polygon_rings(json.loads(row["boundary"]))
            region_id = row["region_id"].strip()
            records.append(
                {
                    "model": "pipeline.region",
                    "fields": {
                        "region_id": region_id,
                        "name": row["region_name"].strip(),
                        "slug": row["slug"].strip(),
                        # Parent L2 sub-region natural key (region_id[:5]).
                        # The referenced EawsSubRegion must exist in
                        # pipeline/fixtures/eaws_sub_regions.json.
                        "subregion": [region_id[:5]],
                        "centre": centre,
                        "boundary": boundary,
                        "created_at": CREATED_AT,
                        "updated_at": UPDATED_AT,
                    },
                }
            )

    fixture_path.parent.mkdir(parents=True, exist_ok=True)
    with fixture_path.open("w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    logger.info("Wrote %d region records to %s", len(records), fixture_path)


if __name__ == "__main__":
    build_fixture(CSV_PATH, FIXTURE_PATH)
