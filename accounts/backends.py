"""
accounts/backends.py — Custom Django authentication backend for Snowdesk.

Provides ``TokenBackend``, which is listed before ``ModelBackend`` in
``AUTHENTICATION_BACKENDS``.  It is used when views call
``django.contrib.auth.login(request, user, backend=…)`` directly after
verifying a magic-link token or a WebAuthn passkey assertion.

``TokenBackend.get_user()`` is called by ``AuthenticationMiddleware`` on every
subsequent request to reload the User from the session.  It returns a plain
``auth.User`` instance; the account profile (if any) is accessed via
``user.account`` (the OneToOneField reverse accessor).

``ModelBackend`` still handles staff password logins via the Django admin form.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

_BACKEND_PATH = "accounts.backends.TokenBackend"


class TokenBackend(BaseBackend):
    """
    Authentication backend that supports magic-link tokens and passkeys.

    The ``authenticate()`` method is intentionally minimal — the views
    perform their own token/passkey verification and call ``login()``
    directly.  ``get_user()`` is the critical method: it is called on every
    request by ``AuthenticationMiddleware`` to reconstruct the User from the
    session-stored primary key.
    """

    def authenticate(self, request: HttpRequest | None, **kwargs: Any) -> None:
        """Not used directly; views call login() after verifying credentials."""
        return None

    def get_user(self, user_id: int) -> User | None:
        """Return the User for the given primary key, or None."""
        UserModel = get_user_model()  # noqa: N806 — conventional upper-case alias
        try:
            return UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
