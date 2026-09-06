"""
apps/public/templatetags/snowdesk_html.py — Template filters for SLF prose.

``snowdesk_html`` sanitises SLF prose HTML strings before they are rendered in
templates.  SLF prose fields (snowpackStructure, weatherReview, weatherForecast,
tendency[].comment) arrive as raw HTML from the API and are stored untouched in
the render model.  Sanitisation is a render-time concern so the allowlist can
be changed without triggering a render-model rebuild.

``prose_title`` and ``prose_body`` pair up to hoist the leading ``<h1>`` out of
an SLF prose block — SLF always starts every prose string with a context-rich
``<h1>`` (e.g. ``"Weather review for Thursday"``) which makes a better panel
summary than a static label.  ``prose_title`` returns the stripped title text
(falling back to a caller-supplied default); ``prose_body`` returns the prose
HTML with the leading ``<h1>`` removed so the body doesn't duplicate it.

``snowdesk_html`` runs ``bleach.clean`` with a strict allowlist of structural
tags only (``h1``, ``h2``, ``p``, ``ul``, ``li``, ``strong``, ``em``).  All
attributes and protocols are removed.  Disallowed tags are *stripped* (not
escaped) so that unknown or dangerous tags disappear silently from the output.

It then marks up EAWS glossary terms *in the sanitised output* (SNOW-853):
each term's first occurrence in a block becomes a ``<button>`` paired with a
native ``[popover]`` carrying the standard's own definition, so a reader meets
the explanation where the word is used and without any JavaScript.  The order
— ``bleach.clean`` → inject → ``mark_safe`` — is load-bearing; see
``docs/decisions/glossary-terms-injected-after-sanitisation.md``.
"""

import collections
import hashlib
import html
import logging
import re
from html.parser import HTMLParser

import bleach
from django import template
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import gettext

from apps.public.glossary import glossary_matcher, load_glossary

logger = logging.getLogger(__name__)

register = template.Library()

# Matches the leading <h1>…</h1> of an SLF prose block, allowing for leading
# whitespace and any attributes on the opening tag.  Non-greedy body match so
# we only consume the first heading.
_LEADING_H1_RE = re.compile(r"^\s*<h1\b[^>]*>(.*?)</h1>", re.DOTALL | re.IGNORECASE)

# Tags that SLF prose is known to contain and that are safe to render.
# This list is intentionally conservative — add tags here only when SLF
# actually ships them and the template needs to render them.
#
# SNOW-853: do NOT widen this to admit the ``button``/``span``/``a`` that the
# glossary injection emits.  Injection runs *after* ``bleach.clean``, so
# bleach never re-validates the injected markup — widening the allowlist would
# achieve nothing except letting *provider prose* ship a ``<button>``, a
# ``popovertarget`` and an ``href`` it currently cannot.  The narrow list is
# the correct one, and it stays narrow precisely because of the injection.
_ALLOWED_TAGS: list[str] = ["h1", "h2", "p", "ul", "li", "strong", "em"]

# No attributes are expected or required in SLF prose.  Same rule as above:
# the injected markup's attributes are ours, added downstream of bleach, and
# are not a reason to allow provider attributes through.
_ALLOWED_ATTRIBUTES: dict[str, list[str]] = {}

# No link protocols are expected in SLF prose.
_ALLOWED_PROTOCOLS: list[str] = []

# Block-level elements that reset the "already marked" term set.  A term is
# marked once per block, so a paragraph naming "weak layer" nine times gets
# one button, but the next paragraph gets its own.
_BLOCK_TAGS: frozenset[str] = frozenset({"p", "li", "h1", "h2"})

# Where the popover's provenance link points.  The per-term fragment is the
# entry's ``anchor`` field.
_EAWS_GLOSSARY_URL = "https://www.avalanches.org/glossary/"

# The injected markup.  Deliberately one unbroken line: it is spliced into
# running prose, and a newline between the button and its popover would put a
# stray space into the provider's sentence.
_TERM_MARKUP = (
    '<button type="button" popovertarget="{popover_id}" '
    'class="glossary-term">{term}</button>'
    '<span popover id="{popover_id}" class="glossary-def">{definition}'
    '<a href="{url}#{anchor}" class="glossary-src">{source_label}</a></span>'
)


class _GlossaryInjector(HTMLParser):
    """
    Mark EAWS glossary terms in the text nodes of an already-sanitised block.

    Everything that is not a text node — start tags, end tags, entity and
    character references, comments, declarations — is re-emitted
    **byte-identical**, start tags via ``get_starttag_text()`` so nothing is
    normalised on the way through.  Only ``handle_data`` output is rewritten.

    ``convert_charrefs`` is off so that ``&amp;`` arrives as an entity
    reference and leaves as one; with it on, the parser would hand us the
    decoded character and we would emit a bare ``&`` back into the HTML.

    Attributes:
        out: Accumulated output fragments, joined by ``result()``.

    """

    def __init__(self, token: str) -> None:
        """
        Prepare an injector for one prose block.

        Args:
            token: Short digest of the block, mixed into every popover id so
                that two blocks on one page cannot collide.

        """
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self._token = token
        self._pattern, self._lookup = glossary_matcher()
        self._glossary = load_glossary()
        self._seen: set[str] = set()
        self._emitted: collections.Counter[str] = collections.Counter()

    def result(self) -> str:
        """Return the rewritten HTML for the block."""
        return "".join(self.out)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Re-emit a start tag verbatim, resetting the seen set at a block."""
        if tag in _BLOCK_TAGS:
            self._seen.clear()
        self.out.append(self.get_starttag_text() or f"<{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Re-emit a self-closing tag verbatim."""
        self.out.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        """Re-emit an end tag."""
        self.out.append(f"</{tag}>")

    def handle_entityref(self, name: str) -> None:
        """Re-emit a named entity reference unchanged."""
        self.out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        """Re-emit a numeric character reference unchanged."""
        self.out.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        """Re-emit a comment unchanged."""
        self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        """Re-emit a declaration unchanged."""
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data: str) -> None:
        """Re-emit a processing instruction unchanged."""
        self.out.append(f"<?{data}>")

    def unknown_decl(self, data: str) -> None:
        """Re-emit an unrecognised declaration (e.g. CDATA) unchanged."""
        self.out.append(f"<![{data}]>")

    def handle_data(self, data: str) -> None:
        """Mark the first occurrence of each glossary term in a text node."""
        self.out.append(self._mark(data))

    def _mark(self, data: str) -> str:
        """
        Return ``data`` with each term's first occurrence in this block wrapped.

        Unmatched text is copied through untouched — escaping it would
        rewrite characters bleach already settled and break the guarantee
        that the provider's words survive character-for-character.
        """
        pieces: list[str] = []
        cursor = 0
        for match in self._pattern.finditer(data):
            key = self._lookup.get(match.group(0).lower())
            if key is None or key in self._seen:
                continue
            self._seen.add(key)
            pieces.append(data[cursor : match.start()])
            pieces.append(self._render(key, match.group(0)))
            cursor = match.end()
        if not pieces:
            return data
        pieces.append(data[cursor:])
        return "".join(pieces)

    def _render(self, key: str, matched: str) -> str:
        """
        Build the button + popover pair for one matched term.

        The matched text is escaped before it is placed inside the button —
        it comes from provider prose and must never be interpolated as
        markup.  The definition is escaped too: it is our own text, but EAWS
        wording contains bare ``>`` characters (see ``weak_layer``).

        ``quote=False`` on both: these land in element content, not in an
        attribute value, and escaping apostrophes there would alter the
        provider's characters for no security gain.

        The seen-set resets at every block, so one term can legitimately be
        marked in two paragraphs of the *same* prose block — which share one
        digest.  A per-block ordinal is appended from the second emission on,
        because two elements with the same ``id`` is invalid HTML and
        ``popovertarget`` resolves to whichever came first.

        Args:
            key: The glossary term slug that was matched.
            matched: The exact surface form found in the prose.

        Returns:
            The HTML fragment replacing the matched text.

        """
        entry = self._glossary[key]
        ordinal = self._emitted[key]
        self._emitted[key] += 1
        suffix = "" if ordinal == 0 else f"-{ordinal + 1}"
        popover_id = f"g-{key.replace('_', '-')}-{self._token}{suffix}"
        return _TERM_MARKUP.format(
            popover_id=popover_id,
            term=html.escape(matched, quote=False),
            definition=html.escape(entry["text"], quote=False),
            url=_EAWS_GLOSSARY_URL,
            anchor=entry["anchor"],
            source_label=gettext("EAWS glossary"),
        )


def inject_glossary_terms(cleaned: str) -> str:
    """
    Mark EAWS glossary terms inside an already-sanitised prose block.

    Runs on ``bleach.clean`` output, never on raw provider HTML: the input is
    known to be six structural tags with no attributes, which is what makes a
    stdlib ``HTMLParser`` walk sufficient and keeps the bleach allowlist free
    of the tags this emits.

    Popover ids are ``g-<term-slug>-<token>`` where the token is a short
    digest of the block itself — deterministic (so tests can assert exact
    output), distinct per block (so two prose panels on one page cannot
    collide) and stable across re-renders and HTMX swaps of the same block.

    The definition text is emitted straight into the output and never fed
    back through the matcher, so a definition that mentions another term does
    not itself gain a button. One level of explanation, deliberately.

    Args:
        cleaned: Sanitised prose HTML.

    Returns:
        The same HTML with glossary terms marked up.

    """
    token = hashlib.blake2s(cleaned.encode()).hexdigest()[:6]
    injector = _GlossaryInjector(token)
    injector.feed(cleaned)
    injector.close()
    return injector.result()


@register.filter
def snowdesk_html(value: str | None) -> SafeString:
    """
    Sanitise an SLF HTML prose string and return a ``SafeString``.

    Runs ``bleach.clean`` with a strict tag allowlist.  Disallowed tags are
    stripped (not escaped) so that unexpected markup vanishes rather than
    becoming visible text.  Returns an empty ``SafeString`` when given
    ``None`` or an empty string.

    EAWS glossary terms are then marked up in the sanitised text (SNOW-853).
    Injection is deliberately the *second* step: bleach never sees — and so
    never has to be taught to allow — the ``<button>`` and ``[popover]`` this
    adds.

    Usage::

        {{ bulletin.prose.snowpack_structure|snowdesk_html }}

    Args:
        value: Raw HTML string from the SLF render model, or ``None``.

    Returns:
        A ``SafeString`` containing only the allowed structural tags,
        safe to render with Django's default auto-escaping.

    """
    if not value:
        return mark_safe("")  # noqa: S308 — empty string carries no XSS risk

    cleaned = bleach.clean(
        value,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
    marked = inject_glossary_terms(cleaned)
    # nosemgrep: python.django.security.audit.avoid-mark-safe.avoid-mark-safe
    return mark_safe(marked)  # noqa: S308 — bleach-sanitised above; the glossary markup is ours


@register.filter
def prose_title(value: str | None, fallback: str = "") -> str:
    """
    Extract the leading ``<h1>`` text from an SLF prose block.

    SLF prose always begins with a context-rich heading (e.g. ``"Weather
    review for Thursday"``) that is a better panel summary than a static
    label.  This filter returns that heading as plain text, stripped of any
    nested inline tags, or the ``fallback`` value when no leading ``<h1>``
    is present or the value is empty.

    The return type is a plain ``str`` so Django's auto-escaping applies
    when it lands in the template — the caller cannot end up rendering
    unexpected markup in a ``<summary>``.

    Usage::

        {{ prose.snowpack_structure|prose_title:"Snowpack" }}
    """
    if not value:
        return fallback
    match = _LEADING_H1_RE.match(value)
    if not match:
        return fallback
    # Strip any nested markup so the title is plain text only.
    plain = bleach.clean(match.group(1), tags=[], strip=True).strip()
    return plain or fallback


@register.filter
def prose_body(value: str | None) -> str:
    """
    Return an SLF prose block with the leading ``<h1>`` removed.

    Pairs with ``prose_title``: the h1 becomes the panel summary and this
    filter yields the remaining HTML for the body, avoiding a duplicated
    heading.  The returned string is still raw (unsanitised) HTML — pipe
    it through ``snowdesk_html`` when rendering.

    Usage::

        {{ prose.snowpack_structure|prose_body|snowdesk_html }}
    """
    if not value:
        return ""
    return _LEADING_H1_RE.sub("", value, count=1).lstrip()


@register.filter
def tendency_has_comment(prose: dict | None) -> bool:
    """
    Return ``True`` when ``prose.tendency`` contains at least one non-empty comment.

    ALBINA bulletins ship a ``tendency`` list whose entries carry
    ``highlights`` text but an empty ``comment``.  The Outlook panel renders
    from ``comment``, so it would otherwise show an empty body.  This filter
    lets the template fall back to the "No data supplied" placeholder when
    no entry has usable comment text.

    Usage::

        {% with has_outlook=prose|tendency_has_comment %}
            {% if has_outlook %}
                …
            {% else %}
                {% include "includes/_no_data_supplied.html" %}
            {% endif %}
        {% endwith %}

    Args:
        prose: The ``prose`` dict from the render model, or ``None``.

    Returns:
        ``True`` if any tendency entry has a non-empty ``comment`` value.

    """
    if not prose:
        return False
    tendency = prose.get("tendency") if isinstance(prose, dict) else None
    if not tendency:
        return False
    return any((entry or {}).get("comment") for entry in tendency)
