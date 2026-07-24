"""
accounts/context_processors.py — Template context processors for accounts.

Adds ``nav_subscriptions`` to every template context so the nav avatar
dropdown can list the authenticated account's regions without each view
having to query and pass them explicitly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.http import HttpRequest

_NAV_SUBSCRIPTION_LIMIT = 3


def nav_subscriptions(request: HttpRequest) -> dict[str, Any]:
    """
    Inject the authenticated account's subscriptions into every template.

    Returns an empty dict for unauthenticated requests.  For authenticated
    accounts, returns up to ``_NAV_SUBSCRIPTION_LIMIT`` subscriptions
    ordered by region name so the nav dropdown can render region links.

    Args:
        request: The incoming HTTP request.

    Returns:
        ``{"nav_subscriptions": queryset}`` or ``{}``.

    """
    if not request.user.is_authenticated:
        return {}

    from accounts.models import Account, Subscription

    # Staff users (created via createsuperuser) have no Account profile.
    try:
        account = request.user.account
    except Account.DoesNotExist:
        return {}

    nav_subs = (
        Subscription.objects.filter(account=account)
        .select_related("region")
        .order_by("region__name")[:_NAV_SUBSCRIPTION_LIMIT]
    )
    return {"nav_subscriptions": nav_subs}
