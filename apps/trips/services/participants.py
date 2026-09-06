"""
apps/trips/services/participants.py — joining and leaving a trip (SNOW-822).

``join_trip``, ``leave_trip``, and ``roster_for`` — the one read the view and
the template share, so "who is on this trip" cannot be answered two ways.

**Both writes are idempotent.** ``(trip, user)`` is unique at the database,
so a double-tapped Join would otherwise surface an ``IntegrityError`` on a
request path; and a Leave arriving twice — a slow network, a retried
mutation — is not an error either. The service absorbs both rather than
letting the caller decide what an exception means.

**The organiser cannot leave.** They are ``created_by`` and the trip's
author, and a trip whose organiser is not on it is incoherent: nobody is
answerable for the plan, and the roster no longer contains the person whose
name is the answer to "who sent me this". ``OrganiserCannotLeave`` says so,
and the view turns it into a message pointing at delete — which is the real
exit, and which the confirmation already says removes the trip for everyone.

**The organiser cannot REMOVE anybody either**, and there is no service
function for it. Removal is a moderation surface with its own questions —
who is told, what the removed person sees, whether they can rejoin — and a
trip lasts one day. Deleting the trip is the one lever, and it is honest
about its blast radius.

The disclosure rule the roster read exists to serve is stated where it is
enforced: ``roster_names_visible_to``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from apps.trips.models import Trip, TripParticipant

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser, AnonymousUser, User

logger = logging.getLogger(__name__)


class OrganiserCannotLeave(Exception):
    """Raised when the organiser tries to leave their own trip."""


def join_trip(user: "User", trip: Trip) -> TripParticipant:
    """Put ``user`` on ``trip``'s roster, or return the row already there.

    Idempotent by ``get_or_create`` rather than by a check-then-insert: two
    taps landing in the same millisecond would both pass a check, and the
    second would hit the ``(trip, user)`` uniqueness constraint. This
    leaves the ``joined_at`` of the FIRST join in place, which is what the
    roster's order is read from.

    Takes a ``Trip`` rather than a uuid or a token because the caller has
    already resolved one — through the share token, which is the only way a
    non-participant reaches a trip at all. Re-resolving here would mean
    this function deciding who may see a trip, which is the view's
    question and is answered differently on each of its two surfaces.

    Args:
        user: The authenticated account joining.
        trip: The trip being joined.

    Returns:
        The participant row, new or pre-existing.

    """
    participant, created = TripParticipant.objects.get_or_create(trip=trip, user=user)
    if created:
        logger.info("Trip joined: user=%s trip=%s", user.pk, trip.uuid)
    return participant


def leave_trip(user: "User", trip: Trip) -> None:
    """Take ``user`` off ``trip``'s roster.

    Silent when they were not on it: a double-submitted Leave reaches here
    twice and the second is not an error.

    Args:
        user: The authenticated account leaving.
        trip: The trip being left.

    Raises:
        OrganiserCannotLeave: When ``user`` is the organiser. A trip whose
            organiser is not on it is incoherent — see this module's
            header. Deleting the trip is the exit.

    """
    if trip.created_by_id == user.pk:
        raise OrganiserCannotLeave(
            f"User {user.pk} organises trip {trip.uuid} and cannot leave it."
        )
    deleted, _ = TripParticipant.objects.filter(trip=trip, user=user).delete()
    if deleted:
        logger.info("Trip left: user=%s trip=%s", user.pk, trip.uuid)


def is_participant(user: "AbstractBaseUser | AnonymousUser", trip: Trip) -> bool:
    """Return whether ``user`` is on ``trip``'s roster.

    Answers False for an anonymous visitor without a query — they cannot be
    on any roster, and asking the database would be a query on every read
    of a public share page.

    Args:
        user: The viewer, possibly anonymous.
        trip: The trip being read.

    Returns:
        True when the viewer holds a participant row.

    """
    if not user.is_authenticated:
        return False
    # Filtered on ``user_id`` rather than ``user``: the parameter is typed
    # as the union a request's ``user`` actually is, and only the pk of it
    # is a lookup value every member of that union supplies.
    return TripParticipant.objects.filter(trip=trip, user_id=user.pk).exists()


def roster_for(trip: Trip) -> list[TripParticipant]:
    """Return every account on ``trip``, in join order, in one query.

    ``select_related("user__account")`` because every caller renders the
    person through ``TripParticipant.display_label``, which reads both —
    without it a ten-person roster is twenty-one queries. Join order
    because that is how the roster reads as the group filled up, with the
    organiser, whose row is written first, always at the top.

    Args:
        trip: The trip whose roster is wanted.

    Returns:
        The participant rows, oldest join first.

    """
    return list(TripParticipant.objects.for_trip(trip).select_related("user__account"))


def roster_names_visible_to(
    user: "AbstractBaseUser | AnonymousUser", trip: Trip
) -> bool:
    """Return whether ``user`` may see WHO is on ``trip``, not just how many.

    **The disclosure rule, stated once and enforced here:**

    * **Anyone holding the link sees the COUNT.** "Four people are going" is
      what a recipient needs to decide whether to come, and it identifies
      nobody.
    * **Only participants see the NAMES.** A trip link travels — forwarded
      out of a group chat, pasted into another one — and the people on the
      roster did not agree to be listed to whoever it reached. Joining is
      the act that puts you in the group, and it is what earns the group's
      names.
    * **The organiser is named to everyone**, regardless. They authored the
      thing and sent it; their name is already the answer to "who is this
      from", and withholding it would make the page more anonymous than the
      message that carried it.

    Args:
        user: The viewer, possibly anonymous.
        trip: The trip being read.

    Returns:
        True when the viewer may see the roster's names.

    """
    return is_participant(user, trip)


def leave_trip_by_uuid(user: "User", uuid: UUID) -> None:
    """Leave a trip named by uuid, scoped to trips the caller is ON.

    The view's entry point. Scoping the LOOKUP by participation rather than
    checking membership afterwards means a uuid the caller has nothing to
    do with raises ``Trip.DoesNotExist`` — answered 404, never 403 — so a
    probing request cannot use this endpoint to learn which trip uuids
    exist.

    Args:
        user: The authenticated account leaving.
        uuid: The Trip's uuid.

    Raises:
        Trip.DoesNotExist: When the caller is not on a trip with that uuid.
        OrganiserCannotLeave: When they organise it.

    """
    trip = Trip.objects.for_user(user).get(uuid=uuid)
    leave_trip(user, trip)


def display_label_for(user: "User") -> str:
    """Return how one account is NAMED on a trip surface.

    Their ``Account.display_name`` when they set one, and otherwise the
    LOCAL PART of their email address — never the whole address.

    The full address is what an account signs in with and what a stranger
    would need to attempt one. A trip's share link travels, so its page is
    read by people the organiser never chose; the local part identifies
    somebody to people who already know them, which is exactly the
    audience, and is not a deliverable address.

    TWO CALLERS, which is why it is here rather than on one of them:
    ``TripParticipant.display_label`` delegates to it, and SNOW-848's
    organiser attribution — the "Marta's trip" eyebrow and the "Notes from
    Marta" label — needs the same answer for ``trip.created_by``, who is
    reached without a roster row.

    Args:
        user: The account to name.

    Returns:
        A short label for this person.

    """
    # Imported here rather than at module scope: ``apps.accounts`` reaches
    # into ``apps.trips`` for account erasure, and a top-level import back
    # the other way would close that loop. The same idiom
    # ``apps.core.models.RequestLogManager.from_request`` uses.
    from apps.accounts.models import Account  # noqa: PLC0415

    email: str = user.email or ""
    try:
        name: str = user.account.display_name
    except Account.DoesNotExist:
        # A plain staff superuser has no Account profile.
        name = ""
    return name or email.split("@")[0] or "Somebody"
