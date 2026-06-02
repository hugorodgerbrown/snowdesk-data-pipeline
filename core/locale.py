"""
core/locale.py — HTTP Accept-Language parsing utilities.

Thin helpers for extracting the user's preferred language from the
``Accept-Language`` HTTP header. Kept in ``core`` so any app can import
without creating circular dependencies.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def primary_language(accept_language: str) -> str:
    """Return the primary language subtag from an Accept-Language header value.

    Parses the highest-weighted language tag and returns the primary subtag
    (i.e. everything before the first ``-`` or ``_`` separator). For example:

    - ``"en-GB,en;q=0.9,fr;q=0.8"``  → ``"en"``
    - ``"de-CH"``                      → ``"de"``
    - ``"fr"``                         → ``"fr"``
    - ``""``                           → ``""``
    - ``"invalid@@"``                  → ``""``

    The first listed tag is assumed to be the highest-quality one (browsers
    always put the preferred language first). No q-value sorting is performed;
    the function just takes the first tag in the list.

    Args:
        accept_language: The raw ``Accept-Language`` header value.

    Returns:
        A lowercase primary subtag string (e.g. ``"en"``, ``"de"``, ``"fr"``),
        or ``""`` when the input is empty or unparseable.

    """
    if not accept_language or not accept_language.strip():
        return ""

    try:
        # Take the first tag in the comma-separated list.
        first_tag = accept_language.split(",")[0].strip()
        # Strip any q-value qualifier (e.g. "en;q=0.9" → "en").
        lang_tag = first_tag.split(";")[0].strip()
        if not lang_tag:
            return ""
        # Return only the primary subtag (before the first "-" or "_").
        primary = lang_tag.replace("_", "-").split("-")[0].lower()
        # Sanity-check: a valid primary subtag is 2–8 ASCII alpha chars.
        if primary and primary.isalpha() and 2 <= len(primary) <= 8:
            return primary
        return ""
    except Exception:  # noqa: BLE001 — never propagate; caller expects "" on any failure
        logger.debug("locale: failed to parse Accept-Language %r", accept_language)
        return ""
