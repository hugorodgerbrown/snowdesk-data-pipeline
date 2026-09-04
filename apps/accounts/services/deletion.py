"""
apps/accounts/services/deletion.py — the one account-erasure path.

Provides ``erase_account``: delete a person's ``auth.User``, their
``Account`` profile, and the two classes of row a plain ``CASCADE`` cannot
reach.

The Privacy Policy promises that deleting an account removes the account,
the subscriptions, the saved items and the request records "in one
operation and without delay". Two things stand between the ``CASCADE`` and
that promise, and both need Python to run:

* **Request records written before the account existed.** Sign-up happens
  anonymously, so that ``RequestLog`` row has ``account=None``; the link
  runs the other way, from ``Account.acquisition_request`` and
  ``Subscription.subscribed_via``. Both are ``SET_NULL``, so the cascade
  drops the pointer and strands the row — still holding the IP address,
  city, coordinates, user agent and session key captured at sign-up
  (SNOW-774).
* **The anonymous ``Location`` each favourite minted.** ``Favourite.user``
  is ``CASCADE`` and ``Favourite.location`` is ``PROTECT``, so the cascade
  deletes the favourites in bulk and leaves the locations behind, holding
  real coordinates and elevation and referenced by nothing.
* **The anonymous ``Location`` each TRIP minted** (SNOW-819). Exactly the
  same shape: ``Trip.created_by`` is ``CASCADE`` and ``Trip.meeting_point``
  is ``PROTECT``, so the meeting point of every trip this account organised
  would outlive the account, holding the real coordinates of a real place
  somebody agreed to meet at.

This lives in a service rather than in ``delete_account`` because the view
is not the only caller: deleting an ``Account`` through the Django admin
must erase exactly as much, and did not while the logic sat in the view.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import transaction

from apps.core.models import RequestLog
from apps.favourites.services import delete_favourites_for_user
from apps.trips.services.trips import delete_trips_for_user

if TYPE_CHECKING:
    from django.contrib.auth.models import User as UserType

    from apps.accounts.models import Account

logger = logging.getLogger(__name__)


def _referenced_request_log_ids(account: "Account | None") -> list[int]:
    """Return the ids of RequestLog rows this account points at (SNOW-774).

    These are the rows a ``CASCADE`` on ``RequestLog.account`` cannot reach:
    the sign-up request happens before the account exists, so that row is
    written anonymously with ``account=None`` and the association is
    recorded the other way round, by ``Account.acquisition_request`` and
    ``Subscription.subscribed_via`` pointing *at* the log row.

    Collect them before the delete, not after: once the account and its
    subscriptions are gone there is nothing left to read the FKs from.

    Args:
        account: The account being deleted, or None for an authenticated
            user with no Account profile (a staff superuser), which owns no
            such rows.

    Returns:
        Distinct RequestLog primary keys, empty when there are none.

    """
    if account is None:
        return []

    ids = {
        pk
        for pk in account.subscriptions.values_list("subscribed_via_id", flat=True)
        if pk is not None
    }
    if account.acquisition_request_id is not None:
        ids.add(account.acquisition_request_id)
    return list(ids)


def erase_account(user: "UserType", account: "Account | None") -> None:
    """Delete a user, their account, and everything erasure promises.

    Runs in this order, and the order is load-bearing:

    1. Collect the referenced ``RequestLog`` ids while the FKs pointing at
       them still exist.
    2. Delete the favourites one row at a time, so each minted anonymous
       ``Location`` is swept as its last referent goes.
    3. Delete the organised trips the same way and for the same reason —
       a trip's meeting point is a minted anonymous ``Location`` too.
    4. Delete the user — the ``CASCADE`` takes the account, subscriptions,
       signed-in request rows, observations, routes, trip participations,
       passkeys and clicks.
    5. Delete the collected request rows, now that nothing points at them.

    All five commit together or none of them do. Erasure that half succeeds
    is the worst of the three outcomes: the account is gone, so the person
    has no way back in to ask again, while the rows carrying their IP
    address and coordinates are still there and no longer attached to
    anything that would lead you to them.

    Args:
        user: The ``auth.User`` being erased.
        account: Their ``Account`` profile, or None for an authenticated
            user that has none (a staff superuser).

    """
    orphan_log_ids = _referenced_request_log_ids(account)
    # ``Model.delete()`` clears the in-memory pk, so read both before the
    # delete or the log line says "None".
    user_pk = user.pk
    account_pk = account.pk if account is not None else None

    with transaction.atomic():
        delete_favourites_for_user(user)
        delete_trips_for_user(user)
        user.delete()
        if orphan_log_ids:
            RequestLog.objects.filter(pk__in=orphan_log_ids).delete()

    logger.info("Erased user pk=%s (account pk=%s)", user_pk, account_pk)
