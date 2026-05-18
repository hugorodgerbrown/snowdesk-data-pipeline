"""
public/templatetags/source_attribution.py — Template tag for inline source attribution.

Exposes ``{% source_link source %}`` which renders a small inline anchor
linking to the upstream data provider (SLF for Switzerland, EUREGIO/ALBINA
for Austria and Italy).  Used inside the bulletin-page attribution sentence
rendered between the day-character callout and the day-risk-profile panel.

Only the two currently-ingested source keys are recognised.  An unknown or
missing key raises ``ValueError`` — every v4 render model carries one of the
two known keys, so an absent or unrecognised value indicates bad data and
must surface loudly rather than silently rendering empty.
"""

from django import template

register = template.Library()

# Mapping from canonical source key to display wordmark and upstream URL.
# Extend this dict (and the HTMX view's country→source mapping in
# public/views.py) when a new data source is added.
SOURCES: dict[str, dict[str, str]] = {
    "slf": {
        "wordmark": "SLF",
        "url": "https://www.slf.ch",
    },
    "euregio": {
        "wordmark": "ALBINA",
        "url": "https://avalanche.report",
    },
}


@register.inclusion_tag("public/_source_link.html")
def source_link(source: str) -> dict[str, str]:
    """
    Render the data-source inline link for the bulletin attribution row.

    Looks up ``source`` in the ``SOURCES`` registry and returns the template
    context dict required by ``public/_source_link.html``.  Raises
    ``ValueError`` for any unrecognised key so data bugs surface loudly
    rather than silently producing an empty link.

    Usage::

        {% load source_attribution %}
        {% source_link source %}

    Args:
        source: Canonical source key — ``"slf"`` or ``"euregio"``.

    Returns:
        Dict with ``wordmark`` and ``url`` keys for the link template.

    Raises:
        ValueError: If ``source`` is not a recognised key in ``SOURCES``.

    """
    if source not in SOURCES:
        raise ValueError(
            f"Unknown bulletin source {source!r}. Expected one of: {sorted(SOURCES)!r}."
        )
    return dict(SOURCES[source])
