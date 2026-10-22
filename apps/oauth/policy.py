"""
apps/oauth/policy.py — who may connect an MCP client to Snowdesk.

``MCP_AUTH["CAN_CONNECT"]`` names :func:`verified_account`. mcp-auth runs it
on the consent page and again on every MCP call, so an account that is
deactivated, or loses its verified address, cuts off its connected apps at
once (SNOW-1193).
"""

from __future__ import annotations

from typing import Any

from apps.accounts.models import user_is_verified


def verified_account(user: Any) -> bool:
    """Return True for an active user with a verified Snowdesk account.

    The same rule Snowdesk holds every verified-only action to
    (``user_is_verified``): an account whose email address has been
    confirmed. A staff user with no ``Account`` row is refused like anyone
    else — a token is a reader's credential, not an admin's.

    Args:
        user: The signed-in user, or the user a token belongs to.

    Returns:
        Whether the user may connect.

    """
    return bool(user.is_active and user_is_verified(user))
