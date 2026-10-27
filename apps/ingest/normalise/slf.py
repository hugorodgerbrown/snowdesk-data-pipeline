"""
apps/ingest/normalise/slf.py — Pure normalisation of the SLF CAAML API's responses.

Moved out of ``apps/bulletins/services/slf_fetcher.py`` (SNOW-1248), which
keeps the HTTP and the database writes:

  normalise_slf_response   the API's three envelope shapes → a flat list.
  detect_caaml_shape       which CAAML export a page actually IS, aggregated
                           or per-region, independent of the URL it came
                           from (SNOW-900) — see the block comment above it.
  parse_dt                 an ISO-8601 string → a UTC-aware datetime.
  resolve_issued_at        a bulletin's publication time, falling back to
                           ``validTime.startTime`` for pre-2024 bulletins.
  slf_pdf_url              the archive PDF a bulletin was published as.

No database, network or settings: each takes what SLF sent and returns a value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from apps.ingest.normalise.fetcher_common import normalise_bulletin_response

# The language every SLF request asks for, and a bulletin's default when its
# payload carries none.
DEFAULT_LANG = "en"

_SLF_PDF_BASE = "https://www.slf.ch/fileadmin/avalanche_bulletin/pdf"


def slf_pdf_url(raw: dict[str, Any]) -> str:
    """
    Derive the SLF archive PDF URL from a raw bulletin dict.

    The PDF URL encodes the issue date, issue time (``08-00`` for morning
    updates, ``17-00`` for afternoon issues), and language.  The hour
    boundary is based on UTC: SLF publishes the morning update at
    approximately 07:00 UTC (08:00 CET) and the afternoon issue at
    approximately 16:00 UTC (17:00 CET), so UTC hour < 12 → ``08-00``,
    UTC hour ≥ 12 → ``17-00``.

    Args:
        raw: A single bulletin dict from the SLF CAAML API.  Must contain
            a parseable ``publicationTime`` (or fall back via
            ``resolve_issued_at``).

    Returns:
        The full public URL to the PDF, e.g.
        ``https://www.slf.ch/fileadmin/avalanche_bulletin/pdf/2026/03/Bulletin_2026-03-15_17-00_en.pdf``.

    """
    issued_at = resolve_issued_at(raw)
    issue_time = "08-00" if issued_at.hour < 12 else "17-00"
    lang = raw.get("lang", DEFAULT_LANG)
    issue_date = issued_at.date()
    return (
        f"{_SLF_PDF_BASE}/{issue_date:%Y}/{issue_date:%m}/"
        f"Bulletin_{issue_date:%Y-%m-%d}_{issue_time}_{lang}.pdf"
    )


def normalise_slf_response(data: Any) -> list[dict[str, Any]]:
    """
    Normalise the SLF API response into a flat list of bulletin dicts.

    The API may return:
      - A flat list of bulletins.
      - A single collection object with a "bulletins" key.
      - A list of collection objects, each with a "bulletins" key.

    Delegates to ``normalise_bulletin_response`` from ``fetcher_common``
    with the ``"SLF"`` source label.

    Args:
        data: The parsed JSON response from the SLF API.

    Returns:
        A flat list of bulletin dicts.

    """
    return normalise_bulletin_response(data, "SLF")


# ---------------------------------------------------------------------------
# CAAML payload shape detection (SNOW-900)
# ---------------------------------------------------------------------------
# SLF's 2026/27 export stops aggregating micro-regions into five or six
# bulletins and publishes one bulletin per region instead — ~150 entries per
# issue where we see 5–6 today. SLF confirmed on 2026-09-10 that the legacy
# endpoint preserves the response FORMAT but not necessarily the old
# aggregation, so the pipeline cannot infer which shape it is handling from
# which URL it fetched. It has to look at what arrived.
#
# Without this, the first de-aggregated payload lands as a silent 25× — the
# ingest succeeds (measured: all 133 sample bulletins upsert cleanly), the row
# count and the stored JSON multiply, and nothing in the log says which format
# was served.

CAAML_SHAPE_AGGREGATED = "aggregated"
CAAML_SHAPE_PER_REGION = "per-region"
CAAML_SHAPE_UNKNOWN = "unknown"

# A single bulletin carrying more than one region is proof of aggregation, so
# it settles the question outright. The converse does not: a page of
# one-region bulletins could in principle be a legacy page that happens to
# hold only narrow bulletins. Measured, the two shapes are nowhere near each
# other — the three committed SLF sentinels carry 93, 65 and 13 regions, and
# SLF's v5 sample is 133 bulletins of one region each — so a page of ten or
# more single-region bulletins is the new shape.
_PER_REGION_MIN_BULLETINS = 10

# Fields the new export carries that no legacy bulletin does. Either one
# settles the question on its own, however few bulletins the page holds, which
# is what makes a short page (the tail of a paged range) classifiable at all.
# ``customData.CH.weather`` is the structured per-region weather block, present
# in the sample and 80% of its payload by size; ``dangerRatingEvolution`` is
# specified but not yet populated, so it is here for the day it appears.
_NEW_FORMAT_MARKERS = ("customData.CH.weather", "dangerRatingEvolution")


@dataclass(frozen=True)
class CaamlPayloadShape:
    """What a page of SLF bulletins turned out to be.

    Attributes:
        shape: One of ``CAAML_SHAPE_AGGREGATED``,
            ``CAAML_SHAPE_PER_REGION`` or ``CAAML_SHAPE_UNKNOWN``.
        bulletin_count: Number of bulletins on the page.
        region_count: Total regions across all of them.
        max_regions_per_bulletin: The largest single bulletin's region
            count — the signal that settles aggregation outright.
        markers: New-format marker paths actually found, in the order
            they are declared. Carried separately from ``shape`` so a
            payload that is aggregated AND carries a new field logs both
            rather than one of them.

    """

    shape: str
    bulletin_count: int
    region_count: int
    max_regions_per_bulletin: int
    markers: tuple[str, ...]


def _region_count(bulletin: Any) -> int:
    """Count a raw bulletin's regions without trusting its shape.

    Deliberately total rather than strict: this runs on the fetch path
    purely to describe what arrived, so a malformed entry must return a
    number and let the real ingest raise on it. A ``KeyError`` from a
    logging helper would fail the whole fetch over an entry that
    ``upsert_bulletin`` might well handle.

    Args:
        bulletin: A raw entry from the normalised response list.

    Returns:
        The number of entries in ``regions``, or 0 if it is missing or
        not a list.

    """
    if not isinstance(bulletin, dict):
        return 0
    regions = bulletin.get("regions")
    return len(regions) if isinstance(regions, list) else 0


def _has_dotted_path(bulletin: Any, path: str) -> bool:
    """Report whether a dotted key path resolves to anything in a bulletin.

    Args:
        bulletin: A raw entry from the normalised response list.
        path: A dotted path such as ``"customData.CH.weather"``.

    Returns:
        True if every segment resolves and the final value is not None.

    """
    node: Any = bulletin
    for segment in path.split("."):
        if not isinstance(node, dict) or segment not in node:
            return False
        node = node[segment]
    return node is not None


def detect_caaml_shape(bulletins: list[dict[str, Any]]) -> CaamlPayloadShape:
    """Classify a page of raw SLF bulletins as aggregated or per-region.

    Pure, and unit-tested against fixture dicts rather than the network.

    The order of the checks is the order of confidence. A multi-region
    bulletin is proof of aggregation and outranks everything, including a
    new-format marker — if SLF ever ship both, the region count is what
    drives the volume and so the volume is what we report. Failing that, a
    marker field is proof of the new export at any page size. Failing
    that, page size decides. An empty page is no evidence either way and
    is reported as such rather than defaulted to today's shape.

    Args:
        bulletins: The normalised bulletin list from one page fetch.

    Returns:
        A ``CaamlPayloadShape`` describing the page.

    """
    if not bulletins:
        return CaamlPayloadShape(CAAML_SHAPE_UNKNOWN, 0, 0, 0, ())

    counts = [_region_count(bulletin) for bulletin in bulletins]
    markers = tuple(
        path
        for path in _NEW_FORMAT_MARKERS
        if any(_has_dotted_path(bulletin, path) for bulletin in bulletins)
    )
    max_regions = max(counts)

    if max_regions > 1:
        shape = CAAML_SHAPE_AGGREGATED
    elif markers or len(bulletins) >= _PER_REGION_MIN_BULLETINS:
        shape = CAAML_SHAPE_PER_REGION
    else:
        shape = CAAML_SHAPE_UNKNOWN

    return CaamlPayloadShape(
        shape=shape,
        bulletin_count=len(bulletins),
        region_count=sum(counts),
        max_regions_per_bulletin=max_regions,
        markers=markers,
    )


def parse_dt(value: str) -> datetime:
    """
    Parse an ISO-8601 datetime string into a UTC-aware datetime.

    Aware inputs (Z-suffixed or with an explicit offset) are converted to
    UTC. Naive inputs are assumed to be UTC, since the CAAML schema
    requires timestamps to be expressed in UTC or with timezone info.

    Args:
        value: An ISO-8601 formatted datetime string.

    Returns:
        A UTC-aware datetime object.

    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def resolve_issued_at(raw: dict[str, Any]) -> datetime:
    """
    Resolve a bulletin's publication timestamp from the raw payload.

    Pre-2024 SLF bulletins omit the top-level ``publicationTime`` field
    that modern bulletins carry. When it is absent, fall back to
    ``validTime.startTime`` — for SLF the two values are typically
    identical (or differ by at most a couple of hours), so this is a
    safe proxy for both the pagination boundary check and the
    ``Bulletin.issued_at`` column.

    Args:
        raw: A single bulletin dict from the SLF CAAML API.

    Returns:
        A UTC-aware datetime suitable for use as ``issued_at``.

    """
    pub_time = raw.get("publicationTime")
    if pub_time:
        return parse_dt(pub_time)
    return parse_dt(raw["validTime"]["startTime"])
