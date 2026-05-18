"""
public/templatetags/source_pill.py — Template tag for the data-source wordmark pill.

Exposes ``{% source_pill source %}`` which renders a small inline anchor
linking to the upstream data provider (SLF for Switzerland, EUREGIO/ALBINA
for Austria and Italy).  The tag is designed to sit in the top-right corner
of the bulletin header's inner flex row.

Only the two currently-ingested source keys are recognised.  An unknown or
missing key raises ``ValueError`` — every v4 render model carries one of the
two known keys, so an absent or unrecognised value indicates bad data and
must surface loudly rather than silently rendering empty.

The ``bulletin_header.html`` template guards the tag call with
``{% if source %}`` so the exception path is only reached on genuine data
bugs, not on the normal error-state / no-bulletin paths that suppress the
source attribute entirely.
"""

from django import template

register = template.Library()

# Mapping from canonical source key to display wordmark and upstream URL.
# Extend this dict (and update the HTMX view's country→source mapping) when
# a new data source is added.
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


@register.inclusion_tag("public/_source_pill.html")
def source_pill(source: str) -> dict[str, str]:
    """
    Render the data-source wordmark pill for the bulletin header.

    Looks up ``source`` in the ``SOURCES`` registry and returns the template
    context dict required by ``public/_source_pill.html``.  Raises
    ``ValueError`` for any unrecognised key so data bugs surface loudly
    rather than silently producing an empty pill.

    Usage::

        {% load source_pill %}
        {% source_pill source %}

    Args:
        source: Canonical source key — ``"slf"`` or ``"euregio"``.

    Returns:
        Dict with ``wordmark`` and ``url`` keys for the pill template.

    Raises:
        ValueError: If ``source`` is not a recognised key in ``SOURCES``.

    """
    if source not in SOURCES:
        raise ValueError(
            f"Unknown bulletin source {source!r}. Expected one of: {sorted(SOURCES)!r}."
        )
    return dict(SOURCES[source])
