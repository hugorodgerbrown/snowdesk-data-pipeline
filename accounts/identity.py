"""
accounts/identity.py — The public-facing identifier for a signed-in user.

Every surface that exposes "who is this user?" outside the server —
PostHog's ``distinct_id``, the ``pwa-user-id`` meta tag that the PWA
mutation queue uses as its principal — resolves through this module
(SNOW-549).

The identifier is ``Account.uuid``. It replaces ``str(request.user.pk)``,
which was Django's sequential ``auth.User`` primary key: a small integer
that leaks the size and growth rate of the user base, is trivially
enumerable, and made spoofing a client-supplied ``user_id`` a one-guess
exercise.

``Account`` inherits ``BaseModel``, which already carries a ``unique``,
``editable=False`` ``uuid`` field — so this needed no new field, no
migration, and no backfill. A stored identifier was chosen over a derived
``HMAC-SHA256(pk, SECRET_KEY)`` because it stays reversible in the admin
(support can map a PostHog ``distinct_id`` back to a user) and survives a
``SECRET_KEY`` rotation.

The house style here follows ``core.idempotency._principal``: small
module-level functions that read defensively off whatever they are handed
and return ``""`` rather than raising, because every caller sits on a path
where analytics or telemetry must never break the request.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)


def user_identity(user: Any) -> str:
    """Return the public identifier for ``user``, or ``""`` if there isn't one.

    Args:
        user: A ``User`` instance, ``AnonymousUser``, or ``None``.

    Returns:
        ``str(user.account.uuid)`` for an authenticated user with an
        ``Account`` row, otherwise ``""``.

    Anonymous users have no identity here — callers fall back to a
    session-scoped value of their own.

    An authenticated user with no ``Account`` row also resolves to ``""``.
    Migration ``accounts.0009_collapse_subscriber_add_and_backfill``
    backfilled every pre-existing user and the sign-up views
    ``get_or_create``, but superusers created via ``createsuperuser``
    genuinely have no profile — the same case
    ``accounts.context_processors.nav_subscriptions`` already guards. This
    function never creates the row: it runs on read paths, and a GET must
    not have write side effects.

    """
    if user is None or not getattr(user, "is_authenticated", False):
        return ""

    # RelatedObjectDoesNotExist subclasses AttributeError, so getattr's
    # default covers the missing-profile case without a try/except.
    account = getattr(user, "account", None)
    if account is None:
        logger.debug("No Account row for user pk=%s — identity is empty", user.pk)
        return ""

    return str(account.uuid)


def request_identity(request: HttpRequest) -> str:
    """Return the public identifier for ``request``'s user, or ``""``.

    Defensive against bare ``HttpRequest`` objects, which have no ``user``
    attribute at all — the same guard ``core.idempotency._principal``
    carries for requests that reach middleware before
    ``AuthenticationMiddleware`` has run.

    Args:
        request: The incoming request.

    Returns:
        The account uuid as a string, or ``""`` for anonymous.

    """
    return user_identity(getattr(request, "user", None))
