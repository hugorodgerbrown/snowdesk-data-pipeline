# mf_bra_index_archive.py — Stage 1 of the Météo-France BRA backfill pipeline.
#
# Hits the public index endpoint (bra.YYYYMMDD.json) for each date in the
# configured range, classifies each response as ok / not_found / error:<reason>,
# and appends one NDJSON record per date to the output file.
#
# The script is resumable: dates already present in the output file are skipped.
# Off-season dates (outside the Nov–Apr window) are skipped without an HTTP
# request.  A 0.2 s delay between requests respects Météo-France's implicit
# rate limit.
#
# Run:
#   python mf_bra_index_archive.py               # 2025-11-01 to today, dry-run
#   python mf_bra_index_archive.py --commit      # actually write bra_indexes.ndjson
#   python mf_bra_index_archive.py --start 2025-11-01 --end 2026-04-30 --commit
"""Stage 1 — Météo-France BRA daily index archiver."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import TextIO

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA"
USER_AGENT = "snowdesk-backfill/1.0 (contact@snowdesk.app)"
RATE_LIMIT_S = 0.2  # seconds between requests

# Avalanche season: November through April (inclusive).
SEASON_MONTHS = {11, 12, 1, 2, 3, 4}


def is_in_season(d: date) -> bool:
    """Return True if the date falls within the avalanche season (Nov–Apr)."""
    return d.month in SEASON_MONTHS


def fetch_index(date_str: str, session: requests.Session) -> dict[str, object]:
    """Fetch the daily BRA index JSON for a given date string (YYYYMMDD).

    Args:
        date_str: Date string in YYYYMMDD format.
        session: Requests session to reuse for connection pooling.

    Returns:
        A dict with keys:
          - ``date``: the date string
          - ``status``: ``"ok"``, ``"not_found"``, or ``"error:<reason>"``
          - ``massifs``: list of massif dicts (only when status is ``"ok"``)

    """
    url = f"{BASE_URL}/bra.{date_str}.json"
    try:
        resp = session.get(url, timeout=30)
        if resp.status_code == 404:
            return {"date": date_str, "status": "not_found", "massifs": []}
        resp.raise_for_status()
        return {"date": date_str, "status": "ok", "massifs": resp.json()}
    except requests.exceptions.HTTPError as exc:
        code = exc.response.status_code
        return {"date": date_str, "status": f"error:{code}", "massifs": []}
    except requests.exceptions.RequestException as exc:
        return {"date": date_str, "status": f"error:{exc}", "massifs": []}


def load_existing_dates(output_path: Path) -> set[str]:
    """Return the set of date strings already recorded in the output NDJSON.

    Args:
        output_path: Path to the output NDJSON file (may not exist yet).

    Returns:
        Set of ``YYYYMMDD`` date strings already present.

    """
    existing: set[str] = set()
    if not output_path.exists():
        return existing
    for line in output_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            existing.add(record["date"])
        except json.JSONDecodeError, KeyError:
            pass
    return existing


def date_range(start: date, end: date) -> list[date]:
    """Return a list of dates from start to end (inclusive).

    Args:
        start: Start date.
        end: End date (inclusive).

    Returns:
        List of date objects.

    """
    result = []
    current = start
    while current <= end:
        result.append(current)
        current += timedelta(days=1)
    return result


def _classify_status(status: object) -> str:
    """Return the counter key for a given status value.

    Args:
        status: Status string from fetch_index().

    Returns:
        One of ``"fetched"``, ``"not_found"``, or ``"errors"``.

    """
    if status == "ok":
        return "fetched"
    if status == "not_found":
        return "not_found"
    return "errors"


def _process_date(
    d: date,
    existing: set[str],
    session: requests.Session,
    counts: dict[str, int],
    write_handle: TextIO | None,
    commit: bool,
    verbosity: int,
    out: TextIO,
) -> None:
    """Process a single date: skip, fetch, classify, and optionally write.

    Args:
        d: The date to process.
        existing: Set of already-archived date strings.
        session: HTTP session for the fetch.
        counts: Mutable counters dict to update in place.
        write_handle: Open file handle for NDJSON output (or None).
        commit: Whether to write records to disk.
        verbosity: Verbosity level.
        out: Output stream for progress messages.

    """
    date_str = d.strftime("%Y%m%d")

    if not is_in_season(d):
        counts["skipped_season"] += 1
        if verbosity >= 2:
            out.write(f"  skip off-season: {date_str}\n")
        return

    if date_str in existing:
        counts["skipped_existing"] += 1
        if verbosity >= 2:
            out.write(f"  skip existing:   {date_str}\n")
        return

    record = fetch_index(date_str, session)
    counts[_classify_status(record["status"])] += 1

    if verbosity >= 1:
        out.write(f"  {date_str}: {record['status']}\n")

    if commit and write_handle is not None:
        write_handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    time.sleep(RATE_LIMIT_S)


def run(
    *,
    start: date,
    end: date,
    output_path: Path,
    commit: bool,
    verbosity: int,
    out: TextIO | None = None,
) -> dict[str, int]:
    """Run the index archive stage.

    Args:
        start: First date to fetch (inclusive).
        end: Last date to fetch (inclusive).
        output_path: Path to write/append the NDJSON output.
        commit: If True, write to disk; otherwise dry-run only.
        verbosity: Django-style verbosity (0=quiet, 1=normal, 2=verbose).
        out: Output stream for human-readable progress (defaults to stdout).

    Returns:
        A dict with counts: ``fetched``, ``skipped_season``, ``skipped_existing``,
        ``not_found``, ``errors``.

    """
    if out is None:
        out = sys.stdout

    existing = load_existing_dates(output_path)
    dates = date_range(start, end)
    counts: dict[str, int] = {
        "fetched": 0,
        "skipped_season": 0,
        "skipped_existing": 0,
        "not_found": 0,
        "errors": 0,
    }

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    write_handle = None
    if commit:
        write_handle = output_path.open("a", encoding="utf-8")

    try:
        for d in dates:
            _process_date(
                d, existing, session, counts, write_handle, commit, verbosity, out
            )
    finally:
        if write_handle is not None:
            write_handle.close()

    return counts


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Parsed namespace.

    """
    today = date.today()
    default_start = date(2025, 11, 1)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--start",
        default=default_start.isoformat(),
        help=f"Start date YYYY-MM-DD (default: {default_start.isoformat()})",
    )
    parser.add_argument(
        "--end",
        default=today.isoformat(),
        help=f"End date YYYY-MM-DD inclusive (default: {today.isoformat()})",
    )
    parser.add_argument(
        "--output",
        default="bra_indexes.ndjson",
        help="Output NDJSON file path (default: bra_indexes.ndjson)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write output to disk (default: dry-run, no writes)",
    )
    parser.add_argument(
        "--verbosity",
        type=int,
        default=1,
        choices=[0, 1, 2],
        help="Output verbosity (0=quiet, 1=normal, 2=verbose)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the index archive stage.

    Args:
        argv: Optional argument list for testing.

    Returns:
        Exit code (0 = success, 1 = errors encountered).

    """
    logging.basicConfig(level=logging.WARNING)
    args = _parse_args(argv)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    output_path = Path(args.output)

    if not args.commit:
        sys.stdout.write("DRY RUN — pass --commit to write output.\n")

    counts = run(
        start=start,
        end=end,
        output_path=output_path,
        commit=args.commit,
        verbosity=args.verbosity,
    )

    sys.stdout.write(
        f"Done. fetched={counts['fetched']}  not_found={counts['not_found']}"
        f"  errors={counts['errors']}"
        f"  skipped_season={counts['skipped_season']}"
        f"  skipped_existing={counts['skipped_existing']}\n"
    )

    return 1 if counts["errors"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
