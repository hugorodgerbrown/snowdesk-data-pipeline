"""
apps/bulletins/management/commands/backfill_pdf_urls.py — backfill_pdf_urls command.

Back-fills the ``Bulletin.pdf_url`` field for all rows where it is currently
empty.  Dispatches to the appropriate per-source URL helper.  Source is
derived from ``bulletin.raw_data["properties"]["customData"]`` via
``render_model.detect_source()`` — **not** from ``render_model["source"]`` —
so that rows with stale ``render_model`` values (e.g. ``"euregio"`` written
by older pipeline versions) are still dispatched correctly.

  * SLF       → ``_slf_pdf_url(raw)``
  * ALBINA    → ``_albina_pdf_url(raw, region)`` — region taken from the
                  first raw ``regions`` entry's ``regionID`` prefix
  * METEOFRANCE → ``_meteofrance_pdf_url(massif_name, valid_date, session)``
                   massif_name read from ``customData["MF"]["massif"]``

Safe by default — read-only unless ``--commit`` is passed (CLAUDE.md Option A).
Idempotent — rows with an existing ``pdf_url`` are skipped unconditionally.
Uses ``bulk_update(..., ["pdf_url"], batch_size=500)`` when committing.

For Météo-France rows the command uses a shared ``requests.Session`` and
sleeps 0.2 s between index calls (polite rate-limiting; the upstream index
endpoint is being decommissioned, so we don't want to hammer it during a
large backfill).

Derived URLs are validated to start with ``https://`` before they are
accepted.  A URL that fails this check is logged as an error and never
persisted, even with ``--commit``.  This guards against unexpected values
arriving from external CAAML feeds being written directly to the database.
"""

from __future__ import annotations

import logging
import time
from argparse import ArgumentParser
from typing import Any

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.bulletins.models import Bulletin
from apps.bulletins.services.albina_fetcher import _albina_pdf_url
from apps.bulletins.services.meteofrance_fetcher import (
    _MF_USER_AGENT,
    _meteofrance_pdf_url,
)
from apps.bulletins.services.render_model import RenderModelBuildError, detect_source
from apps.bulletins.services.slf_fetcher import _slf_pdf_url

logger = logging.getLogger(__name__)

_MF_RATE_LIMIT_S = 0.2
_BATCH_SIZE = 500


def _albina_cdn_region(micro_region_id: str) -> str:
    """
    Derive the ALBINA CDN slot code from a micro-region ID.

    ALBINA CDN region codes (``settings.ALBINA_REGIONS``) are coarse-grained
    slots like ``"AT-07"`` or ``"IT-32-BZ"``.  The raw ``regionID`` values
    stored in bulletin payloads are micro-region identifiers such as
    ``"AT-07-02-02"`` or ``"IT-32-BZ-03"``.  This function maps from the
    micro-region granularity back to the CDN slot by checking whether the
    micro-region ID starts with each known slot prefix (longest first, to
    avoid ``"IT-32-BZ"`` being swallowed by a hypothetical ``"IT-32"``).

    Falls back to ``"EUREGIO"`` when no known prefix matches — the multi-region
    report is a safer default than forwarding a malformed CDN code.

    Args:
        micro_region_id: A raw ``regionID`` string from an ALBINA bulletin.

    Returns:
        The matching ALBINA CDN region code, or ``"EUREGIO"`` if none match.

    """
    # Sort by descending length so more-specific prefixes are checked first.
    candidates = sorted(settings.ALBINA_REGIONS, key=len, reverse=True)
    for code in candidates:
        if micro_region_id.startswith(code):
            return code
    return "EUREGIO"


class Command(BaseCommand):
    """Back-fill Bulletin.pdf_url for all rows where it is currently empty."""

    help = (
        "Back-fill Bulletin.pdf_url for rows where it is empty. "
        "Read-only by default; pass --commit to persist."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line flags."""
        parser.add_argument(
            "--commit",
            action="store_true",
            default=False,
            help="Persist the derived pdf_url values. Omit for a dry-run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Run the backfill."""
        commit: bool = options["commit"]
        verbosity: int = options.get("verbosity", 1)

        logger.info("backfill_pdf_urls started: commit=%s", commit)

        if verbosity >= 1:
            self.stdout.write("Scanning for bulletins with empty pdf_url …")

        qs = Bulletin.objects.exclude(pdf_url__gt="").order_by("id")
        total = qs.count()

        if verbosity >= 1:
            self.stdout.write(f"Found {total} bulletin(s) to process.")

        if total == 0:
            if verbosity >= 1:
                self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        counts, batch = _process_queryset(qs, commit)
        _log_summary(self, verbosity, commit, counts, len(batch) if not commit else 0)
        logger.info("backfill_pdf_urls finished: %s commit=%s", counts, commit)

        if counts["error"] > 0:
            raise CommandError(
                f"backfill_pdf_urls completed with {counts['error']} error(s);"
                " see logs."
            )


def _process_one(
    bulletin: Bulletin,
    mf_session: requests.Session,
    mf_call_count: int,
    counts: dict[str, int],
) -> str:
    """Derive the pdf_url for one bulletin and update counts in-place.

    Source is derived from ``customData`` keys via ``detect_source()`` rather
    than from ``render_model["source"]``.  This ensures rows with stale
    ``render_model`` values (e.g. legacy ``"euregio"`` written by older
    pipeline versions) are dispatched correctly.

    Returns the derived URL string (may be ``""`` on failure or unknown source).
    """
    raw = (bulletin.raw_data or {}).get("properties", {})

    # Detect source from customData — robust against stale render_model values.
    source_slug: str
    try:
        source_enum = detect_source(raw)
        source_slug = source_enum.value
    except RenderModelBuildError:
        source_slug = ""

    try:
        pdf_url = _derive_pdf_url(bulletin, source_slug, raw, mf_session, mf_call_count)
    except Exception as exc:
        logger.exception(
            "backfill_pdf_urls: error deriving URL for bulletin %s: %s",
            bulletin.bulletin_id,
            exc,
        )
        counts["error"] += 1
        return ""

    # Scheme guard: only accept https:// URLs.  Values originate from external
    # CAAML feeds; rejecting non-https results prevents unexpected schemes
    # (e.g. javascript:, http://) from being written to the database.
    if pdf_url and not pdf_url.startswith("https://"):
        logger.error(
            "backfill_pdf_urls: derived URL for bulletin %s has unexpected scheme"
            " (expected https://): %r — skipping",
            bulletin.bulletin_id,
            pdf_url,
        )
        counts["error"] += 1
        return ""

    if source_slug in ("slf", "albina", "meteofrance"):
        counts[source_slug] += 1
    else:
        counts["unknown"] += 1

    return pdf_url


def _process_queryset(
    qs: Any,
    commit: bool,
) -> tuple[dict[str, int], list[Bulletin]]:
    """Iterate over the queryset, derive pdf_urls, and optionally bulk-update.

    Returns the counts dict and any unflushed batch (non-empty only in dry-run).
    """
    mf_session = requests.Session()
    mf_session.headers["User-Agent"] = _MF_USER_AGENT

    counts: dict[str, int] = {
        "slf": 0,
        "albina": 0,
        "meteofrance": 0,
        "unknown": 0,
        "error": 0,
    }
    batch: list[Bulletin] = []
    mf_call_count = 0

    for bulletin in qs.iterator():
        raw_props = (bulletin.raw_data or {}).get("properties", {})
        try:
            is_mf = detect_source(raw_props) == Bulletin.Source.METEOFRANCE
        except RenderModelBuildError:
            is_mf = False
        pdf_url = _process_one(bulletin, mf_session, mf_call_count, counts)
        if is_mf:
            mf_call_count += 1

        if not pdf_url:
            continue  # helper returned "" — nothing to update

        bulletin.pdf_url = pdf_url
        batch.append(bulletin)

        if commit and len(batch) >= _BATCH_SIZE:
            Bulletin.objects.bulk_update(batch, ["pdf_url"], batch_size=_BATCH_SIZE)
            batch = []

    # Flush remaining batch.
    if commit and batch:
        Bulletin.objects.bulk_update(batch, ["pdf_url"], batch_size=_BATCH_SIZE)
        batch = []

    return counts, batch


def _derive_pdf_url(
    bulletin: Bulletin,
    source: str,
    raw: dict[str, Any],
    mf_session: requests.Session,
    mf_call_count: int,
) -> str:
    """
    Derive the pdf_url for a single bulletin using its source-specific helper.

    Args:
        bulletin: The Bulletin instance (used for issued_at and region info).
        source: The detected source slug (``"slf"``, ``"albina"``, or
            ``"meteofrance"``), derived from ``customData`` keys.
        raw: The inner CAAML properties dict from ``bulletin.raw_data["properties"]``.
        mf_session: Shared ``requests.Session`` for Météo-France index calls.
        mf_call_count: How many Météo-France index calls have been made so far;
            used to apply a polite 0.2 s delay between calls.

    Returns:
        The derived PDF URL, or ``""`` if unavailable or source is unknown.

    """
    if source == "slf":
        return _slf_pdf_url(raw)

    if source == "albina":
        regions = raw.get("regions") or []
        micro_region_id = regions[0].get("regionID", "") if regions else ""
        region = _albina_cdn_region(micro_region_id)
        return _albina_pdf_url(raw, region)

    if source == "meteofrance":
        if mf_call_count > 0:
            time.sleep(_MF_RATE_LIMIT_S)
        # Read the canonical upstream slug from customData.MF.massif (e.g.
        # "VANOISE"), not regions[0]["name"] which is the title-case display
        # name (e.g. "Vanoise") and does not match the MF index endpoint.
        massif_name: str = (
            raw.get("customData", {}).get("MF", {}).get("massif", "") or ""
        )
        valid_date = bulletin.valid_from.date()
        return _meteofrance_pdf_url(massif_name, valid_date, mf_session)

    logger.info(
        "backfill_pdf_urls: unknown source %r for bulletin %s — skipping",
        source,
        bulletin.bulletin_id,
    )
    return ""


def _log_summary(
    cmd: Command,
    verbosity: int,
    commit: bool,
    counts: dict[str, int],
    pending: int,
) -> None:
    """Write a human-readable summary to stdout."""
    if verbosity < 1:
        return

    mode = "Committed" if commit else "Dry-run"
    total_updated = sum(v for k, v in counts.items() if k not in ("unknown", "error"))

    if commit:
        msg = (
            f"{mode}: {total_updated} pdf_url(s) populated "
            f"(SLF={counts['slf']}, ALBINA={counts['albina']}, "
            f"MF={counts['meteofrance']}, unknown={counts['unknown']}, "
            f"errors={counts['error']})"
        )
        cmd.stdout.write(cmd.style.SUCCESS(msg))
    else:
        msg = (
            f"{mode}: would populate {total_updated} pdf_url(s) "
            f"(SLF={counts['slf']}, ALBINA={counts['albina']}, "
            f"MF={counts['meteofrance']}, unknown={counts['unknown']}, "
            f"errors={counts['error']}). "
            f"Re-run with --commit to persist."
        )
        cmd.stdout.write(msg)
