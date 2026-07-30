"""
apps/bulletins/services/fetcher_common.py — Shared helpers for the bulletin fetchers.

Centralises three pieces of logic that were duplicated across the SLF, ALBINA,
and Météo-France fetchers:

1. **Outcome constants** — ``OUTCOME_CREATED``, ``OUTCOME_UPDATED``,
   ``OUTCOME_SKIPPED``, ``OUTCOME_FAILED`` are the provider-agnostic outcome
   tags used by all three pipelines.  Provider-specific tags (``"duplicate"``
   for ALBINA, ``"delegated"`` for Météo-France) are defined in their
   respective modules.

2. **NDJSON stash writers** — ``merge_ndjson_records`` (dedupe + sort) and
   ``write_ndjson_archive`` (read-existing → merge → atomic write) are the
   building blocks used by ALBINA and Météo-France stash writers.  The SLF
   stash writer delegates to ``apps.bulletins.services.slf_archive`` which has its
   own decomposed helpers (``merge`` / ``write_archive``) because SLF serialises
   with ``ensure_ascii=False`` while ALBINA/MF use the JSON default; keeping
   separate code avoids a silent encoding divergence.

3. **Response normalisation** — ``normalise_bulletin_response`` unwraps the
   three CDN/API envelope shapes (flat list, ``{"bulletins": [...]}`` dict,
   list-of-collections) into a uniform flat list.  The ``source_label``
   argument threads provider context into the warning log so callers don't
   need to add their own guard.

All functions are Django-free so they can be unit-tested without a database.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared outcome constants
# ---------------------------------------------------------------------------

OUTCOME_CREATED = "created"
OUTCOME_UPDATED = "updated"
OUTCOME_SKIPPED = "skipped"
OUTCOME_FAILED = "failed"

# ---------------------------------------------------------------------------
# NDJSON merge helpers (used by ALBINA and Météo-France stash writers)
# ---------------------------------------------------------------------------


def merge_ndjson_records(
    existing: list[dict[str, Any]],
    new: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Merge newly-fetched bulletin records into existing archive contents.

    Records are deduped by ``bulletinID``; ``new`` entries overwrite
    ``existing`` entries with the same id (later wins — captures re-issues
    and corrections). Records without a ``bulletinID`` are silently dropped.
    The result is sorted ascending by ``validTime.startTime`` (safe-get; an
    absent key sorts to the front of the list).

    Args:
        existing: Records already in the archive (typically from a prior
            ``write_ndjson_archive`` run).
        new: Records collected during the current ``--stash`` run.

    Returns:
        A list of merged, deduped, sorted records ready for
        ``write_ndjson_archive``.

    """
    by_id: dict[str, dict[str, Any]] = {}
    for record in existing:
        bid = record.get("bulletinID", "")
        if bid:
            by_id[bid] = record
    for record in new:
        bid = record.get("bulletinID", "")
        if bid:
            by_id[bid] = record
    return sorted(
        by_id.values(),
        key=lambda r: (r.get("validTime") or {}).get("startTime", ""),
    )


def write_ndjson_archive(
    records: list[dict[str, Any]],
    path: Path,
    *,
    source_label: str = "",
) -> int:
    """
    Read the existing archive, merge ``records`` in, and write atomically.

    Reads the NDJSON file at ``path`` (skipping blank lines and records
    without a ``bulletinID``), overlays ``records`` via
    ``merge_ndjson_records``, and writes the result back through a sibling
    ``.tmp`` file plus ``os.replace`` so an interrupted run never leaves a
    half-written archive.  Creates the parent directory if absent.

    Serialises with ``json.dumps(record)`` — i.e. ``ensure_ascii=True`` (the
    Python default).  The SLF archive uses ``ensure_ascii=False`` and is
    handled separately in ``apps.bulletins.services.slf_archive`` to preserve
    byte-identical output.

    Args:
        records: Bulletin dicts to merge into the archive.
        path: Filesystem path to the destination NDJSON file.
        source_label: Short provider name used in the log message (e.g.
            ``"albina"`` or ``"meteofrance"``).  Pass ``""`` to omit the
            label prefix.

    Returns:
        The total number of records in the archive after the merge.

    """
    existing: list[dict[str, Any]] = []
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    record = json.loads(stripped)
                    if record.get("bulletinID", ""):
                        existing.append(record)

    merged = merge_ndjson_records(existing, records)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for record in merged:
            fh.write(json.dumps(record) + "\n")
    os.replace(tmp, path)

    label_prefix = f"{source_label} " if source_label else ""
    logger.info(
        "%swrite_ndjson_archive: records_in=%d archive_total=%d path=%s",
        label_prefix,
        len(records),
        len(merged),
        path,
    )
    return len(merged)


# ---------------------------------------------------------------------------
# Response envelope normalisation
# ---------------------------------------------------------------------------


def normalise_bulletin_response(
    data: Any,
    source_label: str,
) -> list[dict[str, Any]]:
    """
    Normalise a bulletin API / CDN response into a flat list of bulletin dicts.

    Handles three envelope shapes seen across SLF and ALBINA:

    - A flat JSON array of bulletin dicts — returned as-is.
    - A ``{"bulletins": [...]}`` envelope dict — the inner list is returned.
    - A list of collection objects each containing a ``"bulletins"`` key
      (SLF-specific shape) — all inner lists are concatenated.

    Any other shape logs a warning and returns an empty list so callers
    do not need to handle ``None`` or unexpected types.

    Args:
        data: The parsed JSON response from the API or CDN.
        source_label: Short provider identifier used in warning messages
            (e.g. ``"SLF"``, ``"ALBINA"``).

    Returns:
        A flat list of bulletin dicts, possibly empty.

    """
    if isinstance(data, list):
        if len(data) > 0 and isinstance(data[0], dict) and "bulletins" in data[0]:
            # List of collection objects (SLF shape)
            return [b for collection in data for b in collection.get("bulletins", [])]
        # Flat list — covers both a plain bulletin array and an empty list.
        return data

    if isinstance(data, dict) and "bulletins" in data:
        result: list[dict[str, Any]] = data["bulletins"]
        return result

    logger.warning(
        "Unexpected %s response body shape — type=%s; returning []",
        source_label,
        type(data).__name__,
    )
    return []
