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
service's reading of a bulletin is stored beside it and served with it,
indexes the bulletin's target day and region codes, and refreshes the
service's day ratings (``apps.ingest.day_ratings``) for the pairs the
write touched. A row whose model predates the builder's
``RENDER_MODEL_VERSION``, or that was never indexed, is rebuilt when it is
next fetched, even when its CAAML has not changed, and
``rebuild_render_model`` does the same for one row on demand (the
``rebuild_source_render_models`` command walks the stale ones). A rebuild
that produces the stored values again writes nothing.

``source_caaml(bulletin_id)`` is the one way a host reads a stored
bulletin's CAAML (SNOW-1337): the feed never serves it, so a host's raw
surfaces — on the instance that runs the service — read it here, matched on
the shared ``bulletin_id``, and say "no data" when the service holds none.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.ingest.day_ratings import (
    INDEX_FIELDS,
    day_pairs,
    index_bulletin,
    refresh_source_day_ratings,
)
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


def caaml_envelope(caaml: dict[str, Any]) -> dict[str, Any]:
    """Wrap CAAML properties in the GeoJSON Feature envelope the raw surfaces serve.

    Args:
        caaml: The CAAML properties.

    Returns:
        ``{"type": "Feature", "geometry": None, "properties": caaml}``.

    """
    return {"type": "Feature", "geometry": None, "properties": caaml}


def source_caaml(bulletin_id: str) -> dict[str, Any] | None:
    """Return the CAAML the service stored for ``bulletin_id``, or None.

    Args:
        bulletin_id: The provider's bulletin id, shared with the host's row.

    Returns:
        The CAAML properties, or ``None`` when the service holds no such
        bulletin (expected on an instance that pulls its bulletins from
        another's feed).

    """
    return (
        SourceBulletin.objects.filter(bulletin_id=bulletin_id)
        .values_list("caaml", flat=True)
        .first()
    )


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

# The SourceBulletin columns derived from the CAAML on every write.
DERIVED_FIELDS: tuple[str, ...] = (
    "render_model",
    "render_model_version",
    *INDEX_FIELDS,
)


def _derive(row: SourceBulletin) -> bool:
    """Rebuild ``row``'s render model and day-rating index, without saving.

    Args:
        row: The bulletin, with its CAAML and derived columns loaded.

    Returns:
        True when any derived value changed.

    """
    before = [getattr(row, name) for name in DERIVED_FIELDS]
    row.render_model, row.render_model_version = render_model_for(row.caaml)
    index_bulletin(row)
    return before != [getattr(row, name) for name in DERIVED_FIELDS]


def rebuild_render_model(
    row: SourceBulletin,
    *,
    commit: bool = True,
    touched: set[tuple[str, date]] | None = None,
) -> str:
    """Rebuild one stored bulletin's render model with the current builder.

    Its day-rating index is rebuilt with it. The service's day ratings are
    not refreshed here, so a walk over many rows can refresh each day once:
    pass ``touched`` to collect the pairs to refresh afterwards.

    Args:
        row: The bulletin, with ``caaml`` and its derived columns loaded.
        commit: Save a changed model; otherwise only decide.
        touched: When given and the row changed, the (region code, day)
            pairs it governed before and governs now are added to it.

    Returns:
        ``REBUILD_FAILED`` when the build fails (the sentinel is saved if it
        changed); otherwise ``REBUILT`` when the model changed — and, with
        ``commit``, was saved, which moves ``updated_at`` so the feed serves
        the row again — or ``OUTCOME_UNCHANGED``.

    """
    prior = day_pairs(row)
    changed = _derive(row)
    if changed:
        if commit:
            row.save(update_fields=[*DERIVED_FIELDS, "updated_at"])
        if touched is not None:
            touched |= prior | day_pairs(row)
    if row.render_model_version == 0:
        return REBUILD_FAILED
    return REBUILT if changed else OUTCOME_UNCHANGED


@transaction.atomic
def store_bulletin(
    record: NormalisedBulletin, *, force: bool = False
) -> tuple[SourceBulletin, str]:
    """Write one fetched bulletin, if it is new or its content has changed.

    Every write derives the render model and the day-rating index, then
    refreshes the service's day ratings for the (region, day) pairs the
    bulletin governed before and governs now. Both happen in one
    transaction: a refresh that fails rolls the row back with it, so a
    retry with the same hash is not mistaken for unchanged and the
    ratings are refreshed then.

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
        row = SourceBulletin(
            bulletin_id=record.bulletin_id,
            provider=record.provider,
            caaml=record.caaml,
            pdf_url=record.pdf_url(),
            content_hash=digest,
            fetched_at=timezone.now(),
        )
        _derive(row)
        row.save()
        refresh_source_day_ratings(day_pairs(row))
        logger.debug("Stored new source bulletin %s", row)
        return row, OUTCOME_CREATED

    if row.content_hash == digest:
        changed = False
        if row.render_model_version != RENDER_MODEL_VERSION or row.target_date is None:
            # Built by an older builder, failed, or never indexed: a rebuild
            # that comes out the same (a failure that still fails) writes
            # nothing.
            prior = day_pairs(row)
            if _derive(row):
                row.save(update_fields=[*DERIVED_FIELDS, "updated_at"])
                refresh_source_day_ratings(prior | day_pairs(row))
                changed = True
        if force:
            pdf_url = record.pdf_url()
            if pdf_url != row.pdf_url:
                row.pdf_url = pdf_url
                row.save(update_fields=["pdf_url", "updated_at"])
                changed = True
        return row, OUTCOME_UPDATED if changed else OUTCOME_UNCHANGED

    prior = day_pairs(row)
    row.provider = record.provider
    row.caaml = record.caaml
    row.pdf_url = record.pdf_url()
    row.content_hash = digest
    row.fetched_at = timezone.now()
    _derive(row)
    row.save()
    refresh_source_day_ratings(prior | day_pairs(row))
    logger.debug("Updated source bulletin %s", row)
    return row, OUTCOME_UPDATED
