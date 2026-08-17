"""
apps/routes/services/routes.py — Business logic for creating and deleting Routes.

Provides ``create_route`` and ``delete_route`` — the mutating entry points
used by ``apps/routes/views.py``. Parsing itself lives next door in
``gpx.py``; this module owns the per-user cap and the database write.

The uploaded bytes reach ``create_route``, are handed to ``parse_gpx``, and
go out of scope. Nothing is written to disk at any point — see
``docs/decisions/gpx-uploads-are-parsed-not-stored.md``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.routes.models import Route
from apps.routes.services.gpx import parse_gpx

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)

# Must match Route.name / Route.source_filename max_lengths. A GPX may name
# itself anything at all, so both are truncated here rather than left to
# raise a DB DataError on a file we could otherwise import fine.
_NAME_MAX_LENGTH = 100
_FILENAME_MAX_LENGTH = 255


class RouteLimitReached(Exception):
    """Raised when a user has reached ``settings.ROUTES_MAX_PER_USER``."""


def create_route(user: "User", raw: bytes, source_filename: str = "") -> Route:
    """Parse GPX bytes and store the result as a Route for the given user.

    The per-user cap is checked once up front — so a file that cannot be
    stored is never parsed — and again inside the transaction after locking
    the user row, which serialises concurrent creators so the cap can't be
    exceeded under a race. This mirrors ``create_favourite`` (SNOW-465); see
    the comment at the re-check for the full rationale.

    Args:
        user: The authenticated user uploading the route.
        raw: The raw bytes of the uploaded ``.gpx`` file.
        source_filename: The uploaded file's name, kept as a label.
            Defaults to "".

    Returns:
        The newly created Route.

    Raises:
        RouteLimitReached: When the user already holds
            ``settings.ROUTES_MAX_PER_USER`` routes.
        apps.routes.services.gpx.GPXParseError: When the bytes are not a
            GPX file we can import.

    """
    if Route.objects.for_user(user).count() >= settings.ROUTES_MAX_PER_USER:
        raise RouteLimitReached(
            f"User {user.pk} has reached the "
            f"{settings.ROUTES_MAX_PER_USER}-route limit."
        )

    # CPU-bound and potentially slow on a large track — kept outside the
    # transaction so parsing never holds a row lock.
    parsed = parse_gpx(raw)

    with transaction.atomic():
        # Lock the user row before the cap re-check so concurrent creators
        # serialise here. A bare count() takes no lock, so under PostgreSQL
        # READ COMMITTED two requests could both read below the cap and both
        # insert, exceeding it. select_for_update() makes the second
        # transaction block until the first commits, so its count reflects
        # the first insert. On SQLite (tests) FOR UPDATE is a silent no-op.
        get_user_model().objects.select_for_update().get(pk=user.pk)
        if Route.objects.for_user(user).count() >= settings.ROUTES_MAX_PER_USER:
            raise RouteLimitReached(
                f"User {user.pk} has reached the "
                f"{settings.ROUTES_MAX_PER_USER}-route limit."
            )
        route = Route.objects.create(
            user=user,
            name=parsed.name[:_NAME_MAX_LENGTH],
            source_filename=source_filename[:_FILENAME_MAX_LENGTH],
            points=parsed.points,
            distance_m=parsed.distance_m,
            ascent_m=parsed.ascent_m,
            point_count=parsed.point_count,
            bounds=parsed.bounds,
        )

    logger.info(
        "Route created: user=%s uuid=%s points=%d/%d tracks=%d",
        user.pk,
        route.uuid,
        parsed.point_count,
        parsed.source_point_count,
        parsed.track_count,
    )
    return route


def delete_route(user: "User", uuid: UUID) -> None:
    """Delete the given user's route by uuid.

    Owner-checked: only deletes a row belonging to ``user``.

    Args:
        user: The authenticated user requesting the deletion.
        uuid: The Route's uuid.

    Raises:
        Route.DoesNotExist: When no matching route is owned by this user.

    """
    route = Route.objects.for_user(user).get(uuid=uuid)
    route.delete()
    logger.info("Route deleted: user=%s uuid=%s", user.pk, uuid)
