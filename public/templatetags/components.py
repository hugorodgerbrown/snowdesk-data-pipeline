"""
public/templatetags/components.py — Reusable component building blocks.

Registers three template abstractions that replace copy-pasted markup:

* ``{% card padding="p-6" center=False extra="" %}…{% endcard %}``
  Block tag — wraps arbitrary inner markup in the canonical card chrome
  (``bg-card rounded-card border border-text-3/10 shadow-sm``).  Callers
  control padding, centring and any extra utility classes via kwargs.

* ``button_classes`` — simple tag that returns the Tailwind class string for
  a button given its ``variant``, ``size`` and ``full_width`` flag.  Used
  internally by ``_button.html`` so the class logic lives in one place.

* ``card_classes`` — simple tag that returns the chrome class string for a
  card given ``padding``, ``center`` and ``extra``.  Shared between the
  ``_card.html`` showcase partial and the ``card`` block tag so both always
  produce identical output.
"""

from __future__ import annotations

from django import template
from django.template import Context, Library, Node, TemplateSyntaxError
from django.utils.html import format_html

register: Library = template.Library()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BUTTON_BASE = "text-sm font-medium rounded-tag"

_BUTTON_VARIANT_CLASSES: dict[str, str] = {
    "primary": ("bg-text-1 text-card hover:opacity-90 transition-opacity"),
    "secondary": (
        "bg-card text-text-1 border border-text-1 hover:opacity-90 transition-opacity"
    ),
    "ghost": (
        "text-text-2 border border-text-3/30"
        " hover:text-text-1 hover:border-text-2 transition-colors"
    ),
}

_BUTTON_SIZE_CLASSES: dict[str, str] = {
    "standard": "px-5 py-2.5",
    "compact": "px-4 py-2",
}


def _button_chrome_classes(
    variant: str,
    size: str,
    full_width: bool,
    is_anchor: bool,
) -> str:
    """Return the full Tailwind class string for a button element.

    Args:
        variant: One of ``"primary"``, ``"secondary"``, ``"ghost"``.
        size: One of ``"standard"``, ``"compact"``.
        full_width: When ``True``, append ``w-full`` to the class string.
        is_anchor: When ``True``, append ``inline-block`` (required so an
            ``<a>`` tag takes up its own block in flow layout while still
            behaving as an inline element — matching the pre-migration
            markup that explicitly set ``inline-block`` on all anchor CTAs).

    Returns:
        A space-joined class string ready to emit into a ``class=""``
        attribute.  The output is safe to use without further escaping
        because it contains only ASCII alphanumeric chars and Tailwind
        utility class punctuation.

    """
    variant_cls = _BUTTON_VARIANT_CLASSES.get(
        variant, _BUTTON_VARIANT_CLASSES["primary"]
    )
    size_cls = _BUTTON_SIZE_CLASSES.get(size, _BUTTON_SIZE_CLASSES["standard"])

    parts: list[str] = [_BUTTON_BASE, variant_cls, size_cls]
    if full_width:
        parts.append("w-full")
    if is_anchor:
        parts.append("inline-block")
    return " ".join(parts)


def _card_chrome_classes(padding: str, center: bool, extra: str) -> str:
    """Return the Tailwind class string for the card chrome wrapper.

    Called by both the ``card_classes`` simple tag and the ``CardNode``
    block-tag render method so the two consumers can never drift apart.

    Args:
        padding: Tailwind padding utility string (e.g. ``"p-6"``).  Passed
            straight through — any valid Tailwind padding value is accepted.
        center: When ``True``, append ``text-center`` to the class string.
        extra: Any additional utility classes to append verbatim.

    Returns:
        A space-joined class string ready to emit into a ``class=""``
        attribute.

    """
    parts: list[str] = [
        "bg-card",
        "rounded-card",
        "border",
        "border-text-3/10",
        "shadow-sm",
        padding,
    ]
    if center:
        parts.append("text-center")
    if extra:
        parts.append(extra)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Simple tags
# ---------------------------------------------------------------------------


@register.simple_tag
def button_classes(
    variant: str = "primary",
    size: str = "standard",
    full_width: bool = False,
    is_anchor: bool = False,
) -> str:
    """Return the Tailwind class string for a button.

    Used by ``_button.html`` to keep the class logic out of the template.

    Args:
        variant: Visual style — ``"primary"`` (default), ``"secondary"``,
            or ``"ghost"``.
        size: Padding scale — ``"standard"`` (default, ``px-5 py-2.5``) or
            ``"compact"`` (``px-4 py-2``).
        full_width: When ``True``, appends ``w-full``.
        is_anchor: When ``True``, appends ``inline-block`` (for ``<a>``
            elements that need to behave as block-level anchors).

    Returns:
        Space-joined Tailwind class string.

    """
    return _button_chrome_classes(variant, size, bool(full_width), bool(is_anchor))


@register.simple_tag
def card_classes(
    padding: str = "p-6",
    center: bool = False,
    extra: str = "",
) -> str:
    """Return the Tailwind class string for the card chrome wrapper.

    Used by ``_card.html`` so the library showcase and call sites that
    use the ``{% card %}`` block tag always see identical chrome.

    Args:
        padding: Tailwind padding utility string (e.g. ``"p-6"``).
        center: When ``True``, appends ``text-center``.
        extra: Any extra Tailwind utilities to append verbatim.

    Returns:
        Space-joined Tailwind class string.

    """
    return _card_chrome_classes(padding, bool(center), extra)


# ---------------------------------------------------------------------------
# Block tag: {% card %}…{% endcard %}
# ---------------------------------------------------------------------------


class CardNode(Node):
    """Template node that wraps inner content in the card chrome ``<div>``.

    Parsed by :func:`do_card`.  Accepts keyword arguments for ``padding``,
    ``center``, and ``extra`` — each resolved as a filter expression so
    template variables work (e.g. ``{% card padding=padding_var %}``).
    """

    def __init__(
        self,
        nodelist: template.NodeList,
        padding_expr: template.base.FilterExpression,
        center_expr: template.base.FilterExpression,
        extra_expr: template.base.FilterExpression,
    ) -> None:
        """Initialise the node with its inner nodelist and kwarg expressions.

        Args:
            nodelist: The inner template nodes between ``{% card %}`` and
                ``{% endcard %}``.
            padding_expr: Compiled filter expression for the ``padding``
                kwarg (default resolved to ``"p-6"``).
            center_expr: Compiled filter expression for the ``center``
                kwarg (default resolved to ``False``).
            extra_expr: Compiled filter expression for the ``extra``
                kwarg (default resolved to ``""``).

        """
        self.nodelist = nodelist
        self.padding_expr = padding_expr
        self.center_expr = center_expr
        self.extra_expr = extra_expr

    def render(self, context: Context) -> str:
        """Render the card wrapper with inner content.

        Args:
            context: The current template rendering context.

        Returns:
            HTML string: a ``<div>`` with the card chrome class wrapping
            the rendered inner nodelist.

        """
        padding = self.padding_expr.resolve(context) or "p-6"
        center = self.center_expr.resolve(context)
        extra = self.extra_expr.resolve(context) or ""

        css_classes = _card_chrome_classes(str(padding), bool(center), str(extra))
        inner = self.nodelist.render(context)
        # ``inner`` is a ``SafeString`` returned by ``NodeList.render()``;
        # ``format_html`` passes safe strings through without re-escaping while
        # still escaping ``css_classes`` defensively.
        return format_html('<div class="{}">{}</div>', css_classes, inner)


@register.tag("card")
def do_card(parser: template.base.Parser, token: template.base.Token) -> CardNode:
    """Parse the ``{% card %}`` block tag.

    Accepted kwargs (all optional):

    * ``padding`` — Tailwind padding string, default ``"p-6"``.
    * ``center`` — boolean, default ``False``.
    * ``extra`` — extra Tailwind utilities, default ``""``.

    Each value is compiled as a filter expression so template variables
    can be passed (e.g. ``{% card padding=padding_var %}``).

    Args:
        parser: The Django template parser.
        token: The opening ``{% card … %}`` token.

    Returns:
        A :class:`CardNode` instance.

    Raises:
        TemplateSyntaxError: If an unrecognised kwarg is supplied or if
            the syntax of a kwarg is malformed.

    """
    bits = token.split_contents()
    tag_name = bits[0]

    # Defaults
    _DEFAULTS: dict[str, str] = {
        "padding": '"p-6"',
        "center": "False",
        "extra": '""',
    }
    kwargs: dict[str, str] = dict(_DEFAULTS)

    for bit in bits[1:]:
        if "=" not in bit:
            raise TemplateSyntaxError(
                f"'{tag_name}' tag kwargs must use key=value syntax, got: {bit!r}"
            )
        key, _, raw_value = bit.partition("=")
        if key not in kwargs:
            raise TemplateSyntaxError(
                f"'{tag_name}' received unknown kwarg {key!r}. "
                f"Allowed: {', '.join(_DEFAULTS)}"
            )
        kwargs[key] = raw_value

    nodelist = parser.parse(("endcard",))
    parser.delete_first_token()

    return CardNode(
        nodelist=nodelist,
        padding_expr=parser.compile_filter(kwargs["padding"]),
        center_expr=parser.compile_filter(kwargs["center"]),
        extra_expr=parser.compile_filter(kwargs["extra"]),
    )
