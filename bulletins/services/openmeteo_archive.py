"""
bulletins/services/openmeteo_archive.py — On-disk NDJSON archive of Open-Meteo data.

Reading and writing ``sample_data/openmeteo_archive.ndjson``: a single
NDJSON file holding every weather record captured by ``fetch_weather
--stash`` or ``backfill_weather --stash`` runs.

One record per ``(region_id, date)`` pair per line. Record shape:

.. code-block:: json

    {
      "region_id": "CH-4115",
      "date": "2026-05-01",
      "weather_code": 3,
      "sunrise": "2026-05-01T05:32+02:00",
      "sunset":  "2026-05-01T20:14+02:00",
      "captured_at": "2026-05-09T12:34:56Z"
    }

Deduplication keys on ``(region_id, date)`` with the later-``captured_at``
record winning. The stored order is ascending by ``(region_id, date)`` so
subsequent ``git diff`` output is minimal.

Functions are pure and Django-free (the management command resolves the
archive path from settings and passes it in), which keeps them trivially
unit-testable against ``tmp_path``.
"""

import json
import logging
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def read_archive(path: Path) -> Iterator[dict[str, Any]]:
    """
    Yield each record from the archive in stored order.

    A missing archive yields nothing — the empty-placeholder case is not an
    error. Blank lines are skipped so a partially-written archive (which
    should never happen given the atomic write below) cannot blow up callers.

    Args:
        path: Filesystem path to the NDJSON archive.

    Yields:
        One weather record dict per non-blank line.

    """
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped:
                yield json.loads(stripped)


def merge(
    existing: Iterable[dict[str, Any]],
    new: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Merge newly-fetched records into the existing archive contents.

    Records are deduped by ``(region_id, date)``; the record with the later
    ``captured_at`` value wins. New records overwrite existing ones with the
    same key (the ``new`` iterable is processed last so its values always
    win on equal ``captured_at``). The result is sorted ascending by
    ``(region_id, date)`` so subsequent diffs are minimal.

    Args:
        existing: Records already in the archive (typically the output of
            ``read_archive``).
        new: Records collected during a ``--stash`` run.

    Returns:
        A list of merged, deduped, sorted records ready for
        ``write_archive``.

    """
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for record in existing:
        key = (record["region_id"], record["date"])
        by_key[key] = record
    for record in new:
        key = (record["region_id"], record["date"])
        existing_record = by_key.get(key)
        if (
            existing_record is None
            or record["captured_at"] >= existing_record["captured_at"]
        ):
            by_key[key] = record
    return sorted(by_key.values(), key=lambda r: (r["region_id"], r["date"]))


def write_archive(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """
    Write records to the archive atomically.

    Uses a sibling ``.tmp`` file plus ``os.replace`` so an interrupted run
    never leaves a half-written archive in place. Creates the parent
    directory if it does not yet exist.

    Args:
        path: Final destination path for the archive.
        records: Records to serialise, one per line.

    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    logger.debug("Wrote Open-Meteo archive: path=%s", path)


def flush_stash(
    path: Path,
    collected: list[dict[str, Any]],
    command_name: str,
    *,
    stdout: Any,
    style: Any,
) -> None:
    """
    Merge collected weather records into the on-disk Open-Meteo archive.

    Reads the existing archive at ``path``, overlays the freshly-collected
    records (later ``captured_at`` wins per ``(region_id, date)`` key),
    sorts by ``(region_id, date)``, and atomically writes the result back.

    Emits a success line to ``stdout`` and logs at INFO with ``command_name``
    so production logs remain unambiguous about which command flushed.

    Shared by ``fetch_weather`` and ``backfill_weather`` to avoid duplicating
    the implementation. Accepts Django management-command ``stdout`` /
    ``style`` objects (typed as ``Any`` to avoid noisy management-framework
    imports in a Django-free module).

    Args:
        path: Filesystem path to the NDJSON archive
            (typically ``settings.OPENMETEO_ARCHIVE_PATH``).
        collected: Records gathered during the current ``--stash`` run.
        command_name: Name of the calling command (e.g. ``"fetch_weather"``),
            used in the log line for grep-ability.
        stdout: The management command's ``self.stdout`` output wrapper.
        style: The management command's ``self.style`` colour helper.

    """
    existing = list(read_archive(path))
    merged = merge(existing, collected)
    write_archive(path, merged)
    stdout.write(
        style.SUCCESS(
            f"Stashed {len(collected)} fetched record(s) to {path}; "
            f"archive now contains {len(merged)} record(s)."
        )
    )
    logger.info(
        "%s stash flush: collected=%d archive_total=%d path=%s",
        command_name,
        len(collected),
        len(merged),
        path,
    )
