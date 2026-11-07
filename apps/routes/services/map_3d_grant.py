"""
apps/routes/services/map_3d_grant.py — a route share's 3D link grants 3D (SNOW-1469).

The map's 3D view is for signed-in readers inside the ``map_3d`` flag
(SNOW-1451). A route share opened through its 3D link,
``/routes/s/<token>/3d/``, extends that view to whoever followed it,
signed in or not, for the rest of their session:

- ``seat_map_3d_grant`` — the 3D follow records the token under
  ``GRANT_SESSION_KEY``. The newest grant replaces any older one.
- ``map_3d_granted`` — ``apps.public.map_shared.map_3d_view_enabled``
  asks whether the session's grant still holds. It is checked again on
  every read: the share must still be claimable (live, and not the example
  route every visitor is offered, which would otherwise open 3D to
  everyone), and its sharer must still be inside ``map_3d``. Revoking the
  share, letting it expire or taking the sharer out of the flag ends the
  grant on the visitor's next page.

The grant is its own key rather than a reading of the pending-token list
(``PENDING_SESSION_KEY``): claiming or dismissing a pending route drops its
token from that list, and the 3D the visitor was given should not vanish
with it. The grant shows nothing the share did not: the visitor still sees
only the routes their own session followed. Documented with the session's
other contents in docs/accounts.md.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import waffle

from apps.routes.models import RouteShare

if TYPE_CHECKING:
    from django.contrib.sessions.backends.base import SessionBase

logger = logging.getLogger(__name__)

# The session key the grant lives under: the token of the route share whose
# 3D link this browser followed last.
GRANT_SESSION_KEY = "map_3d_grant"

# The flag the sharer must still be inside for their share to grant 3D.
MAP_3D_FLAG = "map_3d"


def seat_map_3d_grant(session: "SessionBase", token: str) -> None:
    """Record that this browser followed a route share's 3D link.

    The caller has already checked that the share is live and that the
    request is a real top-level navigation; whether the share still grants
    3D is ``map_3d_granted``'s question, asked on every read.

    Args:
        session: The request's session.
        token: The followed share's token.

    """
    session[GRANT_SESSION_KEY] = token
    session.modified = True


def map_3d_granted(session: "SessionBase") -> bool:
    """Say whether the session's 3D grant still holds.

    No grant costs no query. Otherwise one query for the share and its
    sharer, and waffle's own for the flag.

    Args:
        session: The request's session.

    Returns:
        True when the session holds the token of a claimable share whose
        sharer is inside ``map_3d``.

    """
    token = session.get(GRANT_SESSION_KEY)
    # A cookie outlives a deploy, so anything but a non-empty string is a
    # shape this code never wrote and reads as no grant.
    if not isinstance(token, str) or not token:
        return False
    share = (
        RouteShare.objects.claimable()
        .select_related("created_by")
        .filter(token=token)
        .first()
    )
    if share is None:
        return False
    # ``get`` is waffle's cached read; a flag with no row comes back unsaved,
    # and an unsaved flag has no user or group lists to ask.
    flag = waffle.get_waffle_flag_model().get(MAP_3D_FLAG)
    if flag.pk is None:
        return False
    granted = bool(flag.is_active_for_user(share.created_by))
    logger.debug("3D grant for share %s: %s", share.pk, granted)
    return granted
