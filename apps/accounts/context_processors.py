"""
apps/accounts/context_processors.py — Template context processors for accounts.

Adds ``nav_subscriptions`` to every template context so the nav avatar
dropdown can list the authenticated account's regions without each view
having to query and pass them explicitly.

Adds ``PWA_USER_ID`` so ``base.html`` can bake the signed-in user's public
identifier into a ``<meta>`` tag without reaching for ``request.user``
itself (SNOW-549).

Adds ``routes_visible`` so the same dropdown can hide its Routes entry when
the ``routes`` waffle flag is inactive (SNOW-668).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import waffle
from django.utils.functional import SimpleLazyObject

from apps.accounts.identity import request_identity

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

    from apps.accounts.models import Account, Subscription

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


def routes_visible(request: HttpRequest) -> dict[str, Any]:
    """
    Inject the ``routes`` waffle flag's state into every template.

    The nav dropdown's Routes entry (SNOW-668) points at ``accounts:routes``,
    which answers **404** when the flag is inactive — so the entry has to
    disappear with the flag or the menu offers a broken destination.

    This has to be a context processor rather than per-view context because
    ``includes/nav.html`` renders on *every* page, including surfaces that
    build no context of their own (``render_to_string("includes/nav.html",
    {}, request=request)`` in ``tests/public/test_nav_partial.py``). A
    per-view key would leave the entry silently absent everywhere it was not
    passed.

    The name matches the key ``apps.public.views`` already passes for the
    map's routes roundel, deliberately: the template reads the same on both
    surfaces, and a view-supplied value still wins because ``render()``
    pushes the view's dict on top of the processors'.

    LAZY, and that is not a micro-optimisation. A context processor runs on
    every template render in the project, and ``waffle.flag_is_active``
    costs a query whenever the flag cache is cold — which showed up
    immediately as ``tests/public/test_map_api.py``'s query-count budget
    failing on an anonymous JSON-ish fragment that renders no nav at all.
    ``SimpleLazyObject`` defers the lookup to the point the template reads
    the name, so the only requests that pay are the ones that render the
    signed-in menu.

    Args:
        request: The incoming HTTP request.

    Returns:
        ``{"routes_visible": bool}`` — the bool resolved on first read.

    """
    return {
        "routes_visible": SimpleLazyObject(
            lambda: waffle.flag_is_active(request, "routes")
        )
    }


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
