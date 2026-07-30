"""
apps/public/templatetags/component_library_tags.py — Tags for the design system.

Used exclusively by the component-library page at ``/_components/`` and
its panels under ``apps/public/templates/_components/``. Exposes:

* ``include_variant`` — render an arbitrary partial against a
  ``variant.context`` dict so each ``kind="components"`` entry can source
  its render content from the registry without the dispatch template
  hard-coding per-component context keys.  Supports a per-variant
  ``"partial"`` key that overrides the category's default template path —
  used by the ``subscribe-outcomes`` entry to display five different
  outcome templates under one sidebar entry.
* ``contains_slug`` — filter used by the sidebar to decide which
  ``<details>`` group is open on first paint (the one that owns the
  active category).
"""

from typing import Any

from django import template
from django.template.loader import get_template

from apps.public.design_tokens import LibraryGroup

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

    If ``variant`` carries a ``"partial"`` key, that path overrides the
    ``partial`` argument supplied by the category registry.  This lets a
    single category entry (e.g. ``subscribe-outcomes``) render a
    *different* template for each variant without splitting into multiple
    sidebar entries.

    The return value is a ``SafeString`` because ``Template.render`` has
    already routed the output through the autoescaping engine — no
    ``mark_safe`` on user-supplied content is involved (the variant data
    is hand-curated in ``apps/public/_component_fixtures.py``).

    When a request is available on the parent context (the normal case for
    Django views), the partial is rendered via the backend ``Template.render``
    API with the request passed explicitly.  The backend builds a
    ``RequestContext`` internally, so CSRF tokens and other request-aware
    processors work correctly inside the partial.  When no request is
    available the dict-only path is used as a fallback.

    Args:
        context: The parent ``RenderContext``, supplied by ``takes_context``.
        partial: Django template path (e.g. ``"includes/bulletin_header.html"``).
            Used as the default; overridden by ``variant["partial"]`` when present.
        variant: Dict with at least a ``"context"`` key mapping to the dict
            of keys the partial expects.  May also carry a ``"partial"`` key
            to override the template path for this variant only.

    Returns:
        The rendered partial as a string (already template-escaped).

    """
    # Allow per-variant partial override (used by subscribe-outcomes).
    effective_partial = variant.get("partial", partial)
    rendered_template = get_template(effective_partial)
    # Django's ``Context.flatten()`` is typed as
    # ``dict[int | str | Node, ...]`` because Context can technically be
    # keyed by anything; in practice every key is a string. Coerce to
    # ``dict[str, Any]`` so the ``Template.render`` signature is happy.
    flat_context: dict[str, Any] = {str(k): v for k, v in context.flatten().items()}
    flat_context.update(variant.get("context", {}))
    # ``get_template`` returns a backend-level ``Template`` whose ``.render``
    # accepts ``(context: dict, request: HttpRequest | None)``.  Passing the
    # request causes the backend to build a ``RequestContext`` internally, so
    # CSRF and other request-aware context processors work inside the partial.
    # ``RequestContext`` stores the request as an attribute (.request), not as
    # a dict key accessible via ``context.get()`` — use ``getattr`` to extract
    # it safely.
    request = getattr(context, "request", None)
    return rendered_template.render(flat_context, request)


@register.filter
def contains_slug(group: LibraryGroup, slug: str) -> bool:
    """Return True if any category in ``group`` matches ``slug``.

    Used by the sidebar to decide which ``<details>`` group renders open
    on first paint — the one that owns the currently-active panel — so
    the user lands with their context expanded and the rest collapsed.
    """
    return any(category.slug == slug for category in group.categories)
