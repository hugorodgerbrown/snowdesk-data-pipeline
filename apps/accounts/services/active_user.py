"""
apps/accounts/services/active_user.py — who may hold a token-path session.

Provides ``user_may_sign_in``, the one predicate every token-path entry point
asks before it signs a person in or keeps them signed in: the magic-link,
verification, password-reset and email-change views, the passkey sign-in
endpoint, and ``TokenBackend.get_user`` (which reloads the session's user on
every request).

``User.is_active`` is the account kill switch (SNOW-1167). Before this
module existed, setting it to ``False`` locked nobody out: a session made by
an emailed link or a passkey kept working, and a valid link minted a fresh
one, because none of those paths looked at the flag. Putting the check
behind one function keeps the rule identical across all of them.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


def user_may_sign_in(user: User | None) -> bool:
    """
    Return whether ``user`` may sign in, or stay signed in, by a token path.

    Args:
        user: The resolved user, or ``None`` when no user was found.

    Returns:
        ``True`` only for an existing user whose ``is_active`` is set.

    """
    return user is not None and user.is_active
