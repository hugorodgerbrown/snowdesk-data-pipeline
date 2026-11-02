"""
apps/ingest/rest_zones.py — The service's copy of FOEN's wildlife rest zones.

``import_rest_zones()`` makes the service's ``SourceRestZone`` rows equal
what geo.admin holds, through ``sync_source_documents``: an unchanged zone
is not rewritten, one FOEN drops is tombstoned, and one that comes back is
revived, so the v2 ``rest-zones`` feed stays quiet between FOEN's edits.

**All or nothing.** ``sync_source_documents`` makes the table equal the
whole list it is given, so a list missing one canton would tombstone every
zone in it. Every canton is therefore fetched, and every feature read,
before anything is synchronised: a canton that cannot be read, or a
feature that cannot, raises ``RestZoneImportError`` naming it, and the
stored zones are left exactly as they were.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.utils.text import slugify

from apps.ingest.models import SourceRestZone
from apps.ingest.normalise.rest_zones import (
    RestZoneError,
    collect_documents,
    rest_zone_uuid,
)
from apps.ingest.reference import DocumentRecord, sync_source_documents
from apps.ingest.sources.geo_admin import CANTONS, GeoAdminError, fetch_canton

logger = logging.getLogger(__name__)

# The kind the import reports: the feed name it is served as.
REST_ZONES = "rest-zones"


class RestZoneImportError(RuntimeError):
    """A canton or a feature could not be read, so nothing was synchronised."""


def fetch_all_cantons(
    fetch: Callable[[str], list[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Return every canton's features, or raise before returning any.

    Args:
        fetch: Reads one canton; geo.admin's ``find`` (``fetch_canton``,
            looked up at call time) unless a caller passes another.

    Returns:
        Every feature, cantons in ``CANTONS`` order.

    Raises:
        RestZoneImportError: A canton could not be read.

    """
    read = fetch or fetch_canton
    features: list[dict[str, Any]] = []
    for canton in CANTONS:
        try:
            features.extend(read(canton))
        except GeoAdminError as exc:
            raise RestZoneImportError(f"canton {canton} failed: {exc}") from exc
    return features


def rest_zone_records(features: list[dict[str, Any]]) -> list[DocumentRecord]:
    """Return every feature as a record, de-duplicated by feature id.

    Args:
        features: Every canton's features.

    Returns:
        One record per zone.

    Raises:
        RestZoneImportError: A feature is unreadable, or two disagree.

    """
    try:
        documents = collect_documents(features)
    except RestZoneError as exc:
        raise RestZoneImportError(str(exc)) from exc
    return [
        DocumentRecord(
            uuid=rest_zone_uuid(document["feature_id"]),
            slug=(slugify(document["name"])[:200] or "rest-zone")
            + f"-{document['feature_id']}",
            document=document,
        )
        for document in documents
    ]


def import_rest_zones(
    fetch: Callable[[str], list[dict[str, Any]]] | None = None,
) -> dict[str, dict[str, int]]:
    """Make the service's rest zones equal what geo.admin holds now.

    Args:
        fetch: Reads one canton; see ``fetch_all_cantons``.

    Returns:
        The sync's counts (``created``, ``updated``, ``deleted``,
        ``unchanged``), keyed ``rest-zones``.

    Raises:
        RestZoneImportError: A canton or a feature could not be read;
            nothing was written.

    """
    records = rest_zone_records(fetch_all_cantons(fetch))
    result = {REST_ZONES: sync_source_documents(SourceRestZone, records)}
    logger.info("import_rest_zones: %s", result)
    return result
