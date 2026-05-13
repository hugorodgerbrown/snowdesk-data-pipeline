"""
bulletins/management/commands/load_euregio_bulletin.py — load_euregio_bulletin.

Fetches the latest EUREGIO (ALBINA) avalanche bulletin from avalanche.report
and loads it into the database.

The ALBINA API at https://avalanche.report serves CAAML v6 bulletins in JSON
format for the EUREGIO region (Tyrol / South Tyrol / Trentino). Unlike the
SLF API, the ALBINA feed is not paginated — it returns all current bulletins
in a single response.

Safe by default: read-only unless ``--commit`` is passed.

Usage:

    # Dry-run (default) — show what would be imported, no DB writes.
    python manage.py load_euregio_bulletin

    # Persist the bulletins to the database.
    python manage.py load_euregio_bulletin --commit

    # Overwrite existing bulletins (re-ingest even if already present).
    python manage.py load_euregio_bulletin --commit --force

    # Load from a local JSON file instead of the API (useful for testing).
    python manage.py load_euregio_bulletin --file /path/to/bulletin.json --commit

API documentation:
  https://static.avalanche.report/bulletins/latest/EUREGIO_en_CAAMLv6.json
  (returns a ``{"bulletins": [...]}`` envelope of CAAML v6 bulletins, English)
"""

import json
import logging
from argparse import ArgumentParser
from pathlib import Path
from typing import Any

import requests
from django.core.management.base import BaseCommand, CommandError

from bulletins.models import Bulletin, PipelineRun
from bulletins.services.data_fetcher import upsert_bulletin
from bulletins.services.render_model import build_render_model

logger = logging.getLogger(__name__)

EUREGIO_API_URL = (
    "https://static.avalanche.report/bulletins/latest/EUREGIO_en_CAAMLv6.json"
)
REQUEST_TIMEOUT = 30  # seconds


def fetch_euregio_bulletins(url: str = EUREGIO_API_URL) -> list[dict[str, Any]]:
    """
    Fetch the current EUREGIO bulletin list from the ALBINA API.

    Args:
        url: The ALBINA JSON feed URL. Defaults to the production endpoint.

    Returns:
        A flat list of raw bulletin dicts.

    Raises:
        requests.HTTPError: If the API returns a non-2xx status.
        ValueError: If the response body cannot be parsed as JSON.

    """
    logger.debug("Fetching EUREGIO bulletins from %s", url)
    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data: Any = response.json()
    if isinstance(data, list):
        result: list[dict[str, Any]] = data
        return result
    if isinstance(data, dict) and "bulletins" in data:
        result = data["bulletins"]
        return result
    logger.warning("Unexpected ALBINA response shape — returning empty list")
    return []


def load_euregio_bulletins_from_file(path: Path) -> list[dict[str, Any]]:
    """
    Load EUREGIO bulletins from a local JSON or NDJSON file.

    Three input shapes are supported, picked by file extension:

    * ``.ndjson`` — one bulletin dict per line (the
      ``sample_data/euregio_archive.ndjson`` shape produced by
      ``scripts/fetch_euregio_archive.py``).
    * ``.json`` containing ``[{…}, …]`` — a top-level list.
    * ``.json`` containing ``{"bulletins": [{…}, …]}`` — the envelope
      used by ALBINA's CAAMLv6 exports.

    Args:
        path: Filesystem path to the file.

    Returns:
        A flat list of raw bulletin dicts.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the file cannot be parsed or has an unexpected shape.

    """
    logger.debug("Loading EUREGIO bulletins from local file %s", path)
    if path.suffix == ".ndjson":
        results: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                results.append(json.loads(stripped))
        return results
    with path.open(encoding="utf-8") as fh:
        data: Any = json.load(fh)
    if isinstance(data, list):
        result: list[dict[str, Any]] = data
        return result
    if isinstance(data, dict) and "bulletins" in data:
        result = data["bulletins"]
        return result
    raise ValueError(
        f"Unexpected JSON shape in {path}: expected list or "
        '{"bulletins": [...]} object'
    )


class Command(BaseCommand):
    """Fetch and load the latest EUREGIO bulletin from avalanche.report."""

    help = (
        "Fetch the current EUREGIO (ALBINA) bulletin from avalanche.report "
        "and load it into the database. Read-only unless --commit is passed."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Persist bulletins to the database. "
                "Without this flag the command is read-only."
            ),
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Upsert existing bulletins instead of skipping them.",
        )
        parser.add_argument(
            "--url",
            default=EUREGIO_API_URL,
            metavar="URL",
            help=(f"Override the ALBINA API endpoint. Default: {EUREGIO_API_URL}"),
        )
        parser.add_argument(
            "--file",
            default=None,
            metavar="PATH",
            help=(
                "Load bulletins from a local JSON file instead of the API. "
                "Useful for testing with the sample file at "
                "sample_data/EUREGIO_en_CAAMLv6.json."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Execute the command."""
        commit: bool = options["commit"]
        force: bool = options["force"]
        url: str = options["url"]
        file_path: str | None = options["file"]

        source_label = file_path or url
        mode_label = "COMMIT" if commit else "DRY-RUN"
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Loading EUREGIO bulletins from {source_label} [{mode_label}]"
            )
        )
        logger.info(
            "load_euregio_bulletin started: source=%s commit=%s force=%s",
            source_label,
            commit,
            force,
        )

        try:
            if file_path is not None:
                bulletins = load_euregio_bulletins_from_file(Path(file_path))
            else:
                bulletins = fetch_euregio_bulletins(url)
        except FileNotFoundError as exc:
            raise CommandError(f"File not found: {exc}") from exc
        except requests.HTTPError as exc:
            raise CommandError(f"Failed to fetch EUREGIO bulletins: {exc}") from exc
        except Exception as exc:
            raise CommandError(
                f"Unexpected error fetching EUREGIO bulletins: {exc}"
            ) from exc

        if not bulletins:
            self.stdout.write(self.style.WARNING("No bulletins returned from the API."))
            return

        self.stdout.write(f"Fetched {len(bulletins)} bulletin(s) from ALBINA.")

        if not commit:
            self._dry_run_report(bulletins)
            return

        self._persist_bulletins(bulletins, force=force)

    def _dry_run_report(self, bulletins: list[dict[str, Any]]) -> None:
        """Log what would be imported without writing to the DB."""
        for raw in bulletins:
            bulletin_id = raw.get("bulletinID", "<no id>")
            regions = [r.get("regionID", "?") for r in raw.get("regions", [])]
            self.stdout.write(
                f"  [dry-run] {bulletin_id} — regions: {', '.join(regions)}"
            )
            # Attempt render-model build to surface any parse errors early.
            try:
                build_render_model(raw)
            except Exception as exc:
                self.stdout.write(
                    self.style.WARNING(
                        f"  [dry-run] render-model build error for {bulletin_id}: {exc}"
                    )
                )
        self.stdout.write(
            self.style.SUCCESS(
                f"Dry-run complete — {len(bulletins)} bulletin(s) would be imported. "
                "Pass --commit to persist."
            )
        )

    def _persist_bulletins(
        self,
        bulletins: list[dict[str, Any]],
        *,
        force: bool,
    ) -> None:
        """Write bulletins to the database via upsert_bulletin."""
        run = PipelineRun.objects.create(triggered_by="load_euregio_bulletin command")
        run.mark_running()

        created_count = 0
        updated_count = 0
        skipped_count = 0
        failed_ids: list[str] = []

        for raw in bulletins:
            bulletin_id = raw.get("bulletinID", "<no id>")

            if not force and Bulletin.objects.filter(bulletin_id=bulletin_id).exists():
                logger.debug("Skipping existing bulletin %s", bulletin_id)
                skipped_count += 1
                continue

            try:
                created = upsert_bulletin(raw, run)
            except Exception as exc:
                logger.exception(
                    "Failed to upsert bulletin %s: %s",
                    bulletin_id,
                    exc,
                )
                self.stdout.write(self.style.ERROR(f"  Failed {bulletin_id}: {exc}"))
                failed_ids.append(bulletin_id)
                continue

            if created:
                created_count += 1
            else:
                updated_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {created_count} created, {updated_count} updated, "
                f"{skipped_count} skipped, {len(failed_ids)} failed."
            )
        )
        logger.info(
            "load_euregio_bulletin finished: created=%d updated=%d "
            "skipped=%d failed=%d",
            created_count,
            updated_count,
            skipped_count,
            len(failed_ids),
        )

        if failed_ids:
            error = CommandError(
                f"{len(failed_ids)} bulletin(s) failed to import: "
                f"{', '.join(failed_ids)}"
            )
            run.mark_failed(error)
            raise error

        run.mark_success(created_count, updated_count)
