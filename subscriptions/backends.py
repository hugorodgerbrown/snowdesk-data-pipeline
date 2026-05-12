"""
subscriptions/backends.py — Custom Django authentication backend for Snowdesk.

Provides ``TokenBackend``, which verifies a signed magic-link token and
returns the matching Subscriber.  It is listed before ``ModelBackend`` in
``AUTHENTICATION_BACKENDS`` so that token-based logins use it directly;
``ModelBackend`` still handles staff password logins via the Django admin
form.

Usage — token verification (account_view / passkey flow):
  The views call ``django.contrib.auth.login(request, subscriber, backend=…)``
  directly after verifying the token themselves, so ``authenticate()`` is not
  called for the normal subscriber flow.  ``TokenBackend.get_user()`` is called
  by the session middleware on every subsequent request to reload the user from
  the session.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.contrib.auth.backends import BaseBackend

if TYPE_CHECKING:
    from django.http import HttpRequest

    from subscriptions.models import Subscriber

logger = logging.getLogger(__name__)

_BACKEND_PATH = "subscriptions.backends.TokenBackend"


class TokenBackend(BaseBackend):
    """
    Authentication backend that supports magic-link tokens and passkeys.

    The ``authenticate()`` method is intentionally minimal — the views
    perform their own token/passkey verification and call ``login()``
    directly.  ``get_user()`` is the critical method: it is called on every
    request by ``AuthenticationMiddleware`` to reconstruct the user from the
    session-stored primary key.
    """

    def authenticate(self, request: HttpRequest | None, **kwargs: Any) -> None:
        """Not used directly; views call login() after verifying credentials."""
        return None

    def get_user(self, user_id: int) -> Subscriber | None:
        """Return the Subscriber for the given primary key, or None."""
        from subscriptions.models import Subscriber

        try:
            return Subscriber.objects.get(pk=user_id)  # type: ignore[no-any-return]
        except Subscriber.DoesNotExist:
            return None
