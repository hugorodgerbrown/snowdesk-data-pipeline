"""
bulletins/services/meteofrance_fetcher.py — Fetch and persist MeteoFrance bulletins.

Iterates over the configured MeteoFrance DPBRA massif IDs, fetching one XML
document per massif from the MeteoFrance public APIM::

    GET https://public-api.meteofrance.fr/public/DPBRA/v1/...
    apikey: <key>

Translates each XML via ``parse_dpbra_xml()`` and persists each bulletin via
the shared ``upsert_bulletin()`` pipeline.

HTTP 404 for a massif means "no bulletin today" and is treated as a clean
skip. Other non-2xx responses are logged as errors and counted as
``records_failed``. ``MeteoFranceDelegatedRegionError`` (e.g. massif 71 /
Andorre) is also a clean skip.

``run_meteofrance_pipeline``, ``latest_meteofrance_date``, and
``meteofrance_stash_writer`` are the public entry points that the management
command wires up via the ``BulletinSource`` registry.

Local-mirror support: when ``base_url`` starts with ``file://``, XMLs are
read from the file-system directory by filename ``massif-{NN:03d}.xml``
instead of making HTTP requests. This enables full dry-run / commit tests
without an API key.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings

from bulletins.models import Bulletin, PipelineRun
from bulletins.services.data_fetcher import UnknownRegionError, upsert_bulletin
from bulletins.services.meteofrance_translator import (
    MeteoFranceDelegatedRegionError,
    MeteoFranceTranslationError,
    parse_dpbra_xml,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30  # seconds
_ONE_DAY = timedelta(days=1)


# ---------------------------------------------------------------------------
# HTTP / local-mirror fetcher
# ---------------------------------------------------------------------------


def fetch_meteofrance_bulletin(
    massif_id: int,
    base_url: str,
    api_key: str,
) -> bytes | None:
    """
    Fetch the DPBRA XML for one massif from the MeteoFrance APIM.

    If ``base_url`` starts with ``file://``, the function reads the XML from
    the local filesystem at ``<directory>/massif-{massif_id:03d}.xml`` so
    integration tests can run without a live API key.

    Args:
        massif_id: The MeteoFrance integer massif ID (1..74).
        base_url: The API base URL, or a ``file://`` directory URI for local
            mirror use.
        api_key: The MeteoFrance APIM key sent as the ``apikey`` header.
            Unused for ``file://`` base URLs.

    Returns:
        The raw XML bytes, or ``None`` when the API returns 404 (no
        bulletin for this massif today).

    Raises:
        RuntimeError: ``base_url`` is a live HTTP(S) URL and ``api_key``
            is empty.
        requests.HTTPError: The API returns a non-2xx, non-404 status code.

    """
    if base_url.startswith("file://"):
        return _read_local_mirror(massif_id, base_url)

    if not api_key:
        raise RuntimeError(
            "METEOFRANCE_API_KEY must be set when base_url is not a file:// mirror."
        )

    url = f"{base_url.rstrip('/')}/massif/{massif_id}/BRA"
    logger.debug(
        "Fetching MeteoFrance bulletin: massif=%d url=%s",
        massif_id,
        url,
    )

    response = requests.get(
        url,
        headers={"apikey": api_key},
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code == 404:
        logger.debug("No bulletin for massif %d today (404)", massif_id)
        return None

    response.raise_for_status()
    return response.content


def _read_local_mirror(massif_id: int, base_url: str) -> bytes | None:
    """
    Read a DPBRA XML from the local mirror directory.

    Constructs the path ``<mirror_dir>/massif-{massif_id:03d}.xml`` and
    returns the file contents. Returns ``None`` when the file does not exist,
    mirroring the HTTP-404 semantics of the live path.

    Args:
        massif_id: The MeteoFrance integer massif ID.
        base_url: A ``file://`` URI pointing at the mirror directory.

    Returns:
        The raw XML bytes, or ``None`` when the file is absent.

    """
    parsed = urlparse(base_url)
    mirror_dir = Path(parsed.netloc + parsed.path)
    xml_path = mirror_dir / f"massif-{massif_id:03d}.xml"
    if not xml_path.exists():
        logger.debug("Local mirror: no file for massif %d at %s", massif_id, xml_path)
        return None
    return xml_path.read_bytes()


# ---------------------------------------------------------------------------
# Pipeline run
# ---------------------------------------------------------------------------


def run_meteofrance_pipeline(
    start: date,
    end: date,
    triggered_by: str = "unknown",
    dry_run: bool = False,
    force: bool = False,
    base_url: str | None = None,
    on_fetched: Callable[[dict[str, Any]], None] | None = None,
    delay: float = 0.0,
    massif_ids: tuple[int, ...] | None = None,
) -> PipelineRun:
    """
    Orchestrate a full MeteoFrance bulletin ingest for one calendar day.

    Iterates over ``massif_ids`` (defaulting to
    ``settings.METEOFRANCE_MASSIF_IDS``), fetches the DPBRA XML for each,
    translates it, and calls ``upsert_bulletin()`` to persist.

    Note: DPBRA is a one-issue-per-day product with no date-range API. The
    ``start`` and ``end`` arguments are accepted to match the
    ``BulletinSource.pipeline_fn`` signature, but only today's live bulletin
    is fetched for each massif regardless of the range. For historical
    backfill, a separate approach (e.g. the multi-coop CSV archive) is
    needed.

    Args:
        start: First date to include (inclusive). Passed through for
            signature compatibility; the live API only serves today.
        end: Last date to include (inclusive). Same caveat as ``start``.
        triggered_by: Human-readable label for who/what triggered the run.
        dry_run: If ``True``, fetch and translate but do not write to the DB.
        force: If ``True``, upsert bulletins that already exist in the DB.
        base_url: Override for the API base URL. ``None`` defers to
            ``settings.METEOFRANCE_API_BASE_URL`` (or
            ``settings.METEOFRANCE_API_LOCAL_MIRROR_URL`` when non-empty).
        on_fetched: Optional per-bulletin callback invoked for each
            successfully translated dict, before dry-run / upsert decisions.
            The ``--stash`` flag wires this to a list collector.
        delay: Seconds to sleep between successive API requests.
        massif_ids: Override the massif ID set. Falls back to
            ``settings.METEOFRANCE_MASSIF_IDS``.

    Returns:
        The completed (or failed) ``PipelineRun`` instance.

    """
    _default_massif_ids: tuple[int, ...] = getattr(
        settings, "METEOFRANCE_MASSIF_IDS", ()
    )
    resolved_massif_ids: tuple[int, ...] = (
        massif_ids if massif_ids is not None else _default_massif_ids
    )
    resolved_base_url = _resolve_base_url(base_url)
    api_key: str = getattr(settings, "METEOFRANCE_API_KEY", "") or ""

    run = PipelineRun.objects.create(triggered_by=triggered_by)
    run.mark_running()

    counts: dict[str, int] = {
        "created": 0,
        "updated": 0,
        "skipped": 0,
        "delegated": 0,
        "failed": 0,
    }
    request_count = 0

    try:
        logger.info(
            "MeteoFrance pipeline run %s: %d massifs force=%s dry_run=%s",
            run.pk,
            len(resolved_massif_ids),
            force,
            dry_run,
        )

        for i, massif_id in enumerate(resolved_massif_ids):
            if delay > 0 and i > 0:
                time.sleep(delay)

            outcome = _process_massif(
                massif_id=massif_id,
                run=run,
                base_url=resolved_base_url,
                api_key=api_key,
                dry_run=dry_run,
                force=force,
                on_fetched=on_fetched,
            )
            counts[outcome] += 1
            request_count += 1

    except Exception as exc:
        run.mark_failed(exc)
        return run

    logger.info(
        "MeteoFrance pipeline run %s finished: %d requests, "
        "%d created, %d updated, %d skipped, %d delegated, %d failed",
        run.pk,
        request_count,
        counts["created"],
        counts["updated"],
        counts["skipped"],
        counts["delegated"],
        counts["failed"],
    )

    if dry_run:
        run.mark_success(0, 0)
    else:
        run.mark_success(counts["created"], counts["updated"])

    return run


def _resolve_base_url(base_url: str | None) -> str:
    """
    Resolve the effective API base URL for this pipeline run.

    Priority: explicit ``base_url`` parameter → local mirror setting → live
    setting.

    Args:
        base_url: Explicit override from the caller, or ``None``.

    Returns:
        The resolved base URL string.

    """
    if base_url is not None:
        return base_url

    local_mirror: str = getattr(settings, "METEOFRANCE_API_LOCAL_MIRROR_URL", "") or ""
    if local_mirror:
        return local_mirror

    return str(getattr(settings, "METEOFRANCE_API_BASE_URL", ""))


def _process_massif(
    massif_id: int,
    run: PipelineRun,
    base_url: str,
    api_key: str,
    *,
    dry_run: bool,
    force: bool,
    on_fetched: Callable[[dict[str, Any]], None] | None,
) -> str:
    """
    Fetch, translate, and optionally persist one massif bulletin.

    Returns a short outcome tag: ``"created"``, ``"updated"``,
    ``"skipped"``, ``"delegated"``, or ``"failed"``.

    Args:
        massif_id: The MeteoFrance integer massif ID.
        run: The active ``PipelineRun`` instance.
        base_url: The API or local-mirror base URL.
        api_key: The MeteoFrance APIM key.
        dry_run: When ``True``, log and count without writing.
        force: When ``True``, upsert even if the bulletin already exists.
        on_fetched: Optional callback called for each translated dict.

    """
    fetch_result = _fetch_xml(massif_id, run, base_url, api_key)
    if isinstance(fetch_result, str):
        return fetch_result  # "skipped" or "failed"
    xml_bytes = fetch_result

    translate_result = _translate_xml(massif_id, run, xml_bytes)
    if isinstance(translate_result, str):
        return translate_result  # "delegated" or "failed"
    raw = translate_result

    bulletin_id: str = raw.get("bulletinID", "")

    if on_fetched is not None:
        on_fetched(raw)

    if dry_run:
        logger.info("[dry-run] Would store MeteoFrance %s", bulletin_id)
        return "created"

    if not force and Bulletin.objects.filter(bulletin_id=bulletin_id).exists():
        logger.debug("Skipping existing MeteoFrance bulletin %s", bulletin_id)
        return "skipped"

    return _persist_bulletin(massif_id, run, raw, bulletin_id)


def _fetch_xml(
    massif_id: int,
    run: PipelineRun,
    base_url: str,
    api_key: str,
) -> bytes | str:
    """
    Fetch the raw XML bytes for one massif, returning an outcome tag on error.

    Returns the raw XML bytes on success, ``"skipped"`` when the API returns
    404, or ``"failed"`` when an HTTP or configuration error occurs.

    Args:
        massif_id: The MeteoFrance integer massif ID.
        run: The active ``PipelineRun`` instance (updated on failure).
        base_url: The API or local-mirror base URL.
        api_key: The MeteoFrance APIM key.

    """
    try:
        xml_bytes = fetch_meteofrance_bulletin(massif_id, base_url, api_key)
    except (requests.HTTPError, RuntimeError) as exc:
        logger.error("Error fetching MeteoFrance massif %d: %s", massif_id, exc)
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return "failed"

    if xml_bytes is None:
        logger.info("No bulletin for MeteoFrance massif %d today (404)", massif_id)
        return "skipped"

    return xml_bytes


def _translate_xml(
    massif_id: int,
    run: PipelineRun,
    xml_bytes: bytes,
) -> dict[str, Any] | str:
    """
    Translate DPBRA XML to a CAAML dict, returning an outcome tag on error.

    Returns the translated CAAML dict on success, ``"delegated"`` when the
    massif is delegated, or ``"failed"`` on a translation error.

    Args:
        massif_id: The MeteoFrance integer massif ID.
        run: The active ``PipelineRun`` instance (updated on failure).
        xml_bytes: The raw DPBRA XML bytes to translate.

    """
    try:
        return parse_dpbra_xml(xml_bytes)
    except MeteoFranceDelegatedRegionError as exc:
        logger.info("MeteoFrance massif %d is delegated — skipping: %s", massif_id, exc)
        return "delegated"
    except MeteoFranceTranslationError as exc:
        logger.error("Translation error for MeteoFrance massif %d: %s", massif_id, exc)
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return "failed"


def _persist_bulletin(
    massif_id: int,
    run: PipelineRun,
    raw: dict[str, Any],
    bulletin_id: str,
) -> str:
    """
    Upsert a translated bulletin dict and return an outcome tag.

    Returns ``"created"`` or ``"updated"`` on success, ``"failed"`` on any
    exception.

    Args:
        massif_id: The MeteoFrance integer massif ID (for error logging).
        run: The active ``PipelineRun`` instance (updated on failure).
        raw: The translated CAAML dict to persist.
        bulletin_id: The ``bulletinID`` string (for error logging).

    """
    try:
        created = upsert_bulletin(raw, run)
    except UnknownRegionError as exc:
        logger.error(
            "Unknown region for MeteoFrance massif %d bulletin %s: %s",
            massif_id,
            bulletin_id,
            exc,
        )
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return "failed"
    except Exception as exc:
        logger.exception(
            "Failed to upsert MeteoFrance bulletin %s: %s", bulletin_id, exc
        )
        run.records_failed += 1
        run.save(update_fields=["records_failed"])
        return "failed"

    return "created" if created else "updated"


# ---------------------------------------------------------------------------
# Latest-date helper (used by the management command default start-date)
# ---------------------------------------------------------------------------


def latest_meteofrance_date() -> date | None:
    """
    Return the most recent ``valid_from`` date of any MeteoFrance bulletin in the DB.

    Filters on bulletins whose ``bulletin_id`` starts with ``"FR-"`` to
    identify MeteoFrance bulletins — consistent with the approach used in
    ``latest_euregio_date()`` which filters on render_model source.

    Used by the management command to derive the default ``--start-date``
    (resume from where the last run left off). Returns ``None`` when no
    MeteoFrance bulletins exist yet.

    Returns:
        The latest ``valid_from.date()`` across all MeteoFrance ``Bulletin``
        rows, or ``None`` when the table has no MeteoFrance rows.

    """
    result = (
        Bulletin.objects.filter(bulletin_id__startswith="FR-")
        .order_by("-valid_from")
        .values_list("valid_from", flat=True)
        .first()
    )
    if result is None:
        return None
    return result.date()


# ---------------------------------------------------------------------------
# Stash writer (used by the --stash flag)
# ---------------------------------------------------------------------------


def meteofrance_stash_writer(records: list[dict[str, Any]], path: Path) -> int:
    """
    Merge ``records`` into the on-disk MeteoFrance archive and return the new size.

    Reads the existing archive at ``path`` (if it exists), overlays the
    supplied records (later ``bulletinID`` wins), sorts ascending by
    ``validTime.startTime``, and writes the result back atomically via a
    sibling ``.tmp`` file plus ``os.replace``.

    Args:
        records: Raw MeteoFrance CAAML dicts collected during a pipeline run.
        path: Filesystem path to the MeteoFrance archive NDJSON file.

    Returns:
        The total number of records in the archive after the merge.

    """
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    record = json.loads(stripped)
                    bid = record.get("bulletinID", "")
                    if bid:
                        existing[bid] = record

    for record in records:
        bid = record.get("bulletinID", "")
        if bid:
            existing[bid] = record

    merged = sorted(
        existing.values(),
        key=lambda r: (r.get("validTime") or {}).get("startTime", ""),
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for record in merged:
            fh.write(json.dumps(record) + "\n")
    os.replace(tmp, path)

    logger.info(
        "meteofrance_stash_writer: records_in=%d archive_total=%d path=%s",
        len(records),
        len(merged),
        path,
    )
    return len(merged)
