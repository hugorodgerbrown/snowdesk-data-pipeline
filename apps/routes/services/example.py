"""
apps/routes/services/example.py — the example route every visitor is offered.

SNOW-1230. A first-time visitor has no GPX to hand, and route analysis is
the thing Snowdesk does that nothing else does, so the map offers one real
tour to explore before anyone uploads anything. The example is an ordinary
``RouteShare`` with two extra facts on it — ``is_example`` and
``example_date`` — rather than a model of its own, because everything a
visitor does with it is something a share recipient already does:

- the link puts the token in the session and lands on the map with the
  route drawn (``route_share_redirect``'s path, reused by ``example_view``);
- the panel, the profile, the point card and now the terrain data read the
  pending share exactly as they read any other.

What the two flags change, and nothing else:

- **It never expires.** ``RouteShareQuerySet.active()`` treats the example
  as live whatever its ``expires_at``.
- **It can never be claimed.** ``RouteShareQuerySet.claimable()`` leaves it
  out, so no Save writes a tour the visitor never skied onto their account.
- **It opens on its own day.** ``example_date`` is a past day with a full
  bulletin, so the demonstration is complete even in October. The map
  marks that day as the example's for as long as it is open.

The route itself belongs to ``EXAMPLE_OWNER_EMAIL``, an account nobody
signs in to, so the tour never appears in a real person's routes list.
Which tour, and which day, are a person's decision — see the
``set_example_route`` command, which is the only writer.
"""

from __future__ import annotations

import logging
import secrets
from datetime import date, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.routes.models import Route, RouteShare

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# The account the example route belongs to. ``.invalid`` is reserved
# (RFC 2606), so nothing is ever delivered to it, and the account has an
# unusable password, so nobody signs in as it.
EXAMPLE_OWNER_EMAIL = "example-routes@snowdesk.invalid"

# The tour and the day Hugo chose (SNOW-1230, 25 Oct 2026): Mont Fort to
# Col de la Chaux from the canonical corpus, on a spring day with a full
# Martigny–Verbier bulletin. The defaults of ``set_example_route``.
DEFAULT_EXAMPLE_FILENAME = "mont-fort-col-de-la-chaux.gpx"
DEFAULT_EXAMPLE_DATE = date(2026, 4, 10)


def example_share() -> RouteShare | None:
    """Return the live example share, or None when none is set.

    Returns:
        The example share with its route loaded, or None.

    """
    return RouteShare.objects.example().select_related("route").first()


def example_owner() -> "User":
    """Return the account the example route belongs to, creating it if absent.

    Returns:
        The example owner.

    """
    user_model = get_user_model()
    user, created = user_model.objects.get_or_create(
        email=EXAMPLE_OWNER_EMAIL,
        defaults={"username": EXAMPLE_OWNER_EMAIL},
    )
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
        logger.info("Example route owner created: user=%s", user.pk)
    return user


def set_example(route: Route, example_date: date) -> RouteShare:
    """Make ``route`` the example, opening on ``example_date``.

    Any earlier example is unmarked in the same transaction, so there is
    never a moment with two (the partial unique constraint would refuse it)
    or none. The earlier row is kept rather than deleted: a link someone
    copied from the old example keeps working as an ordinary share until
    it expires.

    An unchanged example (same route, same day) is returned as it is.

    Args:
        route: The route to offer.
        example_date: The day the map opens on.

    Returns:
        The example share.

    """
    current = example_share()
    if (
        current is not None
        and current.route_id == route.pk
        and current.example_date == example_date
    ):
        return current

    now = timezone.now()
    with transaction.atomic():
        # The old example becomes an ordinary share with an ordinary
        # window from now, rather than a dead link the moment it is
        # replaced.
        RouteShare.objects.filter(is_example=True).update(
            is_example=False,
            expires_at=now + timedelta(days=settings.ROUTE_SHARE_MAX_AGE_DAYS),
            updated_at=now,
        )
        if current is not None and current.route_id == route.pk:
            # Same route, new day: move the day on the existing row, so a
            # link already handed out still names the example.
            current.is_example = True
            current.example_date = example_date
            current.save(update_fields=["is_example", "example_date", "updated_at"])
            share = current
        else:
            share = RouteShare.objects.create(
                token=_new_token(),
                route=route,
                created_by=route.user,
                # Never read for the example (``active()`` ignores it), but
                # the column is required, and if the row is ever unmarked
                # it then expires on the ordinary schedule.
                expires_at=now + timedelta(days=settings.ROUTE_SHARE_MAX_AGE_DAYS),
                is_example=True,
                example_date=example_date,
            )
    logger.info(
        "Example route set: route=%s date=%s token=%s",
        route.uuid,
        example_date.isoformat(),
        share.token,
    )
    return share


def _new_token() -> str:
    """Return a fresh share token, the same shape every RouteShare uses.

    Returns:
        An 11-character URL-safe token.

    """
    return secrets.token_urlsafe(8)
