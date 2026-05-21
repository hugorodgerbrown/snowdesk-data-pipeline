# _sat_mapping.py — Mapping from Météo-France "Situation Avalancheuse Typique"
# (SAT) labels to EAWS/CAAML avalanche problem types.
#
# Known limitation: this is an initial reasonable mapping.  The official MF
# SAT→CAAML alignment is tracked as a follow-up ticket (SNOW-224 follow-up).
# Until that work lands, some labels may map to None or to a close-but-not-
# exact CAAML type.
#
# CAAML 6.0 problem types (EAWS enum):
#   new_snow, wind_slab, persistent_weak_layers, wet_snow,
#   gliding_snow, cornices, no_distinct_avalanche_problem, favourable_situation
"""SAT (Situation Avalancheuse Typique) to CAAML problem-type mapping."""

# Keys are normalised lowercase SAT labels (strip + lower).
# Values are CAAML 6.0 avalancheType strings, or None when there is no
# unambiguous mapping.
SAT_TO_PROBLEM_TYPE: dict[str, str | None] = {
    # Dry-snow situations
    "neige fraîche": "new_snow",
    "neige fraiche": "new_snow",
    "neige récente": "new_snow",
    "neige recente": "new_snow",
    "neige ventée": "wind_slab",
    "neige ventee": "wind_slab",
    "plaques à vent": "wind_slab",
    "plaques a vent": "wind_slab",
    "couche fragile persistante": "persistent_weak_layers",
    "couche fragile enfouie": "persistent_weak_layers",
    # Wet-snow situations
    "neige humide": "wet_snow",
    "fonte": "wet_snow",
    "redoux": "wet_snow",
    # Gliding
    "glissement": "gliding_snow",
    # Mixed / no distinct problem
    "pas de situation avalancheuse typique": "no_distinct_avalanche_problem",
    "sans objet": "no_distinct_avalanche_problem",
    # Favourable
    "risque faible": "favourable_situation",
}


def sat_to_problem_type(sat_label: str) -> str | None:
    """Map a SAT label to a CAAML 6.0 avalanche problem type string.

    Args:
        sat_label: Raw SAT label text from the BRA PDF (may contain mixed case
            and leading/trailing whitespace).

    Returns:
        A CAAML problem-type string (e.g. ``"wind_slab"``), or ``None`` if
        the label is not in the mapping table.

    """
    return SAT_TO_PROBLEM_TYPE.get(sat_label.strip().lower())
