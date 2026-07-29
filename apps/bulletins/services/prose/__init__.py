"""
apps/bulletins/services/prose/__init__.py — Prose-parser registry for wet-snow comments.

Provides a language-keyed registry of prose parsers that extract aspect and
altitude tokens from free-text avalanche problem comments.  The registry is
intentionally language-keyed (not source-keyed) so that EAWS/ALBINA bulletins,
which already carry structured aspects/elevation in their JSON, simply never
register a parser — they are transparent no-ops.

When SLF starts publishing structured wet-snow fields, the English parser
(``en.py``) can be deleted with zero changes to any consumer: ``parse_for``
returns ``None`` for unregistered languages, and ``_build_problem`` in
``render_model.py`` only applies the result when ``aspects`` and ``elevation``
are both absent.

Public API
----------
``parse_for(lang, comment)``
    Attempt to extract a ``ParsedScope`` from *comment* using the parser
    registered for *lang*.  Returns ``None`` when no parser is registered for
    the language, or when the registered parser finds no tokens.

``register(lang, parser)``
    Attach *parser* callable to *lang*.  Called once at module import time by
    each language module (e.g. ``en.py`` calls ``register("en", _parse)``).

``ParsedScope``
    Dataclass returned by every parser.  ``aspects`` is a list of canonical
    compass-code strings (``"N"``, ``"NE"``, …, ``"NW"``); ``elevation`` is a
    dict matching the render-model elevation shape (``lower``, ``upper``,
    ``treeline``, ``treeline_side``).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ParsedScope dataclass
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ParsedScope:
    """
    Structured geographic scope extracted from wet-snow prose.

    ``aspects`` holds canonical compass codes (``"N"``, ``"NE"``, ``"E"``,
    ``"SE"``, ``"S"``, ``"SW"``, ``"W"``, ``"NW"``) in the order they appear
    in the source text.  Duplicates are removed while preserving order.

    ``elevation`` matches the render-model elevation dict shape produced by
    ``_parse_elevation`` in ``render_model.py``:
    ``{"lower": int|None, "upper": int|None, "treeline": bool,
    "treeline_side": str|None}``.
    """

    aspects: list[str]
    elevation: dict[str, object]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Mapping lang → parser callable.  Pure functions — no I/O.
_PARSERS: dict[str, Callable[[str], ParsedScope | None]] = {}


def register(lang: str, parser: Callable[[str], ParsedScope | None]) -> None:
    """
    Register *parser* for *lang*.

    Should be called exactly once per language module at import time.

    Args:
        lang: BCP-47 language code, e.g. ``"en"``.
        parser: A callable that accepts a comment string and returns a
            ``ParsedScope`` or ``None``.

    """
    _PARSERS[lang] = parser
    logger.debug("prose parser registered for lang=%r", lang)


def parse_for(lang: str, comment: str) -> ParsedScope | None:
    """
    Parse *comment* using the registered parser for *lang*.

    Returns ``None`` when no parser is registered for *lang*, when *comment*
    is empty, or when the registered parser finds no usable tokens.

    Args:
        lang: BCP-47 language code.
        comment: Raw HTML or plain-text problem comment from the bulletin.

    Returns:
        A ``ParsedScope`` when at least one token was extracted, else ``None``.

    """
    parser = _PARSERS.get(lang)
    if parser is None:
        return None
    if not comment or not comment.strip():
        return None
    return parser(comment)
