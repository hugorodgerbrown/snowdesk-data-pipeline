"""
public/templatetags/component_library_tags.py — Template tags for the design system.

Used exclusively by the component-library page at ``/_components/`` and
its panels under ``public/templates/_components/``. Currently exposes one
tag, ``include_variant``, which renders an arbitrary partial against a
``variant.context`` dict — the trick that lets every ``kind="components"``
entry source its render content from the registry without the dispatch
template hard-coding per-component context keys.
"""

from typing import Any

from django import template
from django.template.loader import get_template

register = template.Library()


@register.simple_tag(takes_context=True)
def include_variant(
    context: template.Context, partial: str, variant: dict[str, Any]
) -> str:
    """Render ``partial`` with ``variant["context"]`` merged into the parent context.

    Django's ``{% include %}`` tag has no ``**kwargs`` form — every key
    has to be enumerated as ``key=value``. That doesn't compose for a
    library where each component partial reads a different shape of
    context. This tag closes the gap: it walks the parent context, layers
    the variant's per-render context on top, and renders the partial via
    the same template engine ``{% include %}`` would use.

    The return value is a ``SafeString`` because ``Template.render`` has
    already routed the output through the autoescaping engine — no
    ``mark_safe`` on user-supplied content is involved (the variant data
    is hand-curated in ``public/_component_fixtures.py``).

    Args:
        context: The parent ``RenderContext``, supplied by ``takes_context``.
        partial: Django template path (e.g. ``"includes/bulletin_header.html"``).
        variant: Dict with at least a ``"context"`` key mapping to the dict
            of keys the partial expects.

    Returns:
        The rendered partial as a string (already template-escaped).

    """
    rendered_template = get_template(partial)
    # Django's ``Context.flatten()`` is typed as
    # ``dict[int | str | Node, ...]`` because Context can technically be
    # keyed by anything; in practice every key is a string. Coerce to
    # ``dict[str, Any]`` so the ``Template.render`` signature is happy.
    flat_context: dict[str, Any] = {str(k): v for k, v in context.flatten().items()}
    flat_context.update(variant.get("context", {}))
    return rendered_template.render(flat_context)
