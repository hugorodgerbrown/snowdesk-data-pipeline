"""
apps/public/glossary.py — Load and compile the EAWS glossary term list.

``apps/public/eaws_glossary.yaml`` holds one entry per EAWS glossary term
that turns up in provider prose: the standard's own definition text (copied
verbatim — never paraphrased), the surface forms to match on, and the term's
anchor on <https://www.avalanches.org/glossary/> so the reader can reach the
source.

Two functions, both ``functools.cache``d:

``load_glossary()``
    Mirrors ``apps.public.guidance.load_field_guidance()`` — open the YAML
    next to this module, ``yaml.safe_load``, return a flat dict. Cached
    because, unlike the once-per-problem-card guidance lookup, this is read
    for every prose block on every bulletin render.

``glossary_matcher()``
    Compiles the term matcher once: a single case-insensitive alternation
    over every synonym of every entry, **longest alternative first** so that
    "melt-freeze crust" wins over "crust", each alternative ``re.escape``d
    and word-boundary anchored so "slab" cannot match inside "slabby".
    Returns the compiled pattern plus a matched-text-lowercased → term-key
    lookup, because the pattern itself cannot say which entry it hit.

The consumer is ``apps.public.templatetags.snowdesk_html``, which marks the
matched terms up *after* sanitisation — see
``docs/decisions/glossary-terms-injected-after-sanitisation.md``.
"""

import functools
import re
from pathlib import Path
from typing import Any

import yaml

# The YAML lives beside this module, exactly as field_guidance.yaml lives
# beside guidance.py.
_GLOSSARY_PATH = Path(__file__).parent / "eaws_glossary.yaml"


@functools.cache
def load_glossary() -> dict[str, dict[str, Any]]:
    """
    Load the EAWS glossary terms from YAML.

    Returns a dict keyed by term slug (e.g. ``"avalanche_prone_location"``),
    each value a dict with ``anchor`` (the term's id on the EAWS glossary
    page), ``synonyms`` (a list of lower-case surface forms) and ``text``
    (the verbatim EAWS definition, whitespace-collapsed and stripped).

    Cached: the file never changes at runtime and the result is read on
    every prose block of every bulletin render.

    Returns:
        The parsed glossary, keyed by term slug.

    """
    with _GLOSSARY_PATH.open(encoding="utf-8") as fh:
        data: dict[str, dict[str, Any]] = yaml.safe_load(fh)
    return {
        key: {
            "anchor": entry["anchor"].strip(),
            "synonyms": [synonym.strip() for synonym in entry["synonyms"]],
            "text": " ".join(entry["text"].split()),
        }
        for key, entry in data.items()
    }


@functools.cache
def synonym_index() -> dict[str, str]:
    """
    Map every lower-cased synonym to the term slug that owns it.

    A synonym belonging to two terms would make the match ambiguous, so the
    file is expected to keep them unique — ``tests/public/test_glossary.py``
    asserts it, because a silent last-one-wins here would show the reader
    the wrong definition.

    Returns:
        A dict of lower-cased synonym → term slug.

    """
    return {
        synonym.lower(): key
        for key, entry in load_glossary().items()
        for synonym in entry["synonyms"]
    }


@functools.cache
def synonym_alternatives() -> tuple[str, ...]:
    """
    Return every synonym ordered longest-first, then alphabetically.

    Longest-first is what makes the alternation greedy in the way a reader
    expects: Python's ``|`` takes the first alternative that matches at a
    position, so "crust" listed before "melt-freeze crust" would mark the
    shorter, less specific term. The alphabetical tie-break keeps the
    compiled pattern deterministic across runs.

    Returns:
        A tuple of lower-cased synonyms, longest first.

    """
    return tuple(sorted(synonym_index(), key=lambda s: (-len(s), s)))


@functools.cache
def glossary_matcher() -> tuple[re.Pattern[str], dict[str, str]]:
    r"""
    Compile the term matcher once and return it with its term lookup.

    The pattern is one case-insensitive alternation over every synonym,
    longest first, each ``re.escape``d and wrapped in ``\b`` word
    boundaries so a term is only marked when it stands as a whole word.

    Returns:
        A 2-tuple of the compiled pattern and the lower-cased synonym →
        term-slug lookup needed to resolve whatever the pattern matched.

    """
    alternation = "|".join(re.escape(synonym) for synonym in synonym_alternatives())
    pattern = re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)
    return pattern, synonym_index()
