# bulletins/services/meteofrance_archive_loader.py — Service for loading the
# Météo-France BRA NDJSON archive into the Snowdesk database.
#
# Provides ``load_meteofrance_archive(lines, *, commit, triggered_by)`` which is called
# by both the Django admin upload view (SNOW-227) and the offline script
# ``scripts/meteofrance-archive/load_archive.py``.
#
# Accepts any ``Iterable[str]`` so the admin view can pass a file object
# wrapped in ``io.TextIOWrapper`` while the script passes a file opened from
# the filesystem.
#
# Two impedance mismatches between the NDJSON shape and ``upsert_bulletin``
# are fixed at load time:
#   1. ``regionID`` translation: ``FR-{MASSIF_SLUG}`` → ``FR-{NN}`` using
#      ``SLUG_TO_CODE`` from ``bulletins.services.meteofrance_massifs``.
#   2. ``bulletinID`` synthesis: the CAAML builder does not write one;
#      ``upsert_bulletin`` requires one.  Synthesised as
#      ``FR-{NN:02d}-{covered date}-{publication timestamp}``.
#
# The publication timestamp is what makes the id unique.  Météo-France issues
# more than one BRA per massif per covered day — typically the previous evening
# and a morning refresh — so ``FR-{NN}-{covered date}`` alone collided for 105
# massif-days across the committed archive, and ``upsert_bulletin``'s
# ``update_or_create`` silently overwrote one issue with the other, the winner
# decided by line order in the file.  56 of those pairs differ in structured
# forecast data (danger ratings, elevation bounds, tendency, wind), so real
# content was being dropped (SNOW-559).
#
# Keying on ``(massif, covered date, publicationTime)`` is exact rather than
# approximate: across the 4,671 archived records it yields 4,639 ids, matching
# the 4,639 distinct PDFs by checksum, with no timestamp covering two distinct
# PDFs.  The 32 records that do still coalesce are byte-identical duplicate
# downloads and should coalesce.
"""Service for loading the Météo-France BRA NDJSON archive into the database."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from bulletins.services.meteofrance_identity import compact_publication_stamp
from bulletins.services.meteofrance_massifs import SLUG_TO_CODE, _normalise_slug

logger = logging.getLogger(__name__)


@dataclass
class LoadResult:
    """Summary counters returned by ``load_meteofrance_archive``."""

    total: int
    bad_shape: int
    unknown_slug: int
    would_upsert: int
    created: int
    updated: int
    failed: int
    pipeline_run_id: int | None
    # Records that coalesced onto an id already seen with identical content —
    # duplicate downloads of one PDF, which is benign.
    duplicates: int = 0
    # Records that coalesced onto an id already seen with *different* content —
    # two distinct bulletins claiming one identity.  Never expected; counted
    # toward ``failed`` so cron and CI notice (SNOW-559).
    collisions: int = 0

    def as_summary(self) -> str:
        """Return a human-readable one-line summary for admin messages and logs.

        Returns:
            A short string suitable for display in the Django admin message
            banner (e.g. ``"Loaded 4998 lines: 4995 created, 0 updated, …"``).

        """
        if self.pipeline_run_id is None:
            # Dry-run mode.
            return (
                f"[dry-run] {self.total} lines: "
                f"{self.would_upsert} would-upsert, "
                f"{self.unknown_slug} unknown-slug, "
                f"{self.bad_shape} bad-shape, "
                f"{self.duplicates} duplicate, "
                f"{self.collisions} id-collision."
            )
        return (
            f"Loaded {self.total} lines: "
            f"{self.created} created, "
            f"{self.updated} updated, "
            f"{self.unknown_slug} unknown-slug, "
            f"{self.bad_shape} bad-shape, "
            f"{self.duplicates} duplicate, "
            f"{self.collisions} id-collision, "
            f"{self.failed} failed "
            f"(PipelineRun #{self.pipeline_run_id})."
        )


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
        # bulletinID → payload fingerprint, so a genuine collision within one
        # file can be told from a duplicate download of the same PDF.
        self.fingerprints: dict[str, str] = {}
        self.duplicates = 0
        self.collisions = 0


def _payload_fingerprint(properties: dict[str, Any]) -> str:
    """Return a stable digest of an envelope's forecast content.

    ``bulletinID`` is excluded — it is what two colliding records already agree
    on, so including it would tell us nothing. Everything else is compared,
    including timestamps, so a difference anywhere in the payload counts.

    Args:
        properties: The ``properties`` sub-dict of a CAAML GeoJSON Feature.

    Returns:
        A hex digest of the canonicalised payload.

    """
    payload = {key: value for key, value in properties.items() if key != "bulletinID"}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _check_for_collision(
    bulletin_id: str,
    properties: dict[str, Any],
    line_number: int,
    counts: _Counts,
) -> bool:
    """Record this id's fingerprint and report whether it is a real collision.

    Two records resolving to one id are expected in one case only: the archive
    contains 32 byte-identical duplicate downloads, where the same PDF was
    fetched twice.  Those coalesce harmlessly and are counted, not warned about.

    Anything else means two *different* bulletins claim one identity, which is
    the failure mode this id scheme exists to prevent — so it warns and is
    counted as a failure, making it visible in ``records_failed`` rather than
    silently losing a forecast (SNOW-559, scope item 6).

    Args:
        bulletin_id: The synthesised id.
        properties: The envelope being processed.
        line_number: 1-based line counter for log messages.
        counts: Mutable counter object updated by this call.

    Returns:
        ``True`` when this is a genuine collision with differing content.

    """
    fingerprint = _payload_fingerprint(properties)
    previous = counts.fingerprints.get(bulletin_id)
    if previous is None:
        counts.fingerprints[bulletin_id] = fingerprint
        return False
    if previous == fingerprint:
        counts.duplicates += 1
        logger.debug(
            "Line %d: %s duplicates an identical earlier record",
            line_number,
            bulletin_id,
        )
        return False
    counts.collisions += 1
    logger.warning(
        "Line %d: %s collides with an earlier record that has different content "
        "— two distinct bulletins share one identity",
        line_number,
        bulletin_id,
    )
    return True


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
    return cast("dict[str, Any]", envelope["properties"])


def _slug_to_region_id(slug: str) -> str:
    """Return the canonical ``FR-{NN}`` region identifier for a normalised slug.

    Args:
        slug: Already-normalised (uppercased, hyphen-joined) massif slug.

    Returns:
        Zero-padded region identifier (e.g. ``"FR-01"``).

    Raises:
        KeyError: The slug is not present in ``SLUG_TO_CODE``.

    """
    code = SLUG_TO_CODE[slug]
    return f"FR-{code:02d}"


def _fixup_envelope(
    properties: dict[str, Any],
) -> str | None:
    """Translate ``regionID`` and synthesise ``bulletinID`` for one envelope.

    Mutates ``properties`` in place:
    - ``regions[0]["regionID"]`` is translated from ``FR-{SLUG}`` to
      ``FR-{NN}`` using ``SLUG_TO_CODE`` (after normalising the raw slug
      via ``_normalise_slug`` to fold the space/hyphen variants used by
      the upstream PDFs onto the canonical form).
    - ``bulletinID`` is synthesised as
      ``FR-{NN}-{customData.MF.date}-{publicationTime}``.

    Args:
        properties: The ``properties`` sub-dict of a CAAML GeoJSON Feature.

    Returns:
        The synthesised ``bulletinID`` on success, or ``None`` if the slug
        is unknown or required fields are missing.

    """
    try:
        raw_slug: str = properties["customData"]["MF"]["massif"]
        date_str: str = properties["customData"]["MF"]["date"]
    except KeyError, TypeError:
        logger.warning("Missing customData.MF.massif or customData.MF.date — skipping")
        return None

    published = compact_publication_stamp(properties.get("publicationTime"))
    if published is None:
        # Without a publication timestamp the record cannot be told apart from
        # the day's other issue, so admitting it would reintroduce the silent
        # overwrite this id scheme exists to prevent.
        logger.warning(
            "Missing or unparseable publicationTime for massif %r on %s — skipping",
            raw_slug,
            date_str,
        )
        return None

    slug = _normalise_slug(raw_slug)
    try:
        region_id = _slug_to_region_id(slug)
    except KeyError:
        logger.warning("Unknown massif slug %r — skipping", raw_slug)
        return None

    try:
        regions: list[dict[str, str]] = properties["regions"]
        regions[0]["regionID"] = region_id
    except KeyError, IndexError, TypeError:
        logger.warning("Missing or malformed regions list for slug %r — skipping", slug)
        return None

    bulletin_id = f"{region_id}-{date_str}-{published}"
    properties["bulletinID"] = bulletin_id
    return bulletin_id


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
        commit: If ``True``, call ``upsert_bulletin``; otherwise dry-run.
        run: Active ``PipelineRun`` (or ``None`` in dry-run mode).
        counts: Mutable counter object updated by this call.

    """
    from bulletins.services.slf_fetcher import (
        NoResolvableRegionsError,
        upsert_bulletin,
    )

    properties = _parse_envelope(raw_line, line_number)
    if properties is None:
        counts.bad_shape += 1
        _record_run_failure(run)
        return

    bulletin_id = _fixup_envelope(properties)
    if bulletin_id is None:
        raw_slug = properties.get("customData", {}).get("MF", {}).get("massif", "")
        if raw_slug and _normalise_slug(raw_slug) not in SLUG_TO_CODE:
            counts.unknown_slug += 1
        else:
            counts.bad_shape += 1
        _record_run_failure(run)
        return

    # A real collision is a per-row failure, but the row is still upserted:
    # dropping it would lose a forecast, and which of the two "wins" is exactly
    # the ambiguity being reported.  The warning plus records_failed is the
    # signal (SNOW-559).
    if _check_for_collision(bulletin_id, properties, line_number, counts):
        _record_run_failure(run)

    if not commit:
        logger.debug("[dry-run] Would upsert %s", bulletin_id)
        counts.would_upsert += 1
        return

    # A row whose regions are all unresolvable is a per-row failure, not a
    # reason to abandon the rest of the file (SNOW-547) — same posture as the
    # unknown-slug and bad-shape branches above, and as the ALBINA / MF live
    # fetchers, which already catch broadly around upsert_bulletin.
    try:
        created = upsert_bulletin(properties, run)
    except NoResolvableRegionsError:
        logger.exception("Line %d: skipping %s", line_number, bulletin_id)
        # _record_run_failure alone is enough to surface this: it feeds
        # LoadResult.failed, which the admin already warns on.
        _record_run_failure(run)
        return

    if created:
        counts.created += 1
        logger.debug("Created %s", bulletin_id)
    else:
        counts.updated += 1
        logger.debug("Updated %s", bulletin_id)


def _walk_lines(
    lines: Iterable[str],
    commit: bool,
    run: Any,
) -> _Counts:
    """Iterate every non-empty line, returning final counters.

    Args:
        lines: Iterable of raw text lines (e.g. an open file, a list of
            strings, or an ``io.TextIOWrapper`` over an uploaded file).
        commit: Whether to write to the database.
        run: Active ``PipelineRun`` (or ``None`` in dry-run mode).

    Returns:
        Populated ``_Counts`` instance.

    """
    counts = _Counts()
    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        counts.total += 1
        _process_line(raw_line, counts.total, commit, run, counts)
    return counts


def load_meteofrance_archive(
    lines: Iterable[str],
    *,
    commit: bool,
    triggered_by: str = "meteofrance-archive-admin",
) -> LoadResult:
    """Process MF NDJSON lines; in commit mode opens a PipelineRun and upserts.

    This is the canonical entry point for loading the Météo-France BRA NDJSON
    archive.  It is called by both the Django admin upload view and the offline
    script in ``scripts/meteofrance-archive/load_archive.py``.

    In dry-run mode (``commit=False``) the function validates every line and
    returns a ``LoadResult`` without writing anything to the database.

    In commit mode (``commit=True``) the function opens a ``PipelineRun``,
    calls ``upsert_bulletin`` for each valid row, and marks the run
    ``SUCCESS`` (or ``FAILED`` on an unexpected exception).  Per-row failures
    (unknown slug, missing fields) do not abort the run — they increment
    counters and continue.

    Args:
        lines: Iterable of raw NDJSON text lines.  The caller is responsible
            for opening and closing the underlying file or IO object.
        commit: If ``True``, write to the database.  If ``False``, validate
            only (no DB writes).
        triggered_by: Label stored on the ``PipelineRun`` row — identifies
            the caller (e.g. ``"admin upload"`` or
            ``"meteofrance-archive-backfill"``).

    Returns:
        A ``LoadResult`` dataclass with per-category counters and the
        ``PipelineRun`` primary key (``None`` in dry-run mode).

    Raises:
        Exception: Re-raises any unexpected exception after marking the
            ``PipelineRun`` as ``FAILED`` (commit mode only).

    """
    from bulletins.models import PipelineRun

    run: PipelineRun | None = None
    if commit:
        run = PipelineRun.objects.create(triggered_by=triggered_by)
        run.mark_running()
        logger.info("Opened PipelineRun %s (triggered_by=%r)", run.pk, triggered_by)

    try:
        counts = _walk_lines(lines, commit, run)
    except Exception as exc:
        if run is not None:
            run.mark_failed(exc)
        else:
            logger.exception("Fatal error during MF archive load: %s", exc)
        raise

    if run is not None:
        run.mark_success(counts.created, counts.updated)
        logger.info(
            "Load complete: %d lines, %d created, %d updated, "
            "%d unknown-slug, %d bad-shape",
            counts.total,
            counts.created,
            counts.updated,
            counts.unknown_slug,
            counts.bad_shape,
        )

    return LoadResult(
        total=counts.total,
        bad_shape=counts.bad_shape,
        unknown_slug=counts.unknown_slug,
        would_upsert=counts.would_upsert,
        created=counts.created,
        updated=counts.updated,
        failed=run.records_failed if run is not None else 0,
        pipeline_run_id=run.pk if run is not None else None,
        duplicates=counts.duplicates,
        collisions=counts.collisions,
    )
