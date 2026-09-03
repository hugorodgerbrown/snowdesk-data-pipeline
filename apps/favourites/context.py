"""
apps/favourites/context.py — the region pin control's template context (SNOW-802).

One function, imported by two renderers of the same partial: the
``favourite_region_toggle`` view, which hands the control back in its new
state, and ``apps.public.api.region_summary``, which renders it into the
region tooltip the panel and the popup both inject. Kept out of
``apps.favourites.views`` so ``apps.public.api`` can import it without
pulling the view module (which imports ``apps.public.views``) into the API
module's import graph.
"""

from __future__ import annotations

from typing import Any

from django.urls import reverse

from apps.favourites.models import Favourite
from apps.regions.models import MicroRegion


def region_pin_context(
    region: MicroRegion, user: Any, *, pinned: bool | None = None
) -> dict[str, Any]:
    """Build the context ``favourites/partials/_region_pin_button.html`` renders from.

    Args:
        region: The region the control is for.
        user: The requesting user; may be anonymous.
        pinned: The known pin state, or ``None`` to look it up (one query,
            signed-in users only).

    Returns:
        The template context.

    """
    can_pin = bool(getattr(user, "is_authenticated", False))
    if can_pin and pinned is None:
        pinned = (
            Favourite.objects.for_user(user)
            .region_pins()
            .filter(region=region)
            .exists()
        )
    return {
        "region": region,
        "region_can_pin": can_pin,
        "region_pinned": bool(pinned),
        "region_pin_toggle_url": reverse(
            "favourites:region_toggle", kwargs={"region_id": region.region_id}
        ),
        "signin_url": reverse("accounts:sign_in"),
    }
