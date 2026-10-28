"""
Copy every Bulletin into ingest's SourceBulletin (SNOW-1250).

From this release the ``bulletins`` data feed reads ``SourceBulletin``, so
the table must hold every bulletin production already serves before the
first request reaches it. Each row's CAAML is ``raw_data["properties"]``,
its provider is ``Bulletin.source`` (or, for a row predating that column,
the same ``customData`` marker ``detect_source`` reads), and its timestamps
are the bulletin's own — so a staging pull resuming from its watermark sees
nothing new rather than the whole archive at once.

It lives in the bulletins app, not in ingest: ingest's migrations must not
depend on an app a rehosted ingest would not carry
(docs/decisions/ingestion-is-a-rehostable-unit.md).

Batched by primary key and idempotent on ``bulletin_id``: a bulletin already
copied is skipped, so a re-run after a partial failure finishes the job.
A row with no CAAML or no recognisable provider is skipped and counted —
the feed cannot serve it, and inventing a provider would be worse.

The hash is computed inline rather than imported from
``apps.ingest.models``, so this migration keeps working however that code
changes; it must match ``content_hash`` byte for byte.
"""

import hashlib
import json
import logging

from django.db import migrations
from django.db.models import OuterRef, Subquery

logger = logging.getLogger(__name__)

BATCH = 500


def _provider(source, caaml):
    """Return the provider for a bulletin, or None when it cannot be told."""
    if source:
        return source
    custom = caaml.get("customData") or {}
    if "ALBINA" in custom or any(key.startswith("LWD_") for key in custom):
        return "ALBINA"
    if "MF" in custom:
        return "METEOFRANCE"
    if "CH" in custom:
        return "SLF"
    return None


def _hash(caaml):
    """Return the sha256 of ``caaml`` in canonical JSON (= content_hash)."""
    canonical = json.dumps(
        caaml, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def copy_bulletins(apps, schema_editor):
    """Create a SourceBulletin for every Bulletin that has none."""
    Bulletin = apps.get_model("bulletins", "Bulletin")
    SourceBulletin = apps.get_model("ingest", "SourceBulletin")

    stamp = Bulletin.objects.filter(bulletin_id=OuterRef("bulletin_id"))
    copied = skipped = 0
    last_pk = 0
    while True:
        batch = list(
            Bulletin.objects.filter(pk__gt=last_pk)
            .order_by("pk")
            .only("pk", "bulletin_id", "source", "raw_data", "pdf_url", "updated_at")[
                :BATCH
            ]
        )
        if not batch:
            break
        last_pk = batch[-1].pk
        have = set(
            SourceBulletin.objects.filter(
                bulletin_id__in=[b.bulletin_id for b in batch]
            ).values_list("bulletin_id", flat=True)
        )
        rows = []
        for bulletin in batch:
            if bulletin.bulletin_id in have:
                continue
            caaml = (bulletin.raw_data or {}).get("properties")
            provider = _provider(bulletin.source, caaml) if caaml else None
            if provider is None:
                skipped += 1
                continue
            rows.append(
                SourceBulletin(
                    bulletin_id=bulletin.bulletin_id,
                    provider=provider,
                    caaml=caaml,
                    pdf_url=bulletin.pdf_url or "",
                    content_hash=_hash(caaml),
                    fetched_at=bulletin.updated_at,
                )
            )
        SourceBulletin.objects.bulk_create(rows)
        # auto_now stamped each copy with the time of this migration; give
        # the batch the timestamps of the bulletins it was copied from, in
        # one UPDATE (.update() bypasses auto_now).
        SourceBulletin.objects.filter(
            bulletin_id__in=[row.bulletin_id for row in rows]
        ).update(
            created_at=Subquery(stamp.values("created_at")[:1]),
            updated_at=Subquery(stamp.values("updated_at")[:1]),
        )
        copied += len(rows)
    logger.info("Copied %d bulletin(s) to SourceBulletin", copied)
    if skipped:
        logger.warning(
            "Skipped %d bulletin(s) with no CAAML or no recognisable provider",
            skipped,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("bulletins", "0021_pipelinerun_partial_status"),
        ("ingest", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(copy_bulletins, migrations.RunPython.noop),
    ]
