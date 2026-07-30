"""
apps/mcp_server/normalise.py — Name normaliser feeding the fuzzy region matcher.

Alpine place names arrive from users with wrong or missing accents
("Val d'Isère" / "Val d'Isere"), inconsistent punctuation ("St. Anton" /
"St Anton" / "Sankt Anton"), and inconsistent whitespace ("Val Thorens" /
"Val-Thorens"). ``normalise()`` reduces any of these variants to the same
canonical key so ``apps.mcp_server.resolvers.search_places`` can score them
fairly against each other with ``rapidfuzz``.

Pure function, no I/O — kept in its own module so it can be unit-tested in
isolation from the DB-backed candidate pool.
"""

from __future__ import annotations

import re
import unicodedata

# Matches a "sankt" or "st"/"st." token (case-insensitive, word-bounded) so
# it can be rewritten to "saint" before scoring. Applied after lowercasing;
# the trailing "." is optional because punctuation is stripped in the same
# pass that runs this substitution.
_SAINT_TOKEN_RE = re.compile(r"\bsankt\b|\bst\.?\b")

# Anything that isn't a letter, digit, or space becomes a space — this
# folds hyphens ("Val-Thorens"), apostrophes (handled explicitly below so
# "d'Isere" collapses to "disere" rather than "d isere"), and any other
# punctuation into word breaks.
_PUNCTUATION_RE = re.compile(r"[^a-z0-9 ]+")

# Collapses runs of whitespace (including ones newly created by the
# substitutions above) down to a single space.
_WHITESPACE_RE = re.compile(r"\s+")


def normalise(s: str) -> str:
    """Return a canonical, comparison-ready form of a place name.

    Pipeline (each step operates on the previous step's output):

    1. NFKD-normalise then drop non-ASCII bytes — strips accents
       (``Val d'Isère`` → ``Val d'Isere``, ``Zürs`` → ``Zurs``) while
       leaving the base Latin letters intact.
    2. Lowercase.
    3. Strip apostrophes outright (``d'isere`` → ``disere``) so an
       apostrophe-free query matches an apostrophe-bearing display name.
       (A curly ``’`` apostrophe is non-ASCII and is already gone after
       step 1 — this step only needs to catch the straight ``'``.)
    4. Rewrite ``sankt`` / ``st`` / ``st.`` to ``saint`` — the three
       spellings a bulletin or a user might use for the same honorific.
    5. Replace any remaining punctuation or hyphens with a space.
    6. Collapse whitespace runs to one space and trim.

    Args:
        s: The raw place name or search query.

    Returns:
        The normalised string — lowercase ASCII letters, digits, and single
        spaces only.

    """
    folded = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    lowered = folded.lower()
    no_apostrophes = lowered.replace("'", "")
    saint_form = _SAINT_TOKEN_RE.sub("saint", no_apostrophes)
    no_punctuation = _PUNCTUATION_RE.sub(" ", saint_form)
    return _WHITESPACE_RE.sub(" ", no_punctuation).strip()
