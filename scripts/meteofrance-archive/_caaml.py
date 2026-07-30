# _caaml.py — CAAML 6.0 GeoJSON Feature envelope builder shared between
# the index-archive and the PDF-to-CAAML parser stages.
#
# The envelope matches the GeoJSON Feature shape used by the SLF ingestion
# pipeline so downstream consumers see a consistent record structure:
#   { "type": "Feature", "geometry": null, "properties": { … } }
#
# The `customData.MF` block is where Météo-France-specific fields that have
# no direct CAAML equivalent (e.g. SAT labels, raw weather prose) are stored.
"""CAAML 6.0 GeoJSON Feature envelope builder."""

from __future__ import annotations

from datetime import date, datetime


def build_envelope(
    *,
    massif: str,
    bulletin_date: date,
    valid_from: datetime | None = None,
    lang: str = "fr",
    properties: dict[str, object] | None = None,
) -> dict[str, object]:
    """Return a GeoJSON Feature dict wrapping CAAML bulletin properties.

    Args:
        massif: Canonical massif name (e.g. ``"CHABLAIS"``).
        bulletin_date: The date for which the bulletin is valid.
        valid_from: The instant the bulletin takes effect — the nominal issue
            time printed on the page ("Rédigé le … à 16h"), as a UTC-aware
            datetime.  When omitted, falls back to midnight on
            ``bulletin_date``.  Passing the real value is what lets two issues
            of one massif-day be ordered (SNOW-559); the midnight fallback made
            every issue of a day indistinguishable.
        lang: Language code (default ``"fr"``).
        properties: Additional CAAML properties to merge into the envelope.

    Returns:
        A dict shaped as a GeoJSON Feature with ``type``, ``geometry``, and
        ``properties`` keys.  The ``properties`` dict contains a minimal set
        of CAAML 6.0 fields plus a ``customData.MF`` placeholder.

    """
    start_time = (
        valid_from.isoformat().replace("+00:00", "Z")
        if valid_from is not None
        else bulletin_date.isoformat() + "T00:00:00+00:00"
    )
    base_properties: dict[str, object] = {
        "lang": lang,
        "regions": [
            {
                "regionID": f"FR-{massif}",
                "name": massif.replace("-", " ").title(),
            }
        ],
        "validTime": {
            "startTime": start_time,
            "endTime": bulletin_date.isoformat() + "T23:59:59+00:00",
        },
        "customData": {
            "MF": {
                "massif": massif,
                "date": bulletin_date.isoformat(),
            }
        },
    }
    if properties:
        # Merge top-level keys; caller is responsible for not clobbering
        # the base keys unless intentional.
        base_properties.update(properties)

    return {
        "type": "Feature",
        "geometry": None,
        "properties": base_properties,
    }
