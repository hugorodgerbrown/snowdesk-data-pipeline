# bulletins/services/meteofrance_massifs.py — Canonical massif name list for
# the 23 Alpine massifs covered by the Météo-France BRA pipeline.
#
# Usage: import ALPINE_MASSIFS for the canonical list; call slugify() to
# produce the massif identifier used in Météo-France archive URLs.  Import
# SLUG_TO_CODE for the slug→integer mapping used by the archive loader, or
# call slug_to_region_id() to produce the canonical FR-{NN} region identifier.
#
# NOTE: BEAUFORTAIN is deliberately spelled without an accent — that is the
# canonical spelling used in the Météo-France API and URLs.
#
# This module is pure-Python (no Django imports at module load) so that the
# offline scripts in ``scripts/meteofrance-archive/`` can import it without
# bootstrapping Django.
"""Canonical Alpine massif names, URL-slug helpers, and slug→code lookups."""

from __future__ import annotations

import json
from pathlib import Path

# The 23 Alpine massifs covered by the Météo-France BRA archive pipeline.
# Pyrenean and Corsican massifs are excluded; they use the same API but are
# out of scope for the 2025-2026 season backfill.
#
# Spellings match the Météo-France API exactly (uppercase, hyphens).
ALPINE_MASSIFS: list[str] = [
    "ARAVIS",
    "BAUGES",
    "BEAUFORTAIN",
    "BELLEDONNE",
    "CHABLAIS",
    "CHAMPSAUR",
    "CHARTREUSE",
    "DEVOLUY",
    "EMBRUNAIS-PARPAILLON",
    "GRANDES-ROUSSES",
    "HAUT-VAR-HAUT-VERDON",
    "HAUTE-MAURIENNE",
    "HAUTE-TARENTAISE",
    "MAURIENNE",
    "MERCANTOUR",
    "MONT-BLANC",
    "OISANS",
    "PELVOUX",
    "QUEYRAS",
    "THABOR",
    "UBAYE",
    "VANOISE",
    "VERCORS",
]

# All 35 massifs served by the public API (Alps + Pyrenees + Corsica).
# Use this when fetching the index without massif filtering.
ALL_MASSIFS: list[str] = [
    "ARAVIS",
    "ASPE-OSSAU",
    "AURE-LOURON",
    "BAUGES",
    "BEAUFORTAIN",
    "BELLEDONNE",
    "CAPCIR-PUYMORENS",
    "CERDAGNE-CANIGOU",
    "CHABLAIS",
    "CHAMPSAUR",
    "CHARTREUSE",
    "CINTO-ROTONDO",
    "COUSERANS",
    "DEVOLUY",
    "EMBRUNAIS-PARPAILLON",
    "GRANDES-ROUSSES",
    "HAUT-VAR-HAUT-VERDON",
    "HAUTE-ARIEGE",
    "HAUTE-BIGORRE",
    "HAUTE-MAURIENNE",
    "HAUTE-TARENTAISE",
    "LUCHONNAIS",
    "MAURIENNE",
    "MERCANTOUR",
    "MONT-BLANC",
    "OISANS",
    "ORLU-ST-BARTHELEMY",
    "PAYS-BASQUE",
    "PELVOUX",
    "QUEYRAS",
    "RENOSO-INCUDINE",
    "THABOR",
    "UBAYE",
    "VANOISE",
    "VERCORS",
]


def _build_slug_to_code() -> dict[str, int]:
    """Build the slug→integer-code mapping from the massifs catalogue.

    Reads ``docs/research/meteofrance/massifs.json`` (path resolved relative
    to the repository root — two directories above this file) and
    produces a dict keyed by the URL-slug form of each Alpine massif name
    (uppercased, spaces replaced with hyphens).

    Only Alpine massifs (``cluster == "Alps"``) are included — Pyrenean and
    Corsican massifs are outside the scope of the current backfill.

    The function asserts at call time that every entry in ``ALPINE_MASSIFS``
    resolves successfully, catching catalogue drift early.

    Returns:
        A dict mapping slug strings (e.g. ``"CHABLAIS"``) to integer massif
        codes (e.g. ``1``).

    Raises:
        ValueError: If any ``ALPINE_MASSIFS`` slug is absent from the
            catalogue.

    """
    # This file lives at bulletins/services/meteofrance_massifs.py.
    # The repo root is two directories up: .../repo/bulletins/services/<this>
    _repo_root = Path(__file__).resolve().parents[2]
    catalogue_path = _repo_root / "docs" / "research" / "meteofrance" / "massifs.json"
    data: dict[str, list[dict[str, object]]] = json.loads(
        catalogue_path.read_text(encoding="utf-8")
    )
    mapping: dict[str, int] = {}
    for entry in data["massifs"]:
        if entry.get("cluster") != "Alps":
            continue
        name = str(entry["name"])
        slug = name.upper().replace(" ", "-")
        mapping[slug] = int(str(entry["id"]))

    # Integrity check: every member of ALPINE_MASSIFS must resolve.
    missing = [slug for slug in ALPINE_MASSIFS if slug not in mapping]
    if missing:
        raise ValueError(
            f"ALPINE_MASSIFS slugs not found in massifs.json catalogue: {missing!r}"
        )

    mapping.update(_MASSIF_ALIASES)
    return mapping


# Known-bad spellings that appear in the upstream BRA PDFs and therefore
# leak into ``customData.MF.massif`` after PDF extraction.  Aliasing them
# here lets the loader round-trip those bulletins instead of dropping them.
# Each key is the slug form produced by ``_normalise_slug`` on the raw
# upstream name; the value is the integer massif code from massifs.json.
#
# - ``EMBRUNNAIS-PARPAILLON`` (double N): 66 rows in the 2025-2026 archive.
#   Canonical spelling is ``EMBRUNAIS-PARPAILLON`` (id 20).
_MASSIF_ALIASES: dict[str, int] = {
    "EMBRUNNAIS-PARPAILLON": 20,
}


def _normalise_slug(raw: str) -> str:
    """Return the canonical slug form for an upstream massif identifier.

    The BRA PDFs use a mix of hyphens and spaces in massif names (e.g.
    ``"EMBRUNAIS PARPAILLON"`` vs ``"EMBRUNAIS-PARPAILLON"``).  This helper
    folds them onto a single canonical form so a single ``SLUG_TO_CODE``
    lookup covers every variant.

    Args:
        raw: Massif identifier as it appears in ``customData.MF.massif``.

    Returns:
        Uppercase, hyphen-joined slug (e.g. ``"EMBRUNAIS-PARPAILLON"``).

    """
    return raw.strip().upper().replace(" ", "-")


# Slug-to-integer massif code mapping for the 23 Alpine massifs.
# Built at import time from ``docs/research/meteofrance/massifs.json``.
# Key: URL-slug form (e.g. ``"CHABLAIS"``).
# Value: integer massif ID matching the ``FR-{NN}`` zero-padded region ID
# used in EAWS fixtures (e.g. ``1`` → ``"FR-01"``).
SLUG_TO_CODE: dict[str, int] = _build_slug_to_code()


def slug_to_region_id(slug: str) -> str:
    """Return the canonical ``FR-{NN}`` region identifier for a massif slug.

    The slug is the uppercase, hyphenated form used in
    ``customData.MF.massif`` within the NDJSON archive (e.g. ``"CHABLAIS"``).
    The returned string matches the ``region_id`` column in the
    ``eaws_FR.json`` fixture (e.g. ``"FR-01"``).

    Args:
        slug: Massif slug (e.g. ``"CHABLAIS"``, ``"MONT-BLANC"``).

    Returns:
        Zero-padded region identifier, e.g. ``"FR-01"`` or ``"FR-03"``.

    The input is normalised (uppercased, spaces folded to hyphens) before
    lookup so callers can pass the upstream ``customData.MF.massif`` value
    verbatim — the BRA PDFs use a mix of hyphens and spaces.

    Raises:
        KeyError: The slug is not present in ``SLUG_TO_CODE`` even after
            normalisation and alias resolution.

    """
    code = SLUG_TO_CODE[_normalise_slug(slug)]
    return f"FR-{code:02d}"


def slugify(massif: str) -> str:
    """Return the URL slug for a massif name.

    The slug is identical to the canonical API name — uppercased and
    hyphenated.  This function exists to make the interface explicit and
    to allow future normalisation (e.g. if the API introduces lowercase
    variants) without changing call sites.

    Args:
        massif: Massif name in any capitalisation.

    Returns:
        Uppercased, stripped slug string.

    """
    return massif.strip().upper()
