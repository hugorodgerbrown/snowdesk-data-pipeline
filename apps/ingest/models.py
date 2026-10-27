"""
apps/ingest/models.py — What ingest keeps of what the providers published.

One model, ``SourceBulletin`` (SNOW-1250): one row per bulletin a provider
published, holding its CAAML as normalised, the link to its PDF and a hash
of the CAAML. It is the record the data feed's ``bulletins`` endpoint serves
and the source the app's ``Bulletin`` rows are applied from.

**Not a ``BaseModel``.** ``apps/ingest`` imports no other ``apps.*`` package
(``docs/decisions/ingestion-is-a-rehostable-unit.md``), so the id, uuid and
timestamps ``BaseModel`` would give are declared here, with the same types
and defaults. The rest of the model kit is as CLAUDE.md asks: an admin
class, ``to_string()``, ``Meta.ordering`` and a custom queryset.

**``updated_at`` moves only when the content does.** ``store_bulletin``
writes a row only when its hash changes, so the feed's keyset on
``(updated_at, id)`` reads as "what changed since", and a re-fetch of the
same bulletin costs a client nothing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from django.db import models


def content_hash(caaml: dict[str, Any]) -> str:
    """Return the sha256 of a bulletin's CAAML in canonical JSON.

    Keys are sorted and separators fixed, so two dicts that compare equal
    always hash equal, whatever order a provider sent their keys in.

    Args:
        caaml: The bulletin's CAAML properties.

    Returns:
        The hex digest.

    """
    canonical = json.dumps(
        caaml, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class SourceBulletinQuerySet(models.QuerySet["SourceBulletin"]):
    """Queryset for ``SourceBulletin``."""

    def for_provider(self, provider: str) -> SourceBulletinQuerySet:
        """Return the bulletins one provider published.

        Args:
            provider: ``SLF``, ``ALBINA`` or ``METEOFRANCE``.

        Returns:
            The filtered queryset.

        """
        return self.filter(provider=provider)


class SourceBulletin(models.Model):
    """One bulletin as a provider published it, normalised to CAAML."""

    class Provider(models.TextChoices):
        """The provider a bulletin came from."""

        SLF = "SLF", "SLF (Switzerland)"
        ALBINA = "ALBINA", "ALBINA (Austria/South Tyrol/Trentino)"
        METEOFRANCE = "METEOFRANCE", "Météo-France (France)"

    id = models.BigAutoField(primary_key=True)
    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    bulletin_id = models.CharField(max_length=255, unique=True)
    provider = models.CharField(max_length=16, choices=Provider.choices)
    caaml = models.JSONField(
        help_text="The bulletin's CAAML properties, as normalised at fetch time."
    )
    pdf_url = models.URLField(blank=True, default="", max_length=500)
    content_hash = models.CharField(
        max_length=64,
        help_text="sha256 of the CAAML in canonical JSON; a re-fetch that "
        "matches it writes nothing.",
    )
    fetched_at = models.DateTimeField(
        help_text="When this content was fetched — the last time it changed."
    )

    objects = SourceBulletinQuerySet.as_manager()

    class Meta:
        """Model metadata."""

        ordering = ["-created_at"]
        indexes = [
            # The feed's keyset (apps/ingest/api/paging.py).
            models.Index(fields=["updated_at", "id"], name="ingest_srcb_keyset"),
        ]

    def __str__(self) -> str:
        """Return ``to_string()``."""
        return self.to_string()

    def to_string(self) -> str:
        """Return the provider and the bulletin id, e.g. ``SLF abc-123``."""
        return f"{self.provider} {self.bulletin_id}"
