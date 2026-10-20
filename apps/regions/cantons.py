"""apps/regions/cantons.py — Swiss canton codes and their English names.

``Resort.canton`` stores the two-letter abbreviation the curated resort sheet
uses (``"VS"``, ``"GR"``), and a handful of resorts that straddle a border
carry a compound code (``"BE/VS"``, ``"GR/TI"``). A reader searching for
"Valais ski resorts" does not know the abbreviation, so a page that groups
resorts by canton (the ``/resorts/`` index, SNOW-1150) needs the name.

``CANTON_NAMES`` holds all 26 cantons, keyed by their official code, with the
English exonym (Geneva, Lucerne, Zurich) where English has one and the local
name otherwise. ``canton_label`` is the one reader: it splits a compound code
on ``/`` and falls back to the code itself for anything it does not know, so
a typo in the sheet renders as the typo rather than vanishing.

Domain reference data, so it lives in ``regions`` beside the models that
carry the code.
"""

from __future__ import annotations

import logging

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

# The 26 cantons, by official two-letter code. Lazily translated: the module
# is imported at start-up, before a request has activated a language.
CANTON_NAMES: dict[str, str | Promise] = {
    "AG": _("Aargau"),
    "AI": _("Appenzell Innerrhoden"),
    "AR": _("Appenzell Ausserrhoden"),
    "BE": _("Bern"),
    "BL": _("Basel-Landschaft"),
    "BS": _("Basel-Stadt"),
    "FR": _("Fribourg"),
    "GE": _("Geneva"),
    "GL": _("Glarus"),
    "GR": _("Graubünden"),
    "JU": _("Jura"),
    "LU": _("Lucerne"),
    "NE": _("Neuchâtel"),
    "NW": _("Nidwalden"),
    "OW": _("Obwalden"),
    "SG": _("St. Gallen"),
    "SH": _("Schaffhausen"),
    "SO": _("Solothurn"),
    "SZ": _("Schwyz"),
    "TG": _("Thurgau"),
    "TI": _("Ticino"),
    "UR": _("Uri"),
    "VD": _("Vaud"),
    "VS": _("Valais"),
    "ZG": _("Zug"),
    "ZH": _("Zurich"),
}


def canton_label(code: str) -> str:
    """Return the display name for a canton code, compound or not.

    ``"VS"`` → ``"Valais"``; ``"BE/VS"`` → ``"Bern / Valais"``. Each part is
    matched case-insensitively after trimming whitespace. A part with no
    entry in ``CANTON_NAMES`` is shown as the code itself, so an unexpected
    value in the sheet stays visible instead of disappearing.

    Args:
        code: The canton abbreviation stored on ``Resort.canton``.

    Returns:
        The human-readable name, or ``""`` for an empty code.

    """
    parts = [part.strip() for part in code.split("/") if part.strip()]
    labels = [str(CANTON_NAMES.get(part.upper(), part)) for part in parts]
    return " / ".join(labels)
