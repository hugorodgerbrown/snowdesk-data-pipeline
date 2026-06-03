# _sat_mapping.py — Mapping from Météo-France "Situation Avalancheuse Typique"
# (SAT) labels to EAWS/CAAML avalanche problem types.
#
# Sources:
#   [EAWS-FR] "Situations avalancheuses typiques" — official French-language
#       EAWS avalanche-problem definitions, approved by EAWS General Assembly,
#       Davos 2022.
#       URL: https://www.avalanches.org/wp-content/uploads/2022/09/FR_EAWS_avalanche_problems.pdf
#       Accessed: 2026-06-03
#
#   [EAWS-PROBLEMS] EAWS avalanche problems education page.
#       URL: https://www.avalanches.org/education/avalanche-problems/
#       Accessed: 2026-06-03
#
#   [MF-XML] "Descriptif technique des informations du Bulletin Avalanche" —
#       Météo-France BRA XML format description, version 26/10/2023.
#       URL: https://donneespubliques.meteofrance.fr/client/document/
#            descriptif_bulletinrisqueavalanche_formatxml_20231026_309.pdf
#       Accessed: 2026-06-03
#       Note: this document describes SAT as a numeric code 1–6 in the XML
#       format (`SAT1`/`SAT2` attributes).  No formal correspondence table
#       mapping numeric codes to French text labels is published; the text
#       labels are extracted from BRA PDF prose instead.
#
#   [MF-DATA] Météo-France open-data portal — BRA product page.
#       URL: https://donneespubliques.meteofrance.fr/?fond=produit&id_produit=265&id_rubrique=50
#       Accessed: 2026-06-03
#
# No formal MF → EAWS alignment document was found.  This mapping is derived
# from the EAWS published typical-situation French names [EAWS-FR], cross-
# referenced against PDF text labels observed in the MF BRA archive.  Where
# a label has no published EAWS counterpart, the mapping value is ``None``
# with an explanatory inline comment.
#
# CAAML 6.0 problem types (EAWS enum):
#   new_snow, wind_slab, persistent_weak_layers, wet_snow,
#   gliding_snow, cornices, no_distinct_avalanche_problem, favourable_situation
"""SAT (Situation Avalancheuse Typique) to CAAML problem-type mapping."""

# Keys are normalised lowercase SAT labels (strip + lower).
# Values are CAAML 6.0 avalancheType strings, or None when there is no
# published EAWS equivalent.
SAT_TO_PROBLEM_TYPE: dict[str, str | None] = {
    # ── Dry-snow situations ────────────────────────────────────────────────
    # "Neige fraîche" is the EAWS FR canonical label for new-snow problems.
    # [EAWS-FR, p. 2]
    "neige fraîche": "new_snow",
    "neige fraiche": "new_snow",  # accent-free variant
    # "neige récente" (recent snow) is an older MF synonym for fresh snow;
    # treated as equivalent to neige fraîche. [MF BRA archive text]
    "neige récente": "new_snow",
    "neige recente": "new_snow",  # accent-free variant
    # "Neige ventée" is the EAWS FR canonical label for wind-slab problems.
    # [EAWS-FR, p. 3]
    "neige ventée": "wind_slab",
    "neige ventee": "wind_slab",  # accent-free variant
    # "Plaques à vent" is a MF synonym for neige ventée. [MF BRA archive text]
    "plaques à vent": "wind_slab",
    "plaques a vent": "wind_slab",  # accent-free variant
    # "Couche fragile persistante" is the EAWS FR canonical label.
    # [EAWS-FR, p. 4]
    "couche fragile persistante": "persistent_weak_layers",
    # "couche fragile enfouie" (buried weak layer) is a MF sub-type of
    # persistent weak layers. [MF BRA archive text]
    "couche fragile enfouie": "persistent_weak_layers",
    # "couche fragile" alone is a truncated form observed in MF BRA PDFs
    # alongside "couche fragile persistante"; treated as persistent_weak_layers.
    # [MF BRA archive text — real_samples.ndjson fixture]
    "couche fragile": "persistent_weak_layers",
    # ── Wet-snow situations ────────────────────────────────────────────────
    # "Neige humide" is the EAWS FR canonical label for wet-snow problems.
    # [EAWS-FR, p. 5]
    "neige humide": "wet_snow",
    # "fonte" (snowmelt) and "redoux" (thaw/warming) are MF labels describing
    # the meteorological driver of wet-snow instability; both map to wet_snow.
    # [MF BRA archive text]
    "fonte": "wet_snow",
    "redoux": "wet_snow",
    # ── Gliding snow ──────────────────────────────────────────────────────
    # "glissement" is the MF term for gliding-snow / full-depth avalanche.
    # EAWS FR uses "Avalanches de fond" for this problem type. [EAWS-FR, p. 6]
    "glissement": "gliding_snow",
    # ── Cornices ──────────────────────────────────────────────────────────
    # "Corniches" is the EAWS FR canonical label for the optional cornice
    # problem. [EAWS-FR, p. 8]
    "corniches": "cornices",
    # ── No distinct problem ───────────────────────────────────────────────
    # EAWS FR (p. 9): "Aucune situation avalancheuse typique prédominante"
    # MF BRA PDFs use shortened forms of this label.
    "aucune situation avalancheuse typique prédominante": (
        "no_distinct_avalanche_problem"
    ),
    "pas de situation avalancheuse typique": "no_distinct_avalanche_problem",
    # "pas de situation prédominante" is observed in MF BRA archive PDFs as a
    # shortened variant of the EAWS FR label. [MF BRA archive text —
    # real_samples.ndjson fixture]
    "pas de situation prédominante": "no_distinct_avalanche_problem",
    # "sans objet" (not applicable) appears in some older BRA PDFs.
    # [MF BRA archive text]
    "sans objet": "no_distinct_avalanche_problem",
    # ── Favourable situation ──────────────────────────────────────────────
    # "situation favorable" is the French translation of the CAAML enum value
    # `favourable_situation`.  This problem type is not described in [EAWS-FR]
    # (the 2022 document lists only 5 core + 2 optional types); it exists in
    # the CAAML 6.0 schema [reference_data/openapi.json] but appears rarely
    # in MF BRA PDFs.  Included for completeness.
    "situation favorable": "favourable_situation",
    "situation favorable au ski": "favourable_situation",
}


def sat_to_problem_type(sat_label: str) -> str | None:
    """Map a SAT label to a CAAML 6.0 avalanche problem type string.

    Args:
        sat_label: Raw SAT label text from the BRA PDF (may contain mixed case
            and leading/trailing whitespace).

    Returns:
        A CAAML problem-type string (e.g. ``"wind_slab"``), ``None`` if the
        label is not in the mapping table (unknown label), or ``None`` if the
        label has no published EAWS equivalent (future additions with explicit
        ``None`` values).  Callers must treat ``None`` as a meaningful "no
        mapping" signal rather than silently substituting
        ``no_distinct_avalanche_problem``.

    """
    return SAT_TO_PROBLEM_TYPE.get(sat_label.strip().lower())
