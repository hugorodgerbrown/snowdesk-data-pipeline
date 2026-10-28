"""
apps/regions/services/reference.py — Mirror the estate into the service (SNOW-1271).

Until the service has its own editor, production is where passes and
resorts are curated: the resort sheet, ``import_resorts`` and the staff
map-admin overlays all write production's ``Pass`` and ``Resort``. This
module copies that estate into the service's tables, as SNOW-1253's
``mirror_reference_locations`` does for the weather stations:

  mirror_passes()
      Every ``Pass`` → ``SourcePass``, as ``{"name", "website"}``.

  mirror_resorts()
      Every ``Resort`` with a slug → ``SourceResort``, as the v1 resort
      item with every relation named by the service's uuid: its pin and
      location roles by ``Location.source_uuid``, its passes by
      ``Pass.source_uuid``. Lists are sorted, so the same resort always
      hashes the same.

  mirror_reference_data()
      All three, places first: a resort's document names its places by
      ``source_uuid``, which the location mirror is what sets.

A row with no ``source_uuid`` is given its own ``uuid`` — this instance is
where it is curated, so its id is the service's id.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.db.models import F, Prefetch

from apps.data_feed.contract import RESORT_FIELDS, dump_fields
from apps.ingest.public import (
    DocumentRecord,
    SourcePass,
    SourceResort,
    sync_source_documents,
)
from apps.locations.models import Location, ResortLocation
from apps.regions.models import MicroRegion, Pass, Resort
from apps.weather.services.reference import mirror_reference_locations

logger = logging.getLogger(__name__)

# A resort document's own columns: every one the v1 feed sends but the
# service's id, which is the row's key rather than part of its content.
DOCUMENT_FIELDS: tuple[str, ...] = tuple(f for f in RESORT_FIELDS if f != "source_uuid")


def _link(model: type[Pass] | type[Resort]) -> int:
    """Give every row of ``model`` without a ``source_uuid`` its own ``uuid``.

    Args:
        model: ``Pass`` or ``Resort``.

    Returns:
        How many rows were linked.

    """
    return model.objects.filter(source_uuid__isnull=True).update(source_uuid=F("uuid"))


def _place(location: Location | None) -> str | None:
    """Return a place's service uuid as a string, or None for no place.

    Args:
        location: A public location (so mirrored, so carrying a
            ``source_uuid``), or None.

    Returns:
        The uuid string, or None.

    Raises:
        ValueError: The location carries no ``source_uuid`` — it is not
            public, which a resort's pin and roles always are.

    """
    if location is None:
        return None
    if location.source_uuid is None:
        raise ValueError(f"location {location.pk} has no source_uuid")
    return str(location.source_uuid)


def resort_document(resort: Resort) -> dict[str, Any]:
    """Return ``resort`` as the document the service stores.

    Args:
        resort: A row with ``region``, ``location``, ``passes`` and
            ``resort_locations`` (with their locations) loaded.

    Returns:
        The document.

    """
    return {
        "fields": dump_fields(resort, DOCUMENT_FIELDS),
        "region": resort.region.region_id,
        "location": _place(resort.location),
        "passes": sorted(str(p.source_uuid) for p in resort.passes.all()),
        "locations": sorted(
            (
                {
                    "location": _place(link.location),
                    "role": link.role,
                    "is_primary": link.is_primary,
                }
                for link in resort.resort_locations.all()
            ),
            key=lambda link: (link["location"], link["role"]),
        ),
    }


def mirror_passes() -> dict[str, int]:
    """Make the service's pass list equal this instance's ``Pass`` rows.

    Returns:
        ``sync_source_documents``' counts, plus ``linked``.

    """
    with transaction.atomic():
        linked = _link(Pass)
        records = [
            DocumentRecord(
                uuid=p.source_uuid,
                slug=p.slug,
                document={"name": p.name, "website": p.website},
            )
            for p in Pass.objects.only("slug", "name", "website", "source_uuid")
            # Always true once _link has run; narrows the type.
            if p.source_uuid is not None
        ]
        counts = sync_source_documents(SourcePass, records)
    counts["linked"] = linked
    return counts


def mirror_resorts() -> dict[str, int]:
    """Make the service's resort list equal this instance's slugged ``Resort`` rows.

    Run after ``mirror_passes`` and ``mirror_reference_locations``: the
    documents name passes and places by the ``source_uuid`` those set.

    Returns:
        ``sync_source_documents``' counts, plus ``linked``.

    """
    with transaction.atomic():
        linked = _link(Resort)
        resorts = (
            Resort.objects.exclude(slug__isnull=True)
            # A region row carries its boundary polygon; the document needs
            # only the code, so the region is prefetched slim, not joined.
            .prefetch_related(
                Prefetch(
                    "region", queryset=MicroRegion.objects.only("pk", "region_id")
                ),
                Prefetch(
                    "location", queryset=Location.objects.only("pk", "source_uuid")
                ),
                Prefetch("passes", queryset=Pass.objects.only("pk", "source_uuid")),
                Prefetch(
                    "resort_locations",
                    queryset=ResortLocation.objects.select_related("location").only(
                        "pk",
                        "resort_id",
                        "role",
                        "is_primary",
                        "location__source_uuid",
                    ),
                ),
            )
        )
        records = [
            DocumentRecord(
                uuid=resort.source_uuid,
                slug=resort.slug,
                document=resort_document(resort),
            )
            for resort in resorts
            if resort.slug is not None and resort.source_uuid is not None
        ]
        counts = sync_source_documents(SourceResort, records)
    counts["linked"] = linked
    return counts


def mirror_reference_data() -> dict[str, dict[str, int]]:
    """Mirror places, then passes, then resorts, in one transaction.

    Returns:
        Each mirror's counts, under ``locations``, ``passes`` and
        ``resorts``.

    """
    with transaction.atomic():
        locations = mirror_reference_locations()
        passes = mirror_passes()
        resorts = mirror_resorts()
    result = {"locations": locations, "passes": passes, "resorts": resorts}
    logger.info("mirror_reference_data: %s", result)
    return result
