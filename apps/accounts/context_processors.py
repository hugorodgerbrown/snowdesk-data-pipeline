"""
apps/accounts/context_processors.py — Template context processors for accounts.

Adds ``PWA_USER_ID`` so ``base.html`` can bake the signed-in user's public
identifier into a ``<meta>`` tag without reaching for ``request.user``
itself (SNOW-549).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from apps.accounts.identity import request_identity

if TYPE_CHECKING:
    from django.http import HttpRequest


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
