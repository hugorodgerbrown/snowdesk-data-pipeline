r"""
bulletins/services/prose/en.py — English prose parser for wet-snow problems.

Extracts aspect and altitude tokens from SLF English wet-snow comment text
using a constrained phrase grammar.  The parser is deliberately simple: it
operates on the raw HTML/plain-text comment that SLF embeds in each avalanche
problem entry and applies regular-expression rules in a fixed priority order.

Module contract
---------------
* **Pure functions, no Django imports.**  This module can be imported by any
  consumer without Django being initialised.
* **Registered under ``"en"``** — not under a source identifier.  EAWS/ALBINA
  bulletins already carry structured data and never call this parser; the
  registry shim in ``__init__.py`` returns ``None`` for unregistered languages.
* **Removable.**  If SLF begins publishing structured wet-snow aspects/elevation
  in their JSON, this entire module (and its ``register()`` call) can be
  deleted without changing any consumer.  ``render_model.parse_for`` will
  simply return ``None`` for the ``"en"`` language again.

Aspect grammar
--------------
Matched tokens are normalised to canonical compass codes:

  ``"N"``, ``"NE"``, ``"E"``, ``"SE"``, ``"S"``, ``"SW"``, ``"W"``, ``"NW"``

Recognised patterns (case-insensitive):

* Compass codes ``N``, ``NE``, ``E``, ``SE``, ``S``, ``SW``, ``W``, ``NW``
  — only at word boundaries (``\\b``) to avoid matching inside words like
  "ANNEX" or "South".
* ``north``, ``south``, ``east``, ``west`` (four cardinal names).
* ``northeast`` / ``north-east``, ``northwest`` / ``north-west``,
  ``southeast`` / ``south-east``, ``southwest`` / ``south-west``.
* Terminal phrases: ``"all aspects"``, ``"shady aspects"``, ``"sunny aspects"``.
  These expand to the appropriate aspect set (see ``_TERMINAL_ASPECTS``).

Elevation grammar
-----------------
Matched phrases are projected onto the render-model elevation dict shape
(``lower``, ``upper``, ``treeline``, ``treeline_side``).

Recognised patterns:

* ``below (approximately )? <N> m``  → ``upper=N, lower=None``
* ``above (approximately )? <N> m``  → ``lower=N, upper=None``
* ``between <N> and <M> m``          → ``lower=N, upper=M``
* ``all altitudes``                  → ``lower=None, upper=None, treeline=False``
* ``intermediate altitudes``         → alias for *all altitudes*
* ``high altitudes`` / ``high alpine`` → ``lower=2000, upper=None``
* ``tree line`` / ``treeline``       → treeline pivot (``treeline=True``)

**Direction sensitivity for wet snow.**  Wet-snow altitude bounds are
*lower*-biased: a wet-snow problem "below 2400 m" means the hazard is
confined to the lower elevations (``upper=2400``).  An "above 2000 m"
citation on a wet-snow problem is less common but still correct — the
parser captures it faithfully; the caller decides how to display the
constraint.

**Multiple altitude phrases.**  When both ``below 2400 m`` and ``between
1800 and 2200 m`` appear in the same comment, the **first** match in the
prose wins.  This is the closest to the problem statement and therefore
the most authoritative description of the primary concern.

HTML stripping
--------------
SLF comment fields may contain HTML tags (``<p>``, ``<em>``, etc.).  The
parser strips tags with a simple ``<…>`` regex before applying the grammar
so that phrase boundaries are not broken by inline markup.  This avoids
importing ``bleach`` / ``html.parser`` in a pure-function module.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from bulletins.services.prose import ParsedScope, register

# ---------------------------------------------------------------------------
# HTML stripper
# ---------------------------------------------------------------------------


class _HTMLStripper(HTMLParser):
    """Minimal HTMLParser subclass that accumulates only text content."""

    def __init__(self) -> None:
        """Initialise with an empty buffer."""
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Accumulate text node."""
        self._parts.append(data)

    def get_text(self) -> str:
        """Return the concatenated text content."""
        return " ".join(self._parts)


def _strip_html(text: str) -> str:
    """
    Strip HTML tags from *text* and return plain text.

    Uses stdlib ``html.parser`` — no third-party dependency.

    Args:
        text: Raw HTML or plain-text string.

    Returns:
        Plain text with tags removed and entity references decoded.

    """
    stripper = _HTMLStripper()
    stripper.feed(text)
    return stripper.get_text()


# ---------------------------------------------------------------------------
# Aspect patterns
# ---------------------------------------------------------------------------

# Map from matched group / terminal phrase to one or more canonical codes.
_TERMINAL_ASPECTS: dict[str, list[str]] = {
    "all aspects": ["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
    "shady aspects": ["N", "NE", "NW"],
    "sunny aspects": ["S", "SE", "SW"],
}

# Named compass word/phrase → canonical code.
_WORD_TO_CODE: dict[str, str] = {
    "north": "N",
    "northeast": "NE",
    "north-east": "NE",
    "north east": "NE",
    "northwest": "NW",
    "north-west": "NW",
    "north west": "NW",
    "east": "E",
    "southeast": "SE",
    "south-east": "SE",
    "south east": "SE",
    "southwest": "SW",
    "south-west": "SW",
    "south west": "SW",
    "south": "S",
    "west": "W",
}

# Regex for cardinal/ordinal direction words.  Built from longest-first to
# avoid "north" short-circuiting "northeast" etc.
_WORD_PATTERNS: list[str] = sorted(_WORD_TO_CODE.keys(), key=len, reverse=True)
_WORD_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in _WORD_PATTERNS) + r")\b",
    re.IGNORECASE,
)

# Regex for standalone compass codes (N, NE, E, SE, S, SW, W, NW).
# Must be whole-word tokens — use \b boundaries.
_CODE_RE = re.compile(
    r"\b(N|NE|E|SE|S|SW|W|NW)\b",
)

# Terminal-phrase patterns (tried first — they match the whole scope).
_TERMINAL_RE = re.compile(
    r"\b(all aspects|shady aspects|sunny aspects)\b",
    re.IGNORECASE,
)


def _parse_aspects(text: str) -> list[str]:
    """
    Extract aspect tokens from *text*.

    Terminal phrases (``all aspects`` etc.) are checked first and short-circuit
    further scanning.  Otherwise both compass-word patterns and standalone
    compass-code patterns are applied and the results de-duplicated while
    preserving first-encounter order.

    Args:
        text: Plain-text comment (HTML already stripped).

    Returns:
        List of canonical compass codes, de-duplicated, in encounter order.

    """
    # Terminal-phrase short-circuit.
    terminal_match = _TERMINAL_RE.search(text)
    if terminal_match:
        phrase = terminal_match.group(1).lower()
        return list(_TERMINAL_ASPECTS.get(phrase, []))

    seen: dict[str, None] = {}  # ordered set via insertion-order dict

    # Collect word matches.
    for m in _WORD_RE.finditer(text):
        code = _WORD_TO_CODE.get(m.group(1).lower())
        if code and code not in seen:
            seen[code] = None

    # Collect standalone code matches (only if no word matches found, to avoid
    # double-counting "North (N)" style prose).
    if not seen:
        for m in _CODE_RE.finditer(text):
            code = m.group(1)
            if code not in seen:
                seen[code] = None

    return list(seen.keys())


# ---------------------------------------------------------------------------
# Elevation patterns
# ---------------------------------------------------------------------------

# "below (approximately )? <N> [m]"
_BELOW_RE = re.compile(
    r"\bbelow\s+(?:approximately\s+)?(\d{3,4})\s*m\b",
    re.IGNORECASE,
)

# "above (approximately )? <N> [m]"
_ABOVE_RE = re.compile(
    r"\babove\s+(?:approximately\s+)?(\d{3,4})\s*m\b",
    re.IGNORECASE,
)

# "between <N> and <M> [m]"
_BETWEEN_RE = re.compile(
    r"\bbetween\s+(\d{3,4})\s+and\s+(\d{3,4})\s*m\b",
    re.IGNORECASE,
)

# Terminal elevation phrases.
_ELEV_TERMINAL_RE = re.compile(
    r"\b(all altitudes|intermediate altitudes"
    r"|high altitudes|high alpine|tree line|treeline)\b",
    re.IGNORECASE,
)

# Canonical empty elevation dict shape — no bounds, no treeline.
_ELEV_UNCONSTRAINED: dict[str, object] = {
    "lower": None,
    "upper": None,
    "treeline": False,
    "treeline_side": None,
}

# High-altitude bound used for "high altitudes" / "high alpine".
_HIGH_ALTITUDE_LOWER = 2000


def _first_elevation_match(text: str) -> dict[str, object] | None:
    """
    Find the first elevation phrase in *text* and return its dict.

    "First" is determined by the earliest character position among all
    patterns.  Returns ``None`` when no elevation phrase is found.

    Args:
        text: Plain-text comment (HTML already stripped).

    Returns:
        Render-model elevation dict or ``None``.

    """
    candidates: list[tuple[int, dict[str, object]]] = []

    # Numeric patterns.
    m = _BELOW_RE.search(text)
    if m:
        candidates.append(
            (
                m.start(),
                {
                    "lower": None,
                    "upper": int(m.group(1)),
                    "treeline": False,
                    "treeline_side": None,
                },
            )
        )

    m = _ABOVE_RE.search(text)
    if m:
        candidates.append(
            (
                m.start(),
                {
                    "lower": int(m.group(1)),
                    "upper": None,
                    "treeline": False,
                    "treeline_side": None,
                },
            )
        )

    m = _BETWEEN_RE.search(text)
    if m:
        candidates.append(
            (
                m.start(),
                {
                    "lower": int(m.group(1)),
                    "upper": int(m.group(2)),
                    "treeline": False,
                    "treeline_side": None,
                },
            )
        )

    # Terminal phrases.
    m = _ELEV_TERMINAL_RE.search(text)
    if m:
        phrase = m.group(1).lower()
        if phrase in ("all altitudes", "intermediate altitudes"):
            elev: dict[str, object] = dict(_ELEV_UNCONSTRAINED)
        elif phrase in ("high altitudes", "high alpine"):
            elev = {
                "lower": _HIGH_ALTITUDE_LOWER,
                "upper": None,
                "treeline": False,
                "treeline_side": None,
            }
        else:
            # "tree line" / "treeline"
            elev = {
                "lower": None,
                "upper": None,
                "treeline": True,
                "treeline_side": "upper",  # wet-snow → hazard below treeline
            }
        candidates.append((m.start(), elev))

    if not candidates:
        return None

    # Pick the first match by character position.
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Top-level parser
# ---------------------------------------------------------------------------


def _parse(comment: str) -> ParsedScope | None:
    """
    Parse *comment* and return a ``ParsedScope`` or ``None``.

    Returns ``None`` when neither aspects nor elevation tokens are found,
    so that the caller can detect a true no-match without examining the
    dataclass fields.

    Args:
        comment: Raw HTML or plain-text problem comment.

    Returns:
        ``ParsedScope`` when at least one token was found, else ``None``.

    """
    text = _strip_html(comment)
    aspects = _parse_aspects(text)
    elevation = _first_elevation_match(text)

    if not aspects and elevation is None:
        return None

    return ParsedScope(
        aspects=aspects,
        elevation=elevation or dict(_ELEV_UNCONSTRAINED),
    )


# Register this parser for the "en" language code.
register("en", _parse)
