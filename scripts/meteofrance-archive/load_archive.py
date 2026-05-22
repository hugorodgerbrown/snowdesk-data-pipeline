# load_archive.py — Thin CLI wrapper over the canonical MF archive loader.
#
# The canonical loading logic has moved to
# ``bulletins.services.mf_archive_loader.load_mf_archive`` so it can be
# invoked by both this offline script and the Django admin upload view
# (SNOW-227).
#
# This script bootstraps Django, opens the NDJSON file from the filesystem,
# and delegates to ``load_mf_archive``.  The ``load_archive(path, *, commit,
# verbose) -> int`` public function preserves its original signature so that
# existing tests in ``tests/scripts/meteofrance_archive/test_load_archive.py``
# keep passing without modification.
#
# Two impedance mismatches between the NDJSON shape and ``upsert_bulletin``
# are fixed at load time (in the service layer):
#   1. ``regionID`` translation: ``FR-{MASSIF_SLUG}`` → ``FR-{NN}``.
#   2. ``bulletinID`` synthesis: ``FR-{NN:02d}-{customData.MF.date}``.
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
"""CLI wrapper for the Météo-France BRA NDJSON archive loader."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

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


def _fixup_envelope(properties: object, slug_to_code: dict[str, int]) -> str | None:
    """Delegate to the service-layer fixup for backward compatibility.

    This shim preserves the signature expected by
    ``tests/scripts/meteofrance_archive/test_load_archive.py``.

    Args:
        properties: The ``properties`` sub-dict of a CAAML GeoJSON Feature.
        slug_to_code: Slug → integer code mapping (unused; the service layer
            imports ``SLUG_TO_CODE`` directly).

    Returns:
        The synthesised ``bulletinID`` on success, or ``None`` on failure.

    """
    from bulletins.services.mf_archive_loader import _fixup_envelope as _svc_fixup

    # The service-layer version no longer takes slug_to_code as an argument;
    # it imports SLUG_TO_CODE internally.  Cast here to keep the existing
    # test suite happy with the old call signature.
    if not isinstance(properties, dict):
        return None
    return _svc_fixup(properties)


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
    from bulletins.services.mf_archive_loader import load_mf_archive

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not input_path.exists():
        logger.error("Input file not found: %s", input_path)
        return 1

    try:
        with input_path.open("r", encoding="utf-8") as fh:
            result = load_mf_archive(
                fh,
                commit=commit,
                triggered_by=TRIGGERED_BY,
            )
    except Exception as exc:
        logger.exception("Fatal error during archive load: %s", exc)
        return 1

    if commit:
        logger.info(result.as_summary())
    else:
        logger.info(result.as_summary())

    failed = result.failed if commit else result.unknown_slug + result.bad_shape
    return 1 if failed > 0 else 0


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
