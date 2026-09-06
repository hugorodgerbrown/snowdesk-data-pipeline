"""
tests/glossary_markup.py — Subtract the EAWS glossary injection from HTML.

``snowdesk_html`` splices a ``<button class="glossary-term">`` and a hidden
``<span popover class="glossary-def">`` into every prose text node that names
an EAWS term (SNOW-853). Three test surfaces need to see the prose as the
reader does at rest — with the term in place and the definition off-screen —
rather than as the raw markup:

* ``tests/public/templatetags/test_snowdesk_html.py`` asserts the provider's
  words survive the injection character-for-character;
* ``tests/sentinels/fidelity.py`` runs contiguity probes over the page's
  visible text, which a button boundary mid-sentence would break;
* ``tests/public/test_field_guidance.py`` asserts on sentences that happen to
  contain a marked term.

One helper rather than three copies, because the markup it matches is one
format string in one module.
"""

from __future__ import annotations

import re

# The popover span carrying the definition. Dropped outright: it is in the
# top layer only once the reader opens it, so it is not part of the page's
# visible text.
_POPOVER = re.compile(
    r'<span popover id="[^"]*" class="glossary-def">.*?</span>', re.DOTALL
)

# The button wrapping the matched term. Unwrapped rather than dropped: the
# term itself is the provider's own word and stays on the page.
_TERM = re.compile(r'<button[^>]*class="glossary-term">(.*?)</button>', re.DOTALL)


def strip_glossary_markup(html: str) -> str:
    """
    Return *html* with every injected glossary wrapper subtracted.

    The inverse of the injection: popovers are removed, term buttons are
    unwrapped to the text they contain. For prose whose tags are all on the
    ``snowdesk_html`` allowlist, the result is the sanitised string the
    injection was handed.

    Args:
        html: Rendered HTML, a fragment or a whole page.

    Returns:
        The same HTML with the button/popover pairs collapsed to term text.

    """
    return _TERM.sub(r"\1", _POPOVER.sub("", html))
