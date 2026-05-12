"""
subscriptions/context_processors.py — Template context processors for subscriptions.

Adds ``nav_subscriptions`` to every template context so the nav avatar
dropdown can list the authenticated subscriber's regions without each view
having to query and pass them explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.http import HttpRequest

_NAV_SUBSCRIPTION_LIMIT = 3


def nav_subscriptions(request: HttpRequest) -> dict[str, Any]:
    """
    Inject the authenticated subscriber's subscriptions into every template.

    Returns an empty dict for unauthenticated requests.  For authenticated
    subscribers, returns up to ``_NAV_SUBSCRIPTION_LIMIT`` subscriptions
    ordered by region name so the nav dropdown can render region links.

    Args:
        request: The incoming HTTP request.

    Returns:
        ``{"nav_subscriptions": queryset}`` or ``{}``.

    """
    if not request.user.is_authenticated:
        return {}

    from subscriptions.models import Subscription

    nav_subs = (
        Subscription.objects.filter(subscriber=request.user)
        .select_related("region")
        .order_by("region__name")[:_NAV_SUBSCRIPTION_LIMIT]
    )
    return {"nav_subscriptions": nav_subs}
