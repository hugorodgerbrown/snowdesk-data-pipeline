# _fr_prose.py — French-prose extraction helpers for BRA per-problem fields.
#
# Provides pure functions that extract structured CAAML values from the
# narrative text produced by the BRA PDF extractor.  No pdfplumber dependency.
#
# Prose segmentation strategy
# ---------------------------
# A BRA bulletin's stability section is one continuous text block.  When the
# bulletin has two SAT labels the prose is **not** labelled per-problem; the
# text may describe conditions that span both.  For this extractor we apply
# the combined stability text to **all** problems equally.  The text is the
# concatenation of the spontaneous and triggered activity strings (as built by
# ``extract_avalanche_activity``).  If future bulletin formats start keying
# paragraphs to SAT labels, update ``_segment_prose`` and pass the relevant
# segment per-problem.
#
# Aspect vocabulary
# -----------------
# French compass abbreviations (N, NE, E, SE, S, SO, O, NO) map 1-to-1 to
# CAAML tokens.  Full words (nord, nord-est, …) map via the same table.
# Cardinal groupings used as shorthand in the prose:
#   "ubacs"  → N, NE, NW  (north-facing — snow persists longest)
#   "adrets" → S, SE, SW   (south-facing — receives most solar radiation)
# Terminal phrases:
#   "toutes orientations" / "toutes expositions" → all 8 compass points.
#
# Elevation patterns
# ------------------
# Reuse the three regexes already established in
# ``apps/bulletins/services/meteofrance_translator.py``.  Return value mirrors the
# CAAML ``elevation`` sub-object used by the rest of the pipeline.
#
# Size mapping
# ------------
# EAWS size scale 1–5.  When a range is given ("taille 1 rarement 2") the
# **higher bound** is returned — the operational worst case.  Numeric "taille N"
# takes priority over prose labels.
#
# Frequency mapping
# -----------------
# EAWS: "few", "some", "many".
"""French-prose extraction helpers for BRA per-problem fields.

All public functions accept a plain ``str`` and return a value or ``None`` /
empty container when nothing matches.  They never raise.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Aspect extraction
# ---------------------------------------------------------------------------

# Abbreviated compass tokens (uppercase, as used in BRA compass-rose lists) →
# CAAML compass token.
_FR_ABBREV_MAP: dict[str, str] = {
    "NE": "NE",
    "SE": "SE",
    "SO": "SW",
    "NO": "NW",
    "N": "N",
    "E": "E",
    "S": "S",
    "O": "W",
}

# Full French direction words → CAAML compass token.
# "est" is intentionally excluded: in French prose it is also the verb "is"
# (e.g. "la neige est humide"), making it too noisy as a compass signal.
_FR_FULLWORD_MAP: dict[str, str] = {
    "nord-est": "NE",
    "nord-ouest": "NW",
    "sud-est": "SE",
    "sud-ouest": "SW",
    "nord": "N",
    "sud": "S",
    "ouest": "W",
}

# Combined map used by SECTEUR pattern and _aspects_from_grouping_words.
_FR_ASPECT_MAP: dict[str, str] = {**_FR_ABBREV_MAP, **_FR_FULLWORD_MAP}

# All 8 CAAML compass points in canonical order.
_ALL_ASPECTS: list[str] = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]

# "ubacs" = north-facing slopes (N, NE, NW).
_UBACS_ASPECTS: list[str] = ["N", "NE", "NW"]

# "adrets" = south-facing slopes (S, SE, SW).
_ADRETS_ASPECTS: list[str] = ["S", "SE", "SW"]

# Detect "toutes orientations" / "toutes expositions".
_ALL_ASPECTS_PAT = re.compile(
    r"toutes\s+(?:orientations?|expositions?)",
    re.IGNORECASE,
)

# Detect "secteur nord" / "secteur sud" etc. — a broad directional qualifier.
# Captured group 1 is the direction word.
_SECTEUR_PAT = re.compile(
    r"\bsecteur\s+(nord|sud|ouest|nord-est|nord-ouest|sud-est|sud-ouest)\b",
    re.IGNORECASE,
)

# Regex that matches abbreviated compass tokens as standalone tokens.
# Longest token first to avoid "N" matching before "NE" is tried.
# The character-class boundaries prevent "NE" matching inside "NEIGE".
_ABBREV_TOKENS = sorted(_FR_ABBREV_MAP.keys(), key=len, reverse=True)
_ABBREV_PAT = re.compile(
    r"(?<![A-Za-zÀ-ÿ])("
    + "|".join(re.escape(t) for t in _ABBREV_TOKENS)
    + r")(?![A-Za-zÀ-ÿ])"
)


def _aspects_from_grouping_words(text: str) -> list[str]:
    """Return aspects derived from grouping words (ubacs/adrets/secteur).

    Args:
        text: Prose text to scan.

    Returns:
        Sorted CAAML aspect list, or empty list if no grouping words found.

    """
    text_lower = text.lower()
    aspects: list[str] = []
    if "ubacs" in text_lower or "ubac" in text_lower:
        aspects.extend(_UBACS_ASPECTS)
    if "adrets" in text_lower or "adret" in text_lower:
        aspects.extend(_ADRETS_ASPECTS)
    for m in _SECTEUR_PAT.finditer(text):
        direction = m.group(1).lower()
        token = _FR_FULLWORD_MAP.get(direction) or _FR_ABBREV_MAP.get(direction.upper())
        if token and token not in aspects:
            aspects.append(token)
    return aspects


def _aspects_from_abbreviations(text: str) -> list[str]:
    """Return CAAML tokens for abbreviated compass labels found in *text*.

    Args:
        text: Prose text to scan.

    Returns:
        List of CAAML tokens in the order they are encountered.

    """
    tokens: list[str] = []
    for m in _ABBREV_PAT.finditer(text):
        token = _FR_ASPECT_MAP.get(m.group(1))
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _aspects_from_full_words(text: str) -> list[str]:
    """Return CAAML tokens for full French direction words found in *text*.

    Args:
        text: Lower-cased prose text to scan.

    Returns:
        List of CAAML tokens in the order they are encountered (longest
        multi-word forms checked first to avoid premature partial matches).

    """
    tokens: list[str] = []
    for fr_word, caaml_token in sorted(
        _FR_FULLWORD_MAP.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        if re.search(r"(?<![a-zà-ÿ])" + re.escape(fr_word) + r"(?![a-zà-ÿ])", text):
            if caaml_token not in tokens:
                tokens.append(caaml_token)
    return tokens


def extract_aspects(text: str) -> list[str]:
    """Extract CAAML compass aspects from French prose.

    Recognises:
    - "toutes orientations" / "toutes expositions" → all 8 points.
    - "ubacs" → N, NE, NW.
    - "adrets" → S, SE, SW.
    - "secteur nord/sud/…" → corresponding compass point.
    - Individual compass abbreviations (N, NE, E, SE, S, SO, O, NO).
    - Full direction words (nord, sud, ouest, …; "est" excluded — see module
      docstring).

    De-duplicates and returns in canonical clockwise order (N → NW).

    Args:
        text: Prose text from the stability/activity sections.

    Returns:
        List of CAAML aspect tokens in clockwise order, or ``[]`` if none
        found.

    """
    if not text:
        return []
    if _ALL_ASPECTS_PAT.search(text):
        return _ALL_ASPECTS.copy()

    seen: set[str] = set()
    raw: list[str] = (
        _aspects_from_grouping_words(text)
        + _aspects_from_abbreviations(text)
        + _aspects_from_full_words(text.lower())
    )
    unique = [t for t in raw if not (t in seen or seen.add(t))]  # type: ignore[func-returns-value]

    order = {a: i for i, a in enumerate(_ALL_ASPECTS)}
    return sorted(unique, key=lambda a: order.get(a, 99))


# ---------------------------------------------------------------------------
# Elevation extraction
# ---------------------------------------------------------------------------

# Reuse the same three patterns as apps/bulletins/services/meteofrance_translator.py,
# with one extension: _BETWEEN also handles the BRA prose form
# "Entre 2700 m et 3600 m" where 'm' appears after each altitude.
_ABOVE = re.compile(r"[Aa]u-dessus de (\d{3,4})\s?m")
_BELOW = re.compile(r"[Ee]n dessous de (\d{3,4})\s?m")
_BETWEEN = re.compile(r"[Ee]ntre\s+(\d{3,4})\s*m?\s*(?:et|à)\s*(\d{3,4})\s*m")


def extract_elevation(text: str) -> dict[str, int] | None:
    """Extract a CAAML elevation bounds dict from French prose.

    Priority: ``_BETWEEN`` → ``_ABOVE`` → ``_BELOW``.

    Args:
        text: Prose text to scan.

    Returns:
        Dict with integer ``lowerBound`` and/or ``upperBound`` keys, or
        ``None`` when no elevation phrase is found.

    """
    if not text:
        return None
    m = _BETWEEN.search(text)
    if m:
        return {"lowerBound": int(m.group(1)), "upperBound": int(m.group(2))}
    m = _ABOVE.search(text)
    if m:
        return {"lowerBound": int(m.group(1))}
    m = _BELOW.search(text)
    if m:
        return {"upperBound": int(m.group(1))}
    return None


# ---------------------------------------------------------------------------
# Avalanche size extraction
# ---------------------------------------------------------------------------

# Numeric "taille N [rarement M]" — capture one or two digits.
_SIZE_NUMERIC_PAT = re.compile(
    r"[Tt]aille\s+([1-5])(?:\s+(?:rarement|à|ou|et)\s+([1-5]))?",
    re.IGNORECASE,
)

# Prose size labels, ordered largest-to-smallest so the greedy match wins
# when "grande à très grande" is tested before "grande".
_SIZE_PROSE_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"extr[eê]me", re.IGNORECASE), "5"),
    (re.compile(r"tr[eè]s\s+grande", re.IGNORECASE), "4"),
    (re.compile(r"grande\s+[àa]\s+tr[eè]s\s+grande", re.IGNORECASE), "4"),
    (re.compile(r"moyenne\s+[àa]\s+grande", re.IGNORECASE), "3"),
    (re.compile(r"grande", re.IGNORECASE), "3"),
    (re.compile(r"petite\s*[-àa]\s*moyenne", re.IGNORECASE), "2"),
    (re.compile(r"petite\s+[àa]\s+moyenne", re.IGNORECASE), "2"),
    (re.compile(r"moyenne", re.IGNORECASE), "2"),
    (re.compile(r"petite", re.IGNORECASE), "1"),
]


def extract_avalanche_size(text: str) -> str | None:
    """Extract an EAWS avalanche size string from French prose.

    Numeric "Taille N" takes priority.  When a range is given ("Taille 1
    rarement 2") the **higher bound** is returned.

    Args:
        text: Prose text to scan.

    Returns:
        Size string ``"1"``–``"5"``, or ``None`` if no size phrase found.

    """
    if not text:
        return None

    m = _SIZE_NUMERIC_PAT.search(text)
    if m:
        lo = m.group(1)
        hi = m.group(2)
        # Return the higher bound when a range is given.
        if hi:
            return str(max(int(lo), int(hi)))
        return lo

    for pat, size in _SIZE_PROSE_MAP:
        if pat.search(text):
            return size

    return None


# ---------------------------------------------------------------------------
# Frequency extraction
# ---------------------------------------------------------------------------

_FREQUENCY_MAP: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bnombreuse[sx]?\b|\bnombreux\b", re.IGNORECASE), "many"),
    (re.compile(r"\bquelques\b", re.IGNORECASE), "some"),
    (
        re.compile(
            r"\brares?\b|\bisol[ée]e?s?\b",
            re.IGNORECASE,
        ),
        "few",
    ),
]


def extract_frequency(text: str) -> str | None:
    """Extract an EAWS frequency string from French prose.

    Args:
        text: Prose text to scan.

    Returns:
        ``"few"``, ``"some"``, or ``"many"``, or ``None`` if no frequency
        phrase is found.

    """
    if not text:
        return None
    for pat, freq in _FREQUENCY_MAP:
        if pat.search(text):
            return freq
    return None
