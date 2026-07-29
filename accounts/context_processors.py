"""
accounts/context_processors.py — Template context processors for accounts.

Adds ``nav_subscriptions`` to every template context so the nav avatar
dropdown can list the authenticated account's regions without each view
having to query and pass them explicitly.

Adds ``PWA_USER_ID`` so ``base.html`` can bake the signed-in user's public
identifier into a ``<meta>`` tag without reaching for ``request.user``
itself (SNOW-549).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from accounts.identity import request_identity

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


def pwa_user_identity(request: HttpRequest) -> dict[str, Any]:
    """
    Inject the signed-in user's public identifier into every template.

    ``base.html`` renders this into ``<meta name="pwa-user-id">``, which the
    PWA reads as the mutation queue's principal — the binding SNOW-462
    added so queued mutations are discarded on account change
    (``static/js/mutation_queue.js``, ``db.js``, ``sw.js``,
    ``map_overlay_offline_cache.js``). Those consumers treat the value as
    an opaque string and only compare it for equality, so switching it from
    the sequential ``auth.User`` PK to ``Account.uuid`` (SNOW-549) needed no
    JS change.

    Empty for anonymous requests — ``db.js`` treats an empty tag as null.

    Args:
        request: The incoming HTTP request.

    Returns:
        ``{"PWA_USER_ID": str}`` — the account uuid, or ``""``.

    """
    return {"PWA_USER_ID": request_identity(request)}
