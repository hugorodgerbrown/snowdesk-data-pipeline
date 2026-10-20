"""
apps/public/templatetags/resort_tags.py — a resort's facts in templates (SNOW-1161).

Thin wrappers over ``apps.public.resort_facts``, the one formatter both the
resort page's facts card and the place sheet's summary line read:

    {% load resort_tags %}
    {% resort_facts resort as facts %}
    {{ resort|resort_fact_summary }}
"""

from __future__ import annotations

from django import template

from apps.public.resort_facts import (
    ResortFact,
    resort_fact_summary as _resort_fact_summary,
    resort_facts as _resort_facts,
)
from apps.regions.models import Resort

register = template.Library()


@register.simple_tag(name="resort_facts")
def resort_facts_tag(resort: Resort) -> list[ResortFact]:
    """Return a resort's set facts, in display order.

    Args:
        resort: The resort to describe.

    Returns:
        ``apps.public.resort_facts.resort_facts(resort)``.

    """
    return _resort_facts(resort)


@register.filter(name="resort_fact_summary")
def resort_fact_summary_filter(resort: Resort) -> str:
    """Return the place sheet's one-line summary of a resort.

    Args:
        resort: The resort to summarise.

    Returns:
        ``apps.public.resort_facts.resort_fact_summary(resort)``.

    """
    return _resort_fact_summary(resort)
