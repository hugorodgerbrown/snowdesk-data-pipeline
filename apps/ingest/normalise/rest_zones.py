"""
apps/ingest/normalise/rest_zones.py — FOEN's wildlife rest zones as documents.

Pure functions over the features ``apps/ingest/sources/geo_admin.py``
fetches (SNOW-1263); nothing here reads a setting, the network or the
database.

  rest_zone_uuid(feature_id)
      The service's id for a zone. **Identity is geo.admin's feature id
      and nothing else** — never the order a canton's answer lists the
      zones in, never the order the cantons are asked in, and never the
      zone's name, which FOEN can and does edit. A uuid5 of the id is the
      same on every import, so a second run reports nothing changed and a
      zone FOEN drops is tombstoned rather than re-created under a new id.
  rest_zone_document(feature)
      One feature as the document the service stores: the name, whether
      it is legally binding or only recommended, the restriction, the
      protected period as FOEN states it, the canton and its rules page,
      and the polygon as WGS84 GeoJSON.
  collect_documents(features)
      Every canton's features as one list, de-duplicated by feature id: a
      zone that straddles two cantons may be answered by both. The first
      occurrence wins, and a repeat that differs from it fails loudly
      (``RestZoneError``), since there would then be no telling which
      version is FOEN's.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

# The namespace a zone's uuid is derived in. geo.admin gives a feature an
# integer id and no uuid.
REST_ZONE_NAMESPACE = uuid.UUID("0f3d7b2e-8a41-4c6e-9b1d-5e2a7c9f4d63")

# The two protection statuses, as FOEN states them in German.
BINDING = "rechtsverbindlich"

# The languages FOEN states a zone's status and restriction in.
LANGUAGES: tuple[str, ...] = ("de", "fr", "it")


class RestZoneError(ValueError):
    """A feature that cannot be read, or two that disagree about one zone."""


def rest_zone_uuid(feature_id: int) -> uuid.UUID:
    """Return the service's uuid for geo.admin rest-zone feature ``feature_id``."""
    return uuid.uuid5(REST_ZONE_NAMESPACE, f"rest-zone:{feature_id}")


def _text(value: object) -> str:
    """Return a property as stripped text, ``""`` when absent."""
    return str(value).strip() if value is not None else ""


def feature_id(feature: Mapping[str, Any]) -> int:
    """Return a feature's geo.admin id.

    Raises:
        RestZoneError: The feature carries no integer id.

    """
    raw = feature.get("featureId", feature.get("id"))
    try:
        return int(str(raw))
    except ValueError:
        raise RestZoneError(f"a rest zone has no feature id: {raw!r}") from None


def _is_ring(ring: Any) -> bool:
    """Return whether ``ring`` is a closed GeoJSON ring of [lon, lat] positions."""
    return (
        isinstance(ring, list)
        and len(ring) >= 4
        and all(
            isinstance(position, list)
            and len(position) >= 2
            and all(isinstance(n, int | float) for n in position[:2])
            for position in ring
        )
    )


def _is_polygon_rings(rings: Any) -> bool:
    """Return whether ``rings`` is one polygon: an outer ring, then any holes."""
    return isinstance(rings, list) and bool(rings) and all(map(_is_ring, rings))


def _is_polygon(geometry: Mapping[str, Any]) -> bool:
    """Return whether a geometry is a Polygon or MultiPolygon with real rings.

    Checked before the sync, because the sync tombstones what it does not
    see: an empty ring that only failed at apply would already have
    replaced the stored zone.
    """
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon":
        return _is_polygon_rings(coordinates)
    if geometry.get("type") == "MultiPolygon":
        return (
            isinstance(coordinates, list)
            and bool(coordinates)
            and all(map(_is_polygon_rings, coordinates))
        )
    return False


def rest_zone_document(feature: Mapping[str, Any]) -> dict[str, Any]:
    """Return one geo.admin feature as the document the service stores.

    Args:
        feature: One item of a ``find`` answer.

    Returns:
        The document.

    Raises:
        RestZoneError: The feature has no id or no polygon.

    """
    key = feature_id(feature)
    geometry = feature.get("geometry") or {}
    if not _is_polygon(geometry):
        raise RestZoneError(f"rest zone {key} has no polygon")
    props = feature.get("properties") or {}
    return {
        "feature_id": key,
        "name": _text(props.get("wrz_name")),
        "binding": _text(props.get("schutzs_de")) == BINDING,
        "status": {lang: _text(props.get(f"schutzs_{lang}")) for lang in LANGUAGES},
        "restriction_code": _text(props.get("bestimmung")),
        "restriction": {lang: _text(props.get(f"best_{lang}")) for lang in LANGUAGES},
        "period": _text(props.get("schutzzeit")),
        "basis": _text(props.get("grundlage")),
        "decided": _text(props.get("beschlussjahr")),
        "note": _text(props.get("zusatzinformation")),
        "canton": _text(props.get("kanton")),
        "url": _text(props.get("url_kanton")) or None,
        "geometry": {"type": geometry["type"], "coordinates": geometry["coordinates"]},
    }


def collect_documents(features: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return every feature's document once, in the order first seen.

    Args:
        features: Every canton's features, concatenated.

    Returns:
        One document per feature id.

    Raises:
        RestZoneError: A feature is unreadable, or one id arrives twice with
            different contents.

    """
    seen: dict[int, dict[str, Any]] = {}
    for feature in features:
        document = rest_zone_document(feature)
        key = document["feature_id"]
        first = seen.get(key)
        if first is None:
            seen[key] = document
        elif first != document:
            raise RestZoneError(
                f"rest zone {key} was answered twice with different contents"
            )
    return list(seen.values())
