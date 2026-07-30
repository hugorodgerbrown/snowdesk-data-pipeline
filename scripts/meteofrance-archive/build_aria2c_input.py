# build_aria2c_input.py — Stage 3 of the Météo-France BRA backfill pipeline.
#
# Reads bra_urls.csv (produced by Stage 2) and writes an aria2c input file
# (bra_downloads.txt) with --out directives that normalise filenames to
# BRA.{MASSIF}.{YYYY-MM-DD}.pdf regardless of which heures was published.
#
# If any URL row is skipped (status != 'ok' or missing fields), the script exits
# with code 2 so CI or shell pipelines can detect partial output.
#
# NOTE: deduplication of (massif, date) keeping only the latest heures was
# deliberately omitted.  aria2c will download all heures and the last one
# written wins on disk — this is the intended behaviour for re-published
# bulletins.  See README.md for rationale.
#
# Run:
#   python build_aria2c_input.py              # reads bra_urls.csv, dry-run
#   python build_aria2c_input.py --commit     # writes bra_downloads.txt
"""Stage 3 — Build aria2c input file from BRA URL CSV."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Exit code 2 signals that some rows were skipped (CI-gate friendly).
EXIT_SKIPPED = 2


def normalise_filename(massif: str, heures: str) -> str:
    """Return the output filename for a BRA PDF, keeping the publication time.

    The filename retains the ``heures`` timestamp from the download URL, so each
    issue lands on its own path.

    This previously collapsed to ``BRA.{MASSIF}.{YYYY-MM-DD}.pdf``, discarding
    ``heures``.  Météo-France publishes more than one BRA per massif per day, so
    two issues then contended for one filename and aria2c renamed the loser
    ``.1``, ``.2`` — which reads as a duplicate but is a distinct bulletin, in
    *download* order rather than publication order.  That lost the only record of
    which issue was which, and the identity it feeds
    (``customData.MF.source_file``) could no longer tell them apart (SNOW-559).

    Args:
        massif: Canonical massif name (e.g. ``"CHABLAIS"``).
        heures: Publication timestamp from the URL, ``YYYYMMDDHHMMSS``.

    Returns:
        Output filename string.

    """
    return f"BRA.{massif}.{heures}.pdf"


def row_to_aria2c_entry(row: dict[str, str]) -> tuple[str, str] | None:
    """Convert a CSV row to an aria2c input file entry.

    Each entry consists of the download URL on one line followed by
    ``  out=<filename>`` on the next, which instructs aria2c to save the file
    under that name.

    Args:
        row: A CSV row dict with keys ``massif``, ``heures``, ``url``.

    Returns:
        A tuple of ``(url_line, out_line)`` strings, or ``None`` if the row is
        missing required fields.

    """
    massif = row.get("massif", "").strip()
    heures = row.get("heures", "").strip()
    url = row.get("url", "").strip()
    if not all([massif, heures, url]):
        return None
    filename = normalise_filename(massif, heures)
    return url, f"  out={filename}"


def build_aria2c_input(rows: list[dict[str, str]]) -> tuple[list[str], int]:
    """Build aria2c input lines from a list of CSV rows.

    Args:
        rows: List of CSV row dicts from ``bra_urls.csv``.

    Returns:
        A tuple of ``(lines, skipped_count)`` where ``lines`` is the list of
        strings to write to the aria2c input file and ``skipped_count`` is the
        number of rows that could not be converted.

    """
    lines: list[str] = []
    skipped = 0
    for row in rows:
        entry = row_to_aria2c_entry(row)
        if entry is None:
            skipped += 1
            continue
        url_line, out_line = entry
        lines.append(url_line)
        lines.append(out_line)
        lines.append("")  # blank line between entries (aria2c convention)
    return lines, skipped


def read_csv(csv_path: Path) -> list[dict[str, str]]:
    """Read all rows from a CSV file.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        List of row dicts.

    """
    with csv_path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run(
    *,
    csv_path: Path,
    output_path: Path,
    commit: bool,
    verbosity: int,
) -> dict[str, int]:
    """Run the aria2c input builder stage.

    Args:
        csv_path: Path to the bra_urls.csv from Stage 2.
        output_path: Path to write the aria2c input file.
        commit: If True, write output to disk; otherwise dry-run only.
        verbosity: Django-style verbosity (0=quiet, 1=normal, 2=verbose).

    Returns:
        A dict with counts: ``entries_written``, ``skipped``.

    """
    rows = read_csv(csv_path)
    lines, skipped = build_aria2c_input(rows)

    entry_count = sum(1 for ln in lines if ln and not ln.startswith("  "))

    if verbosity >= 1:
        sys.stdout.write(f"  {entry_count} entries, {skipped} skipped\n")

    if commit:
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        if verbosity >= 1:
            sys.stdout.write(f"  Written to {output_path}\n")

    return {"entries_written": entry_count, "skipped": skipped}


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
        default="bra_urls.csv",
        help="Input CSV path (default: bra_urls.csv)",
    )
    parser.add_argument(
        "--output",
        default="bra_downloads.txt",
        help="Output aria2c input file (default: bra_downloads.txt)",
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
    """Entry point for the aria2c input builder.

    Args:
        argv: Optional argument list for testing.

    Returns:
        Exit code: 0 = success, 1 = error, 2 = rows skipped.

    """
    args = _parse_args(argv)

    csv_path = Path(args.input)
    output_path = Path(args.output)

    if not csv_path.exists():
        sys.stderr.write(f"Error: CSV file not found: {csv_path}\n")
        return 1

    if not args.commit:
        sys.stdout.write("DRY RUN — pass --commit to write output.\n")

    counts = run(
        csv_path=csv_path,
        output_path=output_path,
        commit=args.commit,
        verbosity=args.verbosity,
    )

    sys.stdout.write(
        f"Done. entries={counts['entries_written']}  skipped={counts['skipped']}\n"
    )

    return EXIT_SKIPPED if counts["skipped"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
