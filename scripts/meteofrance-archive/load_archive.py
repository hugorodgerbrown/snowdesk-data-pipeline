# load_archive.py — One-off loader for the Météo-France BRA NDJSON archive.
#
# Reads the ``bulletins.ndjson`` artefact produced by the SNOW-224 pipeline
# (one CAAML 6.0 GeoJSON Feature envelope per line) and loads each row into
# the Snowdesk database via ``bulletins.services.data_fetcher.upsert_bulletin``.
#
# Two impedance mismatches between the NDJSON shape and ``upsert_bulletin``
# are fixed at load time:
#   1. ``regionID`` translation: ``FR-{MASSIF_SLUG}`` → ``FR-{NN}`` using
#      the ``SLUG_TO_CODE`` table in ``_massifs.py``.
#   2. ``bulletinID`` synthesis: the CAAML builder does not write one;
#      ``upsert_bulletin`` requires one.  Synthesised as
#      ``FR-{NN:02d}-{customData.MF.date}``.
#
# DELETION NOTE: This script (and the rest of ``scripts/meteofrance-archive/``)
# is intended to be deleted after the one-off backfill has run.  It is not
# wired into the ``BulletinSource`` enum and is not a management command.
#
# Prerequisites:
#   * The ``eaws_FR.json`` fixture must have been loaded (``loaddata eaws_FR``).
#   * ``DJANGO_SETTINGS_MODULE`` must point to a valid settings module
#     (defaults to ``config.settings.development``).
#
# Usage::
#
#     # Dry-run (default — no DB writes):
#     poetry run python scripts/meteofrance-archive/load_archive.py
#
#     # Commit mode (writes to DB):
#     poetry run python scripts/meteofrance-archive/load_archive.py --commit
#
#     # Custom input file:
#     poetry run python scripts/meteofrance-archive/load_archive.py \
#         --input /path/to/bulletins.ndjson --commit
"""One-off loader for the Météo-France BRA NDJSON archive into Snowdesk."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Resolve the repo root so Django can be bootstrapped when run as a script.
REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_INPUT = Path(__file__).resolve().parent / "bulletins.ndjson"

TRIGGERED_BY = "meteofrance-archive-backfill"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _bootstrap_django() -> None:
    """Bootstrap Django when running as a standalone script.

    Adds the repository root to ``sys.path`` and calls ``django.setup()``
    so that app imports (models, services) work outside of ``manage.py``.

    This is a no-op when the module is imported in an already-configured
    Django environment (e.g. the pytest test suite).
    """
    import django
    from django.apps import apps

    if apps.ready:
        return  # Already configured — pytest session or another caller.

    sys.path.insert(0, str(REPO_ROOT))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    django.setup()


def _slug_to_region_id(slug: str, slug_to_code: dict[str, int]) -> str:
    """Return the canonical ``FR-{NN}`` region identifier for a massif slug.

    Args:
        slug: Massif slug from ``customData.MF.massif``
            (e.g. ``"CHABLAIS"``).
        slug_to_code: Mapping of slug → integer massif code.

    Returns:
        Zero-padded region identifier (e.g. ``"FR-01"``).

    Raises:
        KeyError: The slug is not present in ``slug_to_code``.

    """
    code = slug_to_code[slug]
    return f"FR-{code:02d}"


def _fixup_envelope(
    properties: dict[str, Any],
    slug_to_code: dict[str, int],
) -> str | None:
    """Translate ``regionID`` and synthesise ``bulletinID`` for one envelope.

    Mutates ``properties`` in place:
    - ``regions[0]["regionID"]`` is translated from ``FR-{SLUG}`` to
      ``FR-{NN}`` using ``slug_to_code`` (after normalising the raw slug
      via ``_normalise_slug`` to fold the space/hyphen variants used by
      the upstream PDFs onto the canonical form).
    - ``bulletinID`` is synthesised as ``FR-{NN}-{customData.MF.date}``.

    Args:
        properties: The ``properties`` sub-dict of a CAAML GeoJSON Feature.
        slug_to_code: Mapping of slug → integer massif code (already
            includes the alias entries for known upstream misspellings).

    Returns:
        The synthesised ``bulletinID`` on success, or ``None`` if the slug
        is unknown or required fields are missing.

    """
    from _massifs import _normalise_slug

    try:
        raw_slug: str = properties["customData"]["MF"]["massif"]
        date_str: str = properties["customData"]["MF"]["date"]
    except (KeyError, TypeError):
        logger.warning("Missing customData.MF.massif or customData.MF.date — skipping")
        return None

    slug = _normalise_slug(raw_slug)
    try:
        region_id = _slug_to_region_id(slug, slug_to_code)
    except KeyError:
        logger.warning("Unknown massif slug %r — skipping", raw_slug)
        return None

    try:
        regions: list[dict[str, str]] = properties["regions"]
        regions[0]["regionID"] = region_id
    except (KeyError, IndexError, TypeError):
        logger.warning("Missing or malformed regions list for slug %r — skipping", slug)
        return None

    bulletin_id = f"{region_id}-{date_str}"
    properties["bulletinID"] = bulletin_id
    return bulletin_id


class _Counts:
    """Mutable counters threaded through the per-line processing loop."""

    def __init__(self) -> None:
        """Initialise all counters to zero."""
        self.total = 0
        self.bad_shape = 0
        self.unknown_slug = 0
        self.would_upsert = 0
        self.created = 0
        self.updated = 0


def _parse_envelope(raw_line: str, line_number: int) -> dict[str, Any] | None:
    """Parse a JSON line and return the ``properties`` sub-dict, or ``None`` on error.

    Args:
        raw_line: A stripped NDJSON line.
        line_number: 1-based line counter used in log messages.

    Returns:
        The ``properties`` dict on success, or ``None`` if the line is
        malformed or missing the expected structure.

    """
    try:
        envelope: dict[str, Any] = json.loads(raw_line)
    except json.JSONDecodeError as exc:
        logger.warning("Line %d: invalid JSON — %s", line_number, exc)
        return None
    if not isinstance(envelope, dict) or "properties" not in envelope:
        logger.warning("Line %d: missing 'properties' key — skipping", line_number)
        return None
    return envelope["properties"]  # type: ignore[return-value]


def _record_run_failure(run: Any) -> None:
    """Increment ``run.records_failed`` and persist if run is not None.

    Args:
        run: Active ``PipelineRun`` (or ``None`` in dry-run mode).

    """
    if run is not None:
        run.records_failed += 1
        run.save(update_fields=["records_failed"])


def _process_line(
    raw_line: str,
    line_number: int,
    slug_to_code: dict[str, int],
    commit: bool,
    run: Any,
    counts: _Counts,
) -> None:
    """Parse and process a single NDJSON line, mutating ``counts`` in place.

    Handles JSON decoding errors, shape validation, slug fixup, and the
    dry-run / commit dispatch.  Per-row failures increment counters and
    (in commit mode) bump ``run.records_failed`` without aborting the loop.

    Args:
        raw_line: A stripped line from the NDJSON file.
        line_number: 1-based line counter for log messages.
        slug_to_code: Slug → integer code mapping from ``_massifs``.
        commit: If ``True``, call ``upsert_bulletin``; otherwise dry-run.
        run: Active ``PipelineRun`` (or ``None`` in dry-run mode).
        counts: Mutable counter object updated by this call.

    """
    from bulletins.services.data_fetcher import upsert_bulletin

    properties = _parse_envelope(raw_line, line_number)
    if properties is None:
        counts.bad_shape += 1
        _record_run_failure(run)
        return

    bulletin_id = _fixup_envelope(properties, slug_to_code)
    if bulletin_id is None:
        from _massifs import _normalise_slug

        raw_slug = properties.get("customData", {}).get("MF", {}).get("massif", "")
        if raw_slug and _normalise_slug(raw_slug) not in slug_to_code:
            counts.unknown_slug += 1
        else:
            counts.bad_shape += 1
        _record_run_failure(run)
        return

    if not commit:
        logger.debug("[dry-run] Would upsert %s", bulletin_id)
        counts.would_upsert += 1
        return

    created = upsert_bulletin(properties, run)
    if created:
        counts.created += 1
        logger.debug("Created %s", bulletin_id)
    else:
        counts.updated += 1
        logger.debug("Updated %s", bulletin_id)


def _log_summary(counts: _Counts, commit: bool, run: Any) -> int:
    """Log a summary and return the exit code.

    Args:
        counts: Final counter state.
        commit: Whether the run was in commit mode.
        run: Completed ``PipelineRun`` (or ``None`` in dry-run mode).

    Returns:
        ``0`` on clean completion, ``1`` if any row failed.

    """
    if commit:
        records_failed: int = run.records_failed if run else 0
        logger.info(
            "Load complete: %d lines, %d created, %d updated, "
            "%d unknown-slug, %d bad-shape",
            counts.total,
            counts.created,
            counts.updated,
            counts.unknown_slug,
            counts.bad_shape,
        )
    else:
        records_failed = counts.unknown_slug + counts.bad_shape
        logger.info(
            "[dry-run] %d lines, %d would-upsert, %d unknown-slug, %d bad-shape",
            counts.total,
            counts.would_upsert,
            counts.unknown_slug,
            counts.bad_shape,
        )
    return 1 if records_failed > 0 else 0


def _walk_file(
    input_path: Path,
    slug_to_code: dict[str, int],
    commit: bool,
    run: Any,
) -> _Counts:
    """Iterate every non-empty line of ``input_path``, returning final counters.

    Args:
        input_path: Path to the NDJSON archive file.
        slug_to_code: Slug → integer code mapping from ``_massifs``.
        commit: Whether to write to the database.
        run: Active ``PipelineRun`` (or ``None`` in dry-run mode).

    Returns:
        Populated ``_Counts`` instance.

    Raises:
        Exception: Any unexpected error during file processing (caller catches).

    """
    counts = _Counts()
    with input_path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            counts.total += 1
            _process_line(raw_line, counts.total, slug_to_code, commit, run, counts)
    return counts


def load_archive(
    input_path: Path,
    *,
    commit: bool,
    verbose: bool,
) -> int:
    """Process every line of the NDJSON archive and optionally write to the DB.

    Opens a single ``PipelineRun`` when ``commit`` is ``True`` and calls
    ``upsert_bulletin`` for each valid row.  Per-row failures (unknown slug,
    render-model errors) do not abort the run; the loader continues and
    returns a non-zero exit code at the end if any failure occurred.

    In dry-run mode (``commit=False``) the function validates every line and
    logs a summary without writing anything to the database.

    Args:
        input_path: Path to the NDJSON archive file.
        commit: If ``True``, write to the database.  If ``False``, only
            validate and report.
        verbose: If ``True``, log per-row progress at DEBUG level.

    Returns:
        Exit code: ``0`` on clean completion, ``1`` if any row failed.

    """
    from _massifs import SLUG_TO_CODE  # imported here so tests can control path

    from bulletins.models import PipelineRun

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        return 1

    run: PipelineRun | None = None
    if commit:
        run = PipelineRun.objects.create(triggered_by=TRIGGERED_BY)
        run.mark_running()
        logger.info("Opened PipelineRun %s", run.pk)

    try:
        counts = _walk_file(input_path, SLUG_TO_CODE, commit, run)
    except Exception as exc:
        logger.exception("Fatal error during archive load: %s", exc)
        if run is not None:
            run.mark_failed(exc)
        return 1

    if run is not None:
        run.mark_success(counts.created, counts.updated)
    return _log_summary(counts, commit, run)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Raw argument list (typically ``sys.argv[1:]``).

    Returns:
        Parsed namespace.

    """
    parser = argparse.ArgumentParser(
        description=(
            "Load the Météo-France BRA NDJSON archive into the Snowdesk database. "
            "Read-only by default; pass --commit to write."
        ),
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        metavar="PATH",
        help=f"Path to the NDJSON archive (default: {DEFAULT_INPUT}).",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        default=False,
        help="Write bulletins to the database (default: dry-run, no writes).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="Log per-row progress at DEBUG level.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """Script entry point.

    Args:
        argv: Raw argument list (typically ``sys.argv[1:]``).

    Returns:
        Exit code (0 on success, 1 on any failure).

    """
    args = _parse_args(argv)
    return load_archive(args.input, commit=args.commit, verbose=args.verbose)


if __name__ == "__main__":
    _bootstrap_django()
    sys.exit(main(sys.argv[1:]))
