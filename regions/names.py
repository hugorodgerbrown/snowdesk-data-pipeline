"""regions/names.py — EAWS canonical region-name lookup helper.

Provides a single public function, ``lookup``, that returns the human-readable
name of an EAWS region ID (or parent prefix) in the requested language.  Names
are sourced from the four vendored ``reference_data/eaws/names/<lang>.json``
files (CC0, upstream: https://gitlab.com/eaws/eaws-regions/-/tree/master/public/micro-regions_names).

Supported languages: ``en``, ``de``, ``fr``, ``it``.

Coverage: EAWS publishes names for every region in their dataset, which
includes all L1/L2/L4 IDs for AT and IT, all L4 IDs for CH and FR, and all
L4 IDs for other EAWS member countries.  The ``lookup`` function returns
``None`` for any key not present in the file so callers can fall back gracefully.

Usage::

    from regions.names import lookup

    lookup("AT-02-14", "de")   # → "Karnische Alpen Lesachtal"
    lookup("AT-02",    "en")   # → "Carinthia"
    lookup("MISSING",  "en")   # → None
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path to the vendored name files (module-level so tests can patch it)
# ---------------------------------------------------------------------------

_NAMES_DIR = (
    Path(__file__).resolve().parent.parent / "reference_data" / "eaws" / "names"
)

# ---------------------------------------------------------------------------
# Module-level cache — each language file is parsed at most once per process.
# ---------------------------------------------------------------------------

_cache: dict[str, dict[str, str]] = {}


def _load(lang: str) -> dict[str, str]:
    """Load and cache the name mapping for *lang*.

    Reads ``reference_data/eaws/names/<lang>.json`` on first call per language;
    subsequent calls return the cached dict.

    Args:
        lang: ISO 639-1 language code (``"en"``, ``"de"``, ``"fr"``, or ``"it"``).

    Returns:
        A dict mapping EAWS region keys (e.g. ``"AT-02-14"``) to human-readable
        names in the requested language.

    Raises:
        FileNotFoundError: If the language file does not exist under
            ``_NAMES_DIR``.

    """
    if lang not in _cache:
        path = _NAMES_DIR / f"{lang}.json"
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        _cache[lang] = {str(k): str(v) for k, v in data.items()}
        logger.debug("names: loaded %d entries from %s", len(_cache[lang]), path)
    return _cache[lang]


def lookup(key: str, lang: str) -> str | None:
    """Return the canonical EAWS name for *key* in *lang*, or ``None`` on miss.

    Args:
        key: An EAWS region identifier or country prefix, e.g. ``"AT-02-14"``,
            ``"AT-02"``, or ``"FR-01"``.
        lang: ISO 639-1 language code (``"en"``, ``"de"``, ``"fr"``, or ``"it"``).

    Returns:
        The human-readable name string, or ``None`` if *key* is not present in
        the name file for *lang*.

    """
    return _load(lang).get(key)
