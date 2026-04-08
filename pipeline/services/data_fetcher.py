"""
pipeline/services/data_fetcher.py — Fetching and persisting data records.

Contains pure-ish functions that:
  1. Fetch raw data from an external source (fetch_records).
  2. Persist the fetched data into the database (save_records).
  3. Orchestrate a full pipeline run (run_pipeline).

Keeping these as functions rather than a class makes them easy to test and
compose. The management commands call run_pipeline(); unit tests can call
fetch_records() and save_records() independently.

Replace the stub implementation of fetch_records() with your real data
source (REST API, S3, database, etc.).
"""

import logging
from datetime import date, timedelta
from typing import Iterator

from decouple import config

from pipeline.models import DataRecord, PipelineRun

logger = logging.getLogger(__name__)

# Read API credentials once at module load so misconfiguration fails early.
DATA_SOURCE_BASE_URL = config("DATA_SOURCE_BASE_URL", default="")
DATA_SOURCE_API_KEY = config("DATA_SOURCE_API_KEY", default="")


def date_range(start: date, end: date) -> Iterator[date]:
    """
    Yield each date from start up to and including end.

    Args:
        start: First date to yield.
        end: Last date to yield (inclusive).
    """
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def fetch_records(target_date: date) -> list[dict]:
    """
    Retrieve raw records from the external data source for a single date.

    Replace the stub implementation below with your real HTTP call, S3
    read, database query, etc.

    Args:
        target_date: The date for which to fetch data.

    Returns:
        A list of dicts, each containing at minimum:
          - "external_id" (str)
          - "value" (float or Decimal-compatible)
          - "label" (str, optional)
          - "raw_data" (dict, optional)

    Raises:
        requests.HTTPError: If the upstream API returns a non-2xx response.
        ValueError: If the response payload cannot be parsed.
    """
    logger.debug("Fetching records for %s", target_date)

    # --- Replace this stub with your real implementation ---
    # Example using requests:
    #
    # import requests
    # response = requests.get(
    #     f"{DATA_SOURCE_BASE_URL}/data",
    #     params={"date": target_date.isoformat()},
    #     headers={"Authorization": f"Bearer {DATA_SOURCE_API_KEY}"},
    #     timeout=30,
    # )
    # response.raise_for_status()
    # return response.json()["records"]

    # Stub: return synthetic data so the app runs without a real source.
    return [
        {
            "external_id": f"stub-{target_date.isoformat()}-{i}",
            "value": round(100.0 + i * 1.5, 6),
            "label": f"Stub record {i} for {target_date}",
            "raw_data": {"date": target_date.isoformat(), "index": i},
        }
        for i in range(3)
    ]


def save_records(records: list[dict], target_date: date, run: PipelineRun) -> tuple[int, int]:
    """
    Upsert a list of raw record dicts into the DataRecord table.

    Uses update_or_create so the operation is idempotent — re-running for
    the same date will update existing rows rather than creating duplicates.

    Args:
        records: List of dicts as returned by fetch_records().
        target_date: The date all records belong to.
        run: The PipelineRun to associate with each record.

    Returns:
        Tuple of (records_created, records_updated).
    """
    created_count = 0
    updated_count = 0

    for raw in records:
        _, created = DataRecord.objects.update_or_create(
            external_id=raw["external_id"],
            date=target_date,
            defaults={
                "value": raw["value"],
                "label": raw.get("label", ""),
                "raw_data": raw.get("raw_data", {}),
                "pipeline_run": run,
            },
        )
        if created:
            created_count += 1
        else:
            updated_count += 1

    logger.debug(
        "Saved records for %s: %d created, %d updated",
        target_date,
        created_count,
        updated_count,
    )
    return created_count, updated_count


def run_pipeline(start: date, end: date, triggered_by: str = "unknown", dry_run: bool = False) -> PipelineRun:
    """
    Orchestrate a full pipeline run over a date range.

    Creates a PipelineRun record, iterates over each date in [start, end],
    fetches data, and persists it. Marks the run as SUCCESS or FAILED.

    Args:
        start: First date to process (inclusive).
        end: Last date to process (inclusive).
        triggered_by: Human-readable label for who/what triggered the run.
        dry_run: If True, fetch data but do not write anything to the database.

    Returns:
        The completed (or failed) PipelineRun instance.
    """
    run = PipelineRun.objects.create(triggered_by=triggered_by)
    run.mark_running()

    total_created = 0
    total_updated = 0

    try:
        for target_date in date_range(start, end):
            logger.info("Processing %s", target_date)
            records = fetch_records(target_date)

            if dry_run:
                logger.info("[dry-run] Would save %d records for %s", len(records), target_date)
                continue

            created, updated = save_records(records, target_date, run)
            total_created += created
            total_updated += updated

    except Exception as exc:
        run.mark_failed(exc)
        return run

    if not dry_run:
        run.mark_success(total_created, total_updated)
    else:
        # Dry runs don't mutate data; close out the run record cleanly.
        run.mark_success(0, 0)

    return run
