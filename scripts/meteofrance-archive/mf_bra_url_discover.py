# mf_bra_url_discover.py — Stage 2 of the Météo-France BRA backfill pipeline.
#
# Reads bra_indexes.ndjson (produced by Stage 1, or fetched on demand if absent)
# and expands each (massif, heures[]) pair into one CSV row per combination.
# Multiple heures entries per day (re-published bulletins) are each emitted as
# a separate row; the downloader will overwrite with the latest heures on disk.
#
# Output columns: massif, date, heures, url, status
#
# Run:
#   python mf_bra_url_discover.py                          # dry-run
#   python mf_bra_url_discover.py --commit                 # write bra_urls.csv
#   python mf_bra_url_discover.py --input bra_indexes.ndjson --output bra_urls.csv \
#       --commit
"""Stage 2 — BRA URL discovery from index NDJSON to CSV."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path

from _massifs import ALPINE_MASSIFS

logger = logging.getLogger(__name__)

BASE_URL = "https://donneespubliques.meteofrance.fr/donnees_libres/Pdf/BRA"

# CSV column names (order matters for the aria2c builder in Stage 3).
CSV_COLUMNS = ["massif", "date", "heures", "url", "status"]


def heures_to_date(heures: str) -> str:
    """Extract a YYYY-MM-DD date string from a heures timestamp (YYYYMMDDHHMMSS).

    Args:
        heures: Timestamp string in ``YYYYMMDDHHMMSS`` format
            (e.g. ``"20260521140706"``).

    Returns:
        Date string in ``YYYY-MM-DD`` format (e.g. ``"2026-05-21"``).

    """
    return f"{heures[:4]}-{heures[4:6]}-{heures[6:8]}"


def build_pdf_url(massif: str, heures: str) -> str:
    """Build the PDF download URL for a massif/heures pair.

    Args:
        massif: Canonical massif name (e.g. ``"CHABLAIS"``).
        heures: Timestamp string in ``YYYYMMDDHHMMSS`` format.

    Returns:
        Full URL to the PDF file.

    """
    return f"{BASE_URL}/BRA.{massif}.{heures}.pdf"


def _rows_for_entry(
    entry: dict[str, object], massif_filter: set[str]
) -> list[dict[str, str]]:
    """Expand one massif entry dict into CSV rows (one per heures value).

    Args:
        entry: A single entry from the ``massifs`` list in an index record.
        massif_filter: Set of massif names to include.

    Returns:
        List of CSV row dicts, or empty list if the massif is not in the filter.

    """
    massif = str(entry.get("massif", ""))
    if massif not in massif_filter:
        return []
    heures_list = entry.get("heures", [])
    if not isinstance(heures_list, list):
        return []
    rows = []
    for heures in heures_list:
        heures = str(heures)
        rows.append(
            {
                "massif": massif,
                "date": heures_to_date(heures),
                "heures": heures,
                "url": build_pdf_url(massif, heures),
                "status": "pending",
            }
        )
    return rows


def expand_index_record(
    record: dict[str, object],
    massif_filter: set[str] | None = None,
) -> list[dict[str, str]]:
    """Expand a single index record into one CSV row per (massif, heures) pair.

    Args:
        record: A parsed NDJSON record from Stage 1 with keys ``date``,
            ``status``, and ``massifs``.
        massif_filter: If provided, only include massifs in this set.
            Defaults to the 23 Alpine massifs.

    Returns:
        List of CSV row dicts with keys from ``CSV_COLUMNS``.

    """
    if massif_filter is None:
        massif_filter = set(ALPINE_MASSIFS)

    if record.get("status") != "ok":
        return []

    massifs_raw = record.get("massifs", [])
    if not isinstance(massifs_raw, list):
        return []

    rows: list[dict[str, str]] = []
    for entry in massifs_raw:
        if isinstance(entry, dict):
            rows.extend(_rows_for_entry(entry, massif_filter))
    return rows


def load_index_records(index_path: Path) -> list[dict[str, object]]:
    """Load all NDJSON records from the index file.

    Args:
        index_path: Path to the NDJSON index file (from Stage 1).

    Returns:
        List of parsed record dicts.

    """
    records: list[dict[str, object]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.warning("Skipping malformed NDJSON line: %s", exc)
    return records


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    """Write the URL rows to a CSV file.

    Args:
        rows: List of row dicts with keys from ``CSV_COLUMNS``.
        output_path: Path to the output CSV file.

    """
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _count_skipped_massifs(record: dict[str, object], massif_filter: set[str]) -> int:
    """Count massifs in an index record that are not in the filter.

    Args:
        record: A parsed NDJSON record.
        massif_filter: Set of massif names that are included.

    Returns:
        Number of massifs in the record that are not in massif_filter.

    """
    if record.get("status") != "ok":
        return 0
    massifs_raw = record.get("massifs", [])
    if not isinstance(massifs_raw, list):
        return 0
    return sum(
        1
        for entry in massifs_raw
        if isinstance(entry, dict) and str(entry.get("massif", "")) not in massif_filter
    )


def run(
    *,
    index_path: Path,
    output_path: Path,
    massif_filter: set[str] | None = None,
    commit: bool,
    verbosity: int,
) -> dict[str, int]:
    """Run the URL discovery stage.

    Args:
        index_path: Path to the NDJSON index file (Stage 1 output).
        output_path: Path to write the CSV output.
        massif_filter: Set of massif names to include (default: 23 Alpine).
        commit: If True, write output to disk; otherwise dry-run only.
        verbosity: Django-style verbosity (0=quiet, 1=normal, 2=verbose).

    Returns:
        A dict with counts: ``records_processed``, ``rows_written``,
        ``massifs_skipped``.

    """
    if massif_filter is None:
        massif_filter = set(ALPINE_MASSIFS)

    records = load_index_records(index_path)
    all_rows: list[dict[str, str]] = []
    massifs_skipped = 0

    for record in records:
        all_rows.extend(expand_index_record(record, massif_filter))
        massifs_skipped += _count_skipped_massifs(record, massif_filter)

    if verbosity >= 1:
        sys.stdout.write(f"  {len(records)} index records → {len(all_rows)} URL rows\n")
        sys.stdout.write(
            f"  {massifs_skipped} massifs skipped (outside Alpine filter)\n"
        )

    if commit:
        write_csv(all_rows, output_path)
        if verbosity >= 1:
            sys.stdout.write(f"  Written to {output_path}\n")

    return {
        "records_processed": len(records),
        "rows_written": len(all_rows),
        "massifs_skipped": massifs_skipped,
    }


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
        default="bra_indexes.ndjson",
        help="Input NDJSON file path (default: bra_indexes.ndjson)",
    )
    parser.add_argument(
        "--output",
        default="bra_urls.csv",
        help="Output CSV file path (default: bra_urls.csv)",
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
    """Entry point for the URL discovery stage.

    Args:
        argv: Optional argument list for testing.

    Returns:
        Exit code (0 = success).

    """
    logging.basicConfig(level=logging.WARNING)
    args = _parse_args(argv)

    index_path = Path(args.input)
    output_path = Path(args.output)

    if not index_path.exists():
        sys.stderr.write(f"Error: index file not found: {index_path}\n")
        return 1

    if not args.commit:
        sys.stdout.write("DRY RUN — pass --commit to write output.\n")

    counts = run(
        index_path=index_path,
        output_path=output_path,
        commit=args.commit,
        verbosity=args.verbosity,
    )

    sys.stdout.write(
        f"Done. records_processed={counts['records_processed']}"
        f"  rows_written={counts['rows_written']}"
        f"  massifs_skipped={counts['massifs_skipped']}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
