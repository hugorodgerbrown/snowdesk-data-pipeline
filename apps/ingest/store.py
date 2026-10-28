"""
apps/ingest/store.py — Keeping what a provider published, once per change.

``store_bulletin(record, *, force=False)`` writes one ``NormalisedBulletin``
from a provider walk (``apps.ingest.runs``) into ``SourceBulletin`` and says
what it did (SNOW-1250):

* ``created`` — a bulletin id ingest has not seen before;
* ``updated`` — a known id whose CAAML hash has changed;
* ``unchanged`` — a known id with the same hash: nothing is written, so
  ``updated_at`` stays put and the feed does not send it again.

The PDF link is resolved only when a row is written, because for
Météo-France it costs a request to the archive index (``NormalisedBulletin``
resolves it lazily for exactly this reason). ``force`` rewrites the link of
an unchanged row — the one thing a re-run can learn that the hash cannot
see — and leaves the row alone when the link has not moved either.

Every write also builds the bulletin's render model (SNOW-1270), so the
service's reading of a bulletin is stored beside it and served with it. A
row whose model predates the builder's ``RENDER_MODEL_VERSION`` is rebuilt
when it is next fetched, even when its CAAML has not changed, and
``rebuild_render_model`` does the same for one row on demand (the
``rebuild_source_render_models`` command walks the stale ones). A rebuild
that produces the stored model again writes nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from apps.ingest.models import SourceBulletin, content_hash
from apps.ingest.normalise.fetcher_common import OUTCOME_CREATED, OUTCOME_UPDATED
from apps.ingest.normalise.render_model import (
    RENDER_MODEL_VERSION,
    RenderModelBuildError,
    build_render_model,
)
from apps.ingest.runs import NormalisedBulletin

logger = logging.getLogger(__name__)

OUTCOME_UNCHANGED = "unchanged"


def render_model_for(caaml: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Build a bulletin's render model, or the error sentinel when it fails.

    Args:
        caaml: The bulletin's CAAML properties.

    Returns:
        ``(render_model, version)``: the model at ``RENDER_MODEL_VERSION``,
        or ``({"version": 0, "error": …, "error_type": …}, 0)``.

    """
    try:
        return build_render_model(caaml), RENDER_MODEL_VERSION
    except RenderModelBuildError as exc:
        logger.warning(
            "Render model failed for bulletin %s: %s",
            caaml.get("bulletinID", "<unknown>"),
            exc,
        )
        return {
            "version": 0,
            "error": str(exc),
            "error_type": exc.__class__.__name__,
        }, 0


REBUILT = "rebuilt"
REBUILD_FAILED = "failed"


def rebuild_render_model(row: SourceBulletin, *, commit: bool = True) -> str:
    """Rebuild one stored bulletin's render model with the current builder.

    Args:
        row: The bulletin, with ``caaml``, ``render_model`` and
            ``render_model_version`` loaded.
        commit: Save a changed model; otherwise only decide.

    Returns:
        ``REBUILD_FAILED`` when the build fails (the sentinel is saved if it
        changed); otherwise ``REBUILT`` when the model changed — and, with
        ``commit``, was saved, which moves ``updated_at`` so the feed serves
        the row again — or ``OUTCOME_UNCHANGED``.

    """
    model, version = render_model_for(row.caaml)
    changed = model != row.render_model or version != row.render_model_version
    if changed and commit:
        row.render_model = model
        row.render_model_version = version
        row.save(update_fields=["render_model", "render_model_version", "updated_at"])
    if version == 0:
        return REBUILD_FAILED
    return REBUILT if changed else OUTCOME_UNCHANGED


def store_bulletin(
    record: NormalisedBulletin, *, force: bool = False
) -> tuple[SourceBulletin, str]:
    """Write one fetched bulletin, if it is new or its content has changed.

    Args:
        record: A bulletin from ``fetch_bulletins``.
        force: Re-resolve the PDF link of an unchanged bulletin, and write
            it when it differs.

    Returns:
        ``(row, outcome)``, the outcome one of ``OUTCOME_CREATED``,
        ``OUTCOME_UPDATED`` or ``OUTCOME_UNCHANGED``.

    """
    digest = content_hash(record.caaml)
    row = SourceBulletin.objects.filter(bulletin_id=record.bulletin_id).first()

    if row is None:
        model, version = render_model_for(record.caaml)
        row = SourceBulletin.objects.create(
            bulletin_id=record.bulletin_id,
            provider=record.provider,
            caaml=record.caaml,
            pdf_url=record.pdf_url(),
            content_hash=digest,
            fetched_at=timezone.now(),
            render_model=model,
            render_model_version=version,
        )
        logger.debug("Stored new source bulletin %s", row)
        return row, OUTCOME_CREATED

    if row.content_hash == digest:
        changed = False
        if row.render_model_version != RENDER_MODEL_VERSION:
            # Built by an older builder, or failed: a rebuild that comes out
            # the same (a failure that still fails) writes nothing.
            # A save moves updated_at, which is what "changed" means here.
            before = row.updated_at
            rebuild_render_model(row)
            changed = row.updated_at != before
        if force:
            pdf_url = record.pdf_url()
            if pdf_url != row.pdf_url:
                row.pdf_url = pdf_url
                row.save(update_fields=["pdf_url", "updated_at"])
                changed = True
        return row, OUTCOME_UPDATED if changed else OUTCOME_UNCHANGED

    row.provider = record.provider
    row.caaml = record.caaml
    row.pdf_url = record.pdf_url()
    row.content_hash = digest
    row.fetched_at = timezone.now()
    row.render_model, row.render_model_version = render_model_for(record.caaml)
    row.save()
    logger.debug("Updated source bulletin %s", row)
    return row, OUTCOME_UPDATED
