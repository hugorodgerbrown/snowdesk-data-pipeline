"""
apps/ingest/api/bulletins.py — The ``bulletins`` data feed, read from SourceBulletin.

``serialise_source_bulletin`` turns one row into one wire item: the
service's render model of the bulletin and the few identity fields a client
needs to file it (SNOW-1337). The raw CAAML never leaves the service: the
service ingests and interprets it, and serves the cleanest, leanest dataset
a client can display from.

An item carries:

  bulletin_id, updated_at, pdf_url  the row's own columns.
  content_hash      the sha256 of the CAAML it was served from, which a
                    client compares to decide an item is unchanged.
  render_model      the service's model (SNOW-1270), ``derived`` block
                    included (SNOW-1341). A client copies it.
  valid_from, target_date, region_codes
                    the service's index of the CAAML (SNOW-1341).
  valid_to, issued_at, next_update, lang, unscheduled, source, regions
                    the rest of what a client files a ``Bulletin`` and its
                    ``RegionBulletin`` links by (``bulletin_identity``).

``bulletin_identity`` reads those last fields off the CAAML defensively: a
missing or malformed value is ``None`` here and a refusal at the client, so
one bad bulletin can never break a feed page. ``BULLETINS_FEED`` is the
feed ``/api/data/v1/bulletins/`` pages through.

Moved from ``apps/data_feed/feeds.py`` by SNOW-1250, which also changed what
it reads: ingest's own ``SourceBulletin`` rather than the app's ``Bulletin``.
The fetch pipelines apply what they fetch through this same serialiser, so
production writes its ``Bulletin`` rows from exactly the items staging
receives.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from django.db.models import QuerySet

from apps.ingest.api.paging import Feed
from apps.ingest.models import SourceBulletin
from apps.ingest.normalise.render_model import RenderModelBuildError, detect_source
from apps.ingest.normalise.slf import DEFAULT_LANG, parse_dt, resolve_issued_at


def _iso_or_none(caaml: dict[str, Any], *path: str) -> str | None:
    """Return a CAAML timestamp at ``path`` as a UTC ISO string, or None.

    Args:
        caaml: The bulletin's CAAML properties.
        *path: The keys leading to the timestamp.

    Returns:
        The parsed instant, normalised to UTC, or ``None`` when it is
        absent or unreadable.

    """
    value: Any = caaml
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_dt(value).isoformat()
    except ValueError:
        return None


def bulletin_identity(caaml: dict[str, Any]) -> dict[str, Any]:
    """Return what a client files a bulletin by, read off its CAAML.

    ``regions`` keeps each CAAML entry's ``regionID`` and ``name`` only (the
    name is what ``RegionBulletin.region_name_at_time`` records), in CAAML
    order, malformed entries included so the client refuses them before
    any write (SNOW-460).

    Args:
        caaml: The bulletin's CAAML properties.

    Returns:
        ``valid_to``, ``issued_at``, ``next_update`` (ISO strings or None),
        ``lang``, ``unscheduled``, ``source`` (a provider code, or ``""``)
        and ``regions``.

    """
    try:
        issued_at: str | None = resolve_issued_at(caaml).isoformat()
    except KeyError, TypeError, ValueError:
        issued_at = None
    try:
        source = detect_source(caaml)
    except RenderModelBuildError:
        source = ""
    # A key the CAAML lacks stays absent, so the client's resolver raises
    # on it exactly as it did reading the CAAML itself.
    regions = [
        {key: entry[key] for key in ("regionID", "name") if key in entry}
        if isinstance(entry, dict)
        else {}
        for entry in caaml.get("regions") or []
    ]
    return {
        "valid_to": _iso_or_none(caaml, "validTime", "endTime"),
        "issued_at": issued_at,
        "next_update": _iso_or_none(caaml, "nextUpdate"),
        "lang": caaml.get("lang") or DEFAULT_LANG,
        "unscheduled": bool(caaml.get("unscheduled", False)),
        "source": source,
        "regions": regions,
    }


def serialise_source_bulletin(row: SourceBulletin) -> dict[str, Any]:
    """Return a bulletin as its render model and identity fields — never its CAAML.

    Args:
        row: A row loaded with ``bulletin_id``, ``pdf_url``, ``caaml``,
            ``content_hash``, ``render_model``, ``updated_at``,
            ``valid_from``, ``target_date`` and ``region_codes``.

    Returns:
        The wire item.

    """
    return {
        "bulletin_id": row.bulletin_id,
        "updated_at": row.updated_at.isoformat(),
        "pdf_url": row.pdf_url,
        "content_hash": row.content_hash,
        "render_model": row.render_model,
        "valid_from": row.valid_from.isoformat() if row.valid_from else None,
        "target_date": row.target_date.isoformat() if row.target_date else None,
        "region_codes": list(row.region_codes or []),
        **bulletin_identity(row.caaml or {}),
    }


def _source_bulletins() -> QuerySet[SourceBulletin]:
    """Return every stored bulletin, loading only what the item is built from.

    ``caaml`` is loaded for ``bulletin_identity`` to read; it is not served.
    """
    return SourceBulletin.objects.only(
        "id",
        "bulletin_id",
        "updated_at",
        "pdf_url",
        "caaml",
        "content_hash",
        "render_model",
        "valid_from",
        "target_date",
        "region_codes",
    )


BULLETINS_FEED = Feed(
    "bulletins", _source_bulletins, serialise_source_bulletin, day_field="target_date"
)

# The same feed under /api/data/v2/ (SNOW-1272), so one token bound to v2
# reads everything the reference-data service serves. Same items.
BULLETINS_FEED_V2 = replace(BULLETINS_FEED, version=2)
