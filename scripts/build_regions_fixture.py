"""
scripts/build_regions_fixture.py — Generates pipeline/fixtures/regions.json.

Reads docs/eaws_regions_ch.csv and produces a Django fixture file for the
pipeline.Region model. Each record omits pk and uuid (so Django assigns them)
and sets created_at/updated_at to 2026-04-13T00:00:00Z to match the existing
resorts.json fixture pattern.
"""

import csv
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = REPO_ROOT / "docs" / "eaws_regions_ch.csv"
FIXTURE_PATH = REPO_ROOT / "pipeline" / "fixtures" / "regions.json"

CREATED_AT = "2026-04-13T00:00:00Z"
UPDATED_AT = "2026-04-13T00:00:00Z"


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
            boundary = json.loads(row["boundary"])
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
