"""
apps/public/resort_guide.py — the server half of the "Find a resort" guide.

SNOW-1231.

The welcome card's "Find a resort" runs a three-step guide over the map
(static/js/map_resort_guide.js): search opens with a few resorts to pick
from, the resort's sheet says the resort has no danger level of its own and
points at its avalanche region, and the region's sheet is the bulletin. It
teaches the one thing a skier gets wrong on their first visit: they look for
their resort's danger, and the danger belongs to the region it sits in
(docs/decisions/a-resort-has-no-avalanche-danger.md).

Two facts come from here:

- **The popular resorts** search lists before anything is typed: a fixed
  handful of CORE-tier resorts, in this order (Hugo, SNOW-1231). Every
  resort is Swiss, so placing the visitor would change little; a resort
  with no pin is left out, because the row opens the pin.
- **The guide's day.** In season the guide runs on the map's own day; out
  of season there is no bulletin to read, so it moves the map to the
  example route's day (``RouteShare.example_date``) and back again at the
  end, as the example walkthrough does.
"""

from __future__ import annotations

import datetime
import logging
from typing import TypedDict

from apps.regions.models import Resort
from apps.routes.services.example import DEFAULT_EXAMPLE_DATE

logger = logging.getLogger(__name__)

# The resorts the guide offers before anything is typed, in display order.
POPULAR_RESORT_SLUGS: tuple[str, ...] = ("verbier", "zermatt", "davos", "st-moritz")


class PopularResort(TypedDict):
    """One row of the guide's "Popular resorts" group, as JSON for the page."""

    slug: str
    name: str
    region_id: str
    region_name: str


def popular_resorts() -> list[PopularResort]:
    """Return the popular resorts that have a pin, in ``POPULAR_RESORT_SLUGS`` order.

    One query: the resorts with their regions.

    Returns:
        The rows, possibly fewer than the slugs (or none) on a database
        that lacks some of them.

    """
    found = {
        resort.slug: resort
        for resort in Resort.objects.geocoded()
        .filter(slug__in=POPULAR_RESORT_SLUGS)
        .select_related("region")
    }
    return [
        {
            "slug": resort.slug,
            "name": resort.name,
            "region_id": resort.region.region_id,
            "region_name": resort.region.name,
        }
        for slug in POPULAR_RESORT_SLUGS
        if (resort := found.get(slug)) is not None
    ]


def guide_date(
    is_offseason: bool, example_date: datetime.date | None
) -> datetime.date | None:
    """Return the day the guide moves the map to, or None to keep the map's day.

    Args:
        is_offseason: Whether today is past the season's last bulletin.
        example_date: The example route's day, when an example is set.

    Returns:
        None in season; out of season the example's day, else the default
        example day, which has a full bulletin on every environment seeded
        from the fixtures.

    """
    if not is_offseason:
        return None
    return example_date or DEFAULT_EXAMPLE_DATE
