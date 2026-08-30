"""
apps/routes/services/shares.py — Business logic for sharing a Route (SNOW-764).

Three concerns, in the order a share travels through them:

- ``create_route_share`` — the owner mints a link for one of their routes.
- ``pending_tokens`` / ``add_pending_token`` / ``drop_pending_token`` /
  ``pending_shares`` — the recipient has followed the link but has not
  claimed it yet, so the token rides in ``request.session`` until they do.
- ``claim_route_share`` — the recipient takes a COPY of the route onto
  their own account.

**A claim copies, it never transfers.** The sharer keeps their row
untouched and the claimer gets a new one; see ``RouteShare``'s own
docstring for why handing a friend a track must not take it off the
sharer's map.

**Why the pending list lives in the session.** A recipient who is not
signed in has no account to hang a pending claim off, and the sign-in
round trip is exactly where a claim would otherwise be lost: the link
lands on ``/``, the visitor signs in, comes back, and the token that
brought them is gone from the URL. A session survives that round trip
(Django cycles the session key on login but carries the data across), it
needs no schema, and it expires by itself. The cost is that the pending
list is per-browser rather than per-account, which is the correct scope
anyway — following a link is a thing a browser did, not a thing an account
did. Full argument:
``docs/decisions/route-share-pending-claim-in-session.md``.

The cap on a claimed copy is ``settings.ROUTES_MAX_PER_USER``, enforced
through ``routes.py``'s own ``_assert_under_cap`` / ``_locked_cap_recheck``
rather than re-derived here — a second writer with its own copy of the
arithmetic is a cap that eventually differs between the two paths.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.routes.models import Route, RouteShare
from apps.routes.services.routes import _assert_under_cap, _locked_cap_recheck

if TYPE_CHECKING:
    from django.contrib.auth.models import User
    from django.contrib.sessions.backends.base import SessionBase

logger = logging.getLogger(__name__)

# The session key the pending-token list lives under. Its own constant
# because two apps read it — ``apps.routes.views`` writes and drops, and
# ``apps.public.views``'s map context asks whether the list is non-empty to
# decide ``routes_eligible`` — and a key spelled twice is a key that
# eventually differs. Sits beside ``analytics_utm`` in the session's
# documented contents (docs/accounts.md).
PENDING_SESSION_KEY = "route_shares"

# How many times a token collision is retried before giving up. Mirrors
# ``apps.public.api._SHARE_TOKEN_MAX_RETRIES``: with ~66 bits of entropy a
# single collision is already implausible, so this is a runaway guard
# rather than a realistic path.
_TOKEN_MAX_RETRIES = 5


class RouteShareTokenCollision(Exception):
    """Raised when a unique token could not be minted after every retry."""


# ---------------------------------------------------------------------------
# Creating a share
# ---------------------------------------------------------------------------


def create_route_share(user: "User", uuid: UUID) -> RouteShare:
    """Mint a share link for one of the given user's own routes.

    Owner-scoped by the lookup, not by a check afterwards: a uuid that is
    not this user's raises ``Route.DoesNotExist``, which the view turns
    into a 404 rather than a 403, so a probing request cannot tell "not
    yours" from "doesn't exist".

    A route may be shared more than once, and each call mints a new row.
    Reusing an existing live share would be the smaller write, but it
    would also mean one link that can never be narrowed: an owner who has
    sent a link to the wrong person has no way to stop it except to stop
    sharing the route with anyone. Separate rows make a future per-link
    revoke a delete of one row.

    Args:
        user: The authenticated owner of the route.
        uuid: The Route's uuid.

    Returns:
        The newly created RouteShare.

    Raises:
        Route.DoesNotExist: When no route with that uuid belongs to
            ``user``.
        RouteShareTokenCollision: When ``_TOKEN_MAX_RETRIES`` unique-token
            attempts all collided.

    """
    route = Route.objects.for_user(user).get(uuid=uuid)
    expires_at = timezone.now() + timedelta(days=settings.ROUTE_SHARE_MAX_AGE_DAYS)

    for attempt in range(_TOKEN_MAX_RETRIES):
        try:
            share = RouteShare.objects.create(
                # ``token_urlsafe(8)`` — 11 URL-safe characters, the same
                # generator and the same width BulletinShare uses.
                token=secrets.token_urlsafe(8),
                route=route,
                created_by=user,
                expires_at=expires_at,
            )
        except IntegrityError:
            # The only unique constraint on the row is ``token``, so an
            # IntegrityError here is a collision and nothing else. Retry
            # with a fresh one rather than failing the request.
            logger.warning("route_share: token collision, retrying")
            if attempt == _TOKEN_MAX_RETRIES - 1:
                logger.error(
                    "route_share: token collision after %d retries",
                    _TOKEN_MAX_RETRIES,
                )
            continue

        logger.info(
            "Route share created: user=%s route=%s token=%s",
            user.pk,
            route.uuid,
            share.token,
        )
        return share

    raise RouteShareTokenCollision(
        f"Could not mint a unique route-share token in {_TOKEN_MAX_RETRIES} attempts."
    )


# ---------------------------------------------------------------------------
# Claiming a share
# ---------------------------------------------------------------------------

# The Route fields a claimed copy inherits verbatim. Listed rather than
# derived from ``Route._meta`` so adding a field to Route is a deliberate
# decision about whether a copy should carry it, not an automatic yes: the
# three fields NOT here are the reason the list exists at all —
# ``user`` (the copy's whole point is that it belongs to someone else),
# and the BaseModel trio ``id``/``uuid``/``created_at``, which identify a
# row rather than describe a route.
_COPIED_FIELDS = (
    "name",
    "source_filename",
    "points",
    "distance_m",
    "ascent_m",
    "descent_m",
    "started_at",
    "finished_at",
    "point_count",
    "bounds",
)


def claim_route_share(user: "User", token: str) -> Route:
    """Copy the route behind an active share onto the claiming user's account.

    The original is never read for anything but its field values and is
    never written to. What IS written on the share row is the pair of
    counters — ``claim_count`` and ``last_claimed_at`` — which record that
    the link was used without gating whether it can be used again.

    The copy carries the sharer's ``name`` and ``source_filename``
    unchanged. The claimer can rename it afterwards through the ordinary
    row rename; seeding it with something like "Copy of …" would put a
    word of ours inside the user's own data, which every later surface
    would then render as though they had typed it.

    Cap-checked twice, exactly as ``create_route`` is: an unlocked early
    exit before the copy, and a locked re-check inside the transaction.
    Both are ``routes.py``'s, so a claim and an upload compete for the same
    cap under the same lock rather than each holding their own idea of it.

    Args:
        user: The authenticated user claiming the copy.
        token: The share token, from the URL.

    Returns:
        The newly created Route owned by ``user``.

    Raises:
        RouteShare.DoesNotExist: When the token matches no ACTIVE share —
            unknown, expired, or its route deleted. The three are one
            exception on purpose: distinguishing them for the caller would
            tell a guesser which tokens exist.
        apps.routes.services.routes.RouteLimitReached: When the claimer is
            already at ``settings.ROUTES_MAX_PER_USER``.

    """
    share = RouteShare.objects.active().select_related("route").get(token=token)
    source = share.route
    if source is None:
        # Unreachable through ``active()``, which filters on
        # ``route__isnull=False`` — but the FK is nullable, so mypy reads
        # the declaration rather than the filter, and this narrows it
        # honestly rather than with an assert. Raising the same exception
        # the filter would have keeps the caller's single 410 branch
        # correct if it ever does happen.
        raise RouteShare.DoesNotExist(f"Share {token} has no route.")

    _assert_under_cap(user)

    with transaction.atomic():
        _locked_cap_recheck(user)
        route = Route.objects.create(
            user=user,
            **{field: getattr(source, field) for field in _COPIED_FIELDS},
        )
        # F() would be the race-free increment, but it also makes the
        # in-memory value a CombinedExpression the caller cannot read, and
        # this counter is a record rather than a limit — nothing branches
        # on it, so a lost update under simultaneous claims costs an
        # inaccurate count and nothing else.
        share.claim_count += 1
        share.last_claimed_at = timezone.now()
        # updated_at is auto_now and applied in Python, so it must be named
        # explicitly or save(update_fields=…) leaves the column stale.
        share.save(update_fields=["claim_count", "last_claimed_at", "updated_at"])

    logger.info(
        "Route share claimed: user=%s token=%s source=%s copy=%s",
        user.pk,
        token,
        source.uuid,
        route.uuid,
    )
    return route


# ---------------------------------------------------------------------------
# The pending list — tokens followed but not yet claimed
# ---------------------------------------------------------------------------


def pending_tokens(session: "SessionBase") -> list[str]:
    """Return the session's pending share tokens, oldest first.

    Defensive about what it reads back. The session is a signed cookie
    (or a row keyed by one), so its contents cannot be forged — but they
    CAN be stale in a shape this code no longer writes, because a cookie
    outlives a deploy. Anything that is not a list of strings is treated
    as an empty list rather than raising halfway through rendering the
    map page.

    Args:
        session: The request's session.

    Returns:
        The token list, in the order the links were followed.

    """
    raw = session.get(PENDING_SESSION_KEY)
    if not isinstance(raw, list):
        return []
    return [token for token in raw if isinstance(token, str)]


def add_pending_token(session: "SessionBase", token: str) -> None:
    """Record that this browser has followed a share link, without claiming it.

    Idempotent: following the same link twice leaves one entry, in its
    original position. Re-adding at the end would make a refresh reorder
    the list, and the order is what the routes panel lists pending rows in.

    Bounded by ``settings.ROUTE_SHARE_MAX_PENDING``. When the list is
    full the OLDEST entry is dropped rather than the new one refused —
    the link the visitor has just followed is the one they mean, and a
    silent refusal would show them a map with no sign of the route they
    were sent.

    Args:
        session: The request's session.
        token: The share token just followed.

    """
    tokens = pending_tokens(session)
    if token not in tokens:
        tokens.append(token)
    session[PENDING_SESSION_KEY] = tokens[-settings.ROUTE_SHARE_MAX_PENDING :]
    # The list is a mutable object inside the session dict; assigning it
    # back marks the session modified, but say so explicitly rather than
    # relying on that — the assignment above is easy to refactor away.
    session.modified = True


def drop_pending_token(session: "SessionBase", token: str) -> None:
    """Remove one token from the session's pending list.

    Called when the claim lands. A claimed share stays claimable — the
    link is reusable — so what is dropped here is this browser's standing
    intention to claim it, not the share itself. Leaving it would re-offer
    a Save button for a route the user already has.

    Silent when the token is not in the list: a double-submitted claim
    reaches here twice and the second is not an error.

    Args:
        session: The request's session.
        token: The share token to drop.

    """
    tokens = [existing for existing in pending_tokens(session) if existing != token]
    session[PENDING_SESSION_KEY] = tokens
    session.modified = True


def pending_shares(session: "SessionBase") -> list[RouteShare]:
    """Resolve the session's pending tokens to active shares, pruning dead ones.

    Returns the shares in session order — the order the links were
    followed — because that is the order the routes panel and the map
    layer list them in, and a list that reordered itself between two
    surfaces would read as two different lists.

    **It prunes as it reads.** A token whose share has expired or whose
    route has been deleted is dropped from the session here, so a dead
    link stops costing a query on every subsequent page load rather than
    sitting in the cookie until the session ends. This is the one place
    that write happens, which is why the function takes the session
    rather than a token list.

    One query however many tokens are pending, and ``select_related`` on
    the route because every caller renders it.

    Args:
        session: The request's session.

    Returns:
        The claimable shares, oldest-followed first.

    """
    tokens = pending_tokens(session)
    if not tokens:
        return []

    by_token = {
        share.token: share
        for share in RouteShare.objects.active()
        .select_related("route")
        .filter(token__in=tokens)
    }

    live = [by_token[token] for token in tokens if token in by_token]
    if len(live) != len(tokens):
        session[PENDING_SESSION_KEY] = [share.token for share in live]
        session.modified = True

    return live
