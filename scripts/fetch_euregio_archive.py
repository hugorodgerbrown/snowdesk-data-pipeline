r"""
scripts/fetch_euregio_archive.py — One-off season fetcher for EUREGIO bulletins.

Walks the ALBINA CDN at ``static.avalanche.report/bulletins/YYYY-MM-DD/``
and pulls every EUREGIO regional CAAMLv6 bulletin (AT-07, IT-32-BZ,
IT-32-TN) for the date range, then writes one bulletin per line to
``bulletins/local_mirrors/euregio_archive.ndjson`` (ascending by
``validTime.startTime``).

The output mirrors the shape of ``bulletins/local_mirrors/slf_archive.ndjson``
so ``fetch_euregio_bulletins --source local-mirror --commit`` can seed a
dev DB from the committed artefact without a network call.

This is a committed one-off script — not a management command. ALBINA's
archive is append-only and indexed by date directory, so a single full
season fetch is sufficient to populate a season's worth of dev data.
Re-running the script overwrites the output file; new-day additions are
handled by running ``fetch_euregio_bulletins --stash`` inside the
management command.

Usage::

    python scripts/fetch_euregio_archive.py
    python scripts/fetch_euregio_archive.py \
        --start-date 2025-11-01 --end-date 2026-05-14
    python scripts/fetch_euregio_archive.py --regions AT-07 IT-32-BZ
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "bulletins" / "local_mirrors" / "euregio_archive.ndjson"

# Regions Snowdesk has fixture coverage for. Pulling other ALBINA regions
# (AT-02..AT-08, etc.) would 200 but the resulting bulletins would fail
# ``upsert_bulletin`` with UnknownRegionError until fixtures are seeded.
DEFAULT_REGIONS: tuple[str, ...] = ("AT-07", "IT-32-BZ", "IT-32-TN")

# Current Snowdesk season. Update before re-running for a future season.
DEFAULT_START_DATE = date(2025, 11, 1)

BASE_URL = "https://static.avalanche.report/bulletins"
REQUEST_TIMEOUT = 30  # seconds
REQUEST_DELAY = 0.3  # seconds between requests — courteous to ALBINA's CDN


def _iter_dates(start: date, end: date) -> list[date]:
    """
    Return every date from ``start`` to ``end`` inclusive.

    Args:
        start: First date.
        end: Last date.

    Returns:
        List of dates in ascending order.

    """
    span = (end - start).days
    return [start + timedelta(days=i) for i in range(span + 1)]


def _fetch_day_region(
    target_date: date, region: str, session: requests.Session
) -> list[dict[str, Any]]:
    """
    Fetch one region's bulletins for one date.

    Returns the list of bulletin dicts. Returns ``[]`` on 404 (off-season
    or missing day for this region) — that is the expected shape for an
    archive gap, not an error.

    Args:
        target_date: The date to fetch.
        region: ALBINA region code (e.g. ``"AT-07"``).
        session: A requests Session for connection re-use.

    Returns:
        Flat list of raw bulletin dicts. Empty on 404 or unrecognised body.

    """
    date_str = target_date.isoformat()
    url = f"{BASE_URL}/{date_str}/{date_str}_{region}_en_CAAMLv6.json"
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        logger.warning("Request failed for %s %s: %s", date_str, region, exc)
        return []
    if response.status_code == 404:
        return []
    if response.status_code >= 400:
        logger.warning(
            "HTTP %s for %s %s — skipping", response.status_code, date_str, region
        )
        return []
    try:
        data: Any = response.json()
    except ValueError as exc:
        logger.warning("Invalid JSON for %s %s: %s", date_str, region, exc)
        return []
    if isinstance(data, dict) and "bulletins" in data:
        bulletins = data["bulletins"]
        if isinstance(bulletins, list):
            return bulletins
    if isinstance(data, list):
        return data
    logger.warning(
        "Unexpected body shape for %s %s — top-level type=%s",
        date_str,
        region,
        type(data).__name__,
    )
    return []


def fetch_archive(
    start_date: date, end_date: date, regions: tuple[str, ...], output: Path
) -> None:
    """
    Walk the date × region grid and write all unique bulletins to ``output``.

    Bulletins are deduplicated by ``bulletinID`` (a single bulletin can be
    republished across consecutive days or appear in multiple regional
    files when its coverage spans regions). The output is sorted ascending
    by ``validTime.startTime`` so a downstream loader processes them in
    publication order.

    Args:
        start_date: First date to fetch (inclusive).
        end_date: Last date to fetch (inclusive).
        regions: ALBINA region codes to fetch per date.
        output: Destination NDJSON path.

    """
    dates = _iter_dates(start_date, end_date)
    logger.info(
        "Fetching %d day(s) × %d region(s) = %d requests",
        len(dates),
        len(regions),
        len(dates) * len(regions),
    )

    seen_ids: set[str] = set()
    collected: list[dict[str, Any]] = []
    requests_made = 0

    with requests.Session() as session:
        for d in dates:
            day_count = 0
            for region in regions:
                bulletins = _fetch_day_region(d, region, session)
                requests_made += 1
                for entry in bulletins:
                    bid = entry.get("bulletinID")
                    if not bid or bid in seen_ids:
                        continue
                    seen_ids.add(bid)
                    collected.append(entry)
                    day_count += 1
                if REQUEST_DELAY:
                    time.sleep(REQUEST_DELAY)
            if day_count:
                logger.info("%s: %d new bulletin(s)", d.isoformat(), day_count)

    collected.sort(key=lambda b: (b.get("validTime") or {}).get("startTime") or "")

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for entry in collected:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
            fh.write("\n")

    logger.info(
        "Wrote %d unique bulletins to %s (after %d requests)",
        len(collected),
        output,
        requests_made,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """
    Parse CLI arguments.

    Args:
        argv: Raw argument list (typically ``sys.argv[1:]``).

    Returns:
        Parsed namespace.

    """
    parser = argparse.ArgumentParser(
        description=(
            "Fetch a season of EUREGIO bulletins into a local NDJSON archive."
        ),
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
        default=DEFAULT_START_DATE,
        metavar="YYYY-MM-DD",
        help=f"First date to fetch (default: {DEFAULT_START_DATE.isoformat()}).",
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
        default=date.today(),
        metavar="YYYY-MM-DD",
        help="Last date to fetch (default: today).",
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        default=list(DEFAULT_REGIONS),
        metavar="REGION",
        help=(
            "ALBINA region codes to fetch per date "
            f"(default: {' '.join(DEFAULT_REGIONS)})."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination NDJSON path (default: {DEFAULT_OUTPUT}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """
    Script entry point.

    Args:
        argv: Raw argument list (typically ``sys.argv[1:]``).

    Returns:
        Exit code (0 on success, 1 on argument error).

    """
    args = _parse_args(argv)
    if args.start_date > args.end_date:
        logger.error("start-date must be on or before end-date")
        return 1
    fetch_archive(args.start_date, args.end_date, tuple(args.regions), args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
