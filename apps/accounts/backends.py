"""
apps/accounts/backends.py — Custom Django authentication backend for Snowdesk.

Provides ``TokenBackend``, which is listed before ``ModelBackend`` in
``AUTHENTICATION_BACKENDS``.  It is used when views call
``django.contrib.auth.login(request, user, backend=…)`` directly after
verifying a magic-link token or a WebAuthn passkey assertion.

``TokenBackend.get_user()`` is called by ``AuthenticationMiddleware`` on every
subsequent request to reload the User from the session.  It returns a plain
``auth.User`` instance; the account profile (if any) is accessed via
``user.account`` (the OneToOneField reverse accessor).

A user whose ``is_active`` is ``False`` is not returned (SNOW-1167): the
policy lives in ``apps.accounts.services.active_user.user_may_sign_in``, so
deactivating an account ends its token-backend session on the next request
— the person is simply anonymous from then on.

``ModelBackend`` still handles staff password logins via the Django admin form.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.models import User

from .services.active_user import user_may_sign_in

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

_BACKEND_PATH = "apps.accounts.backends.TokenBackend"


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
        """
        Return the active User for the given primary key, or None.

        An inactive user is answered as ``None`` — the same as an unknown pk —
        so a session belonging to a deactivated account stops authenticating.
        """
        UserModel = get_user_model()  # noqa: N806 — conventional upper-case alias
        try:
            user = UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
        return user if user_may_sign_in(user) else None
