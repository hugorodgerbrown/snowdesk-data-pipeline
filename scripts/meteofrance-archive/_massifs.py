# _massifs.py — Canonical massif name list for the 23 Alpine massifs covered
# by the Météo-France BRA (Bulletin de Risque d'Avalanche) pipeline.
#
# Usage: import ALPINE_MASSIFS for the canonical list; call slugify() to produce
# the massif identifier used in Météo-France archive URLs.
#
# NOTE: BEAUFORTAIN is deliberately spelled without an accent — that is the
# canonical spelling used in the Météo-France API and URLs.
"""Canonical Alpine massif names and URL-slug helpers."""

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


def slugify(massif: str) -> str:
    """Return the URL slug for a massif name.

    The slug is identical to the canonical API name — uppercased and
    hyphenated.  This function exists to make the interface explicit and
    to allow future normalisation (e.g. if the API introduces lowercase
    variants) without changing call sites.
    """
    return massif.strip().upper()
