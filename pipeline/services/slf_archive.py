"""
pipeline/services/slf_archive.py — On-disk NDJSON archive of SLF bulletins.

Reading and writing ``sample_data/slf_archive.ndjson``: a single
NDJSON file holding every bulletin captured by ``fetch_bulletins
--stash`` runs. One un-wrapped CAAML record per line (the shape that
``fetch_bulletin_page`` returns inside the API's ``bulletins[]`` list,
**not** the GeoJSON Feature envelope built by ``upsert_bulletin``).

The stored order is ascending by ``validTime.startTime``; the mirror
view reverses to descending by ``publicationTime`` on read so the
upstream's reverse-chronological paging contract is preserved.

Functions are pure and Django-free (the management command resolves
the archive path from settings and passes it in), which keeps them
trivially unit-testable against ``tmp_path``.
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

    A missing archive yields nothing — the empty-placeholder case is
    not an error. Blank lines are skipped so a partially-written
    archive (which should never happen given the atomic write below)
    cannot blow up callers.

    Args:
        path: Filesystem path to the NDJSON archive.

    Yields:
        One CAAML record dict per non-blank line.

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

    Records are deduped by ``bulletinID``; ``new`` entries overwrite
    ``existing`` entries with the same id (later wins — captures
    re-issues and corrections). The result is sorted ascending by
    ``validTime.startTime`` so subsequent diffs are minimal.

    Args:
        existing: Records already in the archive (typically the
            output of ``read_archive``).
        new: Records collected during a ``--stash`` run.

    Returns:
        A list of merged, deduped, sorted records ready for
        ``write_archive``.

    """
    by_id: dict[str, dict[str, Any]] = {r["bulletinID"]: r for r in existing}
    for record in new:
        by_id[record["bulletinID"]] = record
    return sorted(by_id.values(), key=lambda r: r["validTime"]["startTime"])


def write_archive(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """
    Write records to the archive atomically.

    Uses a sibling ``.tmp`` file plus ``os.replace`` so an interrupted
    run never leaves a half-written archive in place. Creates the
    parent directory if it does not yet exist.

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
    logger.debug("Wrote SLF archive: path=%s", path)
