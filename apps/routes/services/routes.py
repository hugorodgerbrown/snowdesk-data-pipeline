"""
apps/routes/services/routes.py — Business logic for creating and deleting Routes.

Provides ``create_route``, ``create_drawn_route`` (SNOW-1255) and
``delete_route`` — the mutating entry points used by
``apps/routes/views.py``. Parsing itself lives next door in
``gpx.py``; this module owns the per-user cap and the database write.

The uploaded bytes reach ``create_route``, are handed to ``parse_gpx``, and
go out of scope. Nothing is written to disk at any point — see
``docs/decisions/gpx-uploads-are-parsed-not-stored.md``.

Terrain sampling (SNOW-910) is enqueued here rather than done here, and
the LINE IT SITS ON MATTERS: after the transaction, never inside it. See
``apps.routes.services.slope_segments.enqueue_route_slope_sampling``.

The cap itself is two functions — ``_assert_under_cap`` and
``_locked_cap_recheck`` — rather than the pair of inline blocks
``create_route`` carried until SNOW-764. There is now a SECOND way a Route
row is written (claiming a share, in ``shares.py``), and a cap enforced by
copy-pasted arithmetic is a cap that eventually differs between the two
paths. Both are private to this package and imported by ``shares.py``: the
lock and the re-check are the whole reason concurrent creators cannot
exceed the cap, and a second writer that skipped them would reopen the
race for both.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils.translation import gettext

from apps.routes.models import Route
from apps.routes.services.drawn import draft_route
from apps.routes.services.gpx import parse_gpx
from apps.routes.services.slope_segments import enqueue_route_slope_sampling
from apps.routes.services.track_render import build_track_render, stamp_render

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


def _assert_under_cap(user: "User") -> None:
    """Raise ``RouteLimitReached`` when the user is already at the cap.

    The cheap, unlocked check: it takes no lock and is therefore racy on
    its own, which is fine because it is not the enforcement — it is the
    early exit that keeps expensive work (parsing a multi-megabyte GPX,
    copying a shared route's geometry) from happening for a row that
    cannot be stored. ``_locked_cap_recheck`` below is what actually
    enforces the cap.

    Args:
        user: The user about to gain a route.

    Raises:
        RouteLimitReached: When the user already holds
            ``settings.ROUTES_MAX_PER_USER`` routes.

    """
    if Route.objects.for_user(user).count() >= settings.ROUTES_MAX_PER_USER:
        raise RouteLimitReached(
            f"User {user.pk} has reached the "
            f"{settings.ROUTES_MAX_PER_USER}-route limit."
        )


def _locked_cap_recheck(user: "User") -> None:
    """Lock the user row, then re-check the cap. Call inside ``atomic()``.

    Locking first is what makes the cap hold under concurrency (SNOW-465's
    ``create_favourite`` is the precedent). A bare ``count()`` takes no
    lock, so under PostgreSQL READ COMMITTED two requests could both read
    below the cap and both insert, exceeding it. ``select_for_update()``
    makes the second transaction block until the first commits, so its
    count reflects the first insert. On SQLite (tests) ``FOR UPDATE`` is a
    silent no-op, which is why this is argued rather than demonstrated
    here.

    The lock is taken on the USER row rather than on the routes: the thing
    being serialised is "how many routes does this user have", and there is
    no row to lock for a route that does not exist yet.

    Args:
        user: The user about to gain a route.

    Raises:
        RouteLimitReached: When the user already holds
            ``settings.ROUTES_MAX_PER_USER`` routes.

    """
    get_user_model().objects.select_for_update().get(pk=user.pk)
    _assert_under_cap(user)


def create_route(user: "User", raw: bytes, source_filename: str = "") -> Route:
    """Parse GPX bytes and store the result as a Route for the given user.

    The per-user cap is checked once up front — so a file that cannot be
    stored is never parsed — and again inside the transaction after locking
    the user row, which serialises concurrent creators so the cap can't be
    exceeded under a race. This mirrors ``create_favourite`` (SNOW-465); see
    the comment at the re-check for the full rationale.

    Terrain sampling is enqueued once the transaction has committed, so
    the returned route's ``slope_samples`` may still be null when the
    caller reads it — under the DatabaseBackend it certainly is. Callers
    must treat that as "not yet", which is the same null the field's
    help_text describes.

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
    _assert_under_cap(user)

    # CPU-bound and potentially slow on a large track — kept outside the
    # transaction so parsing never holds a row lock.
    parsed = parse_gpx(raw)

    with transaction.atomic():
        _locked_cap_recheck(user)
        route = Route.objects.create(
            user=user,
            name=parsed.name[:_NAME_MAX_LENGTH],
            source_filename=source_filename[:_FILENAME_MAX_LENGTH],
            points=parsed.points,
            distance_m=parsed.distance_m,
            ascent_m=parsed.ascent_m,
            descent_m=parsed.descent_m,
            started_at=parsed.started_at,
            finished_at=parsed.finished_at,
            point_count=parsed.point_count,
            source_point_count=parsed.source_point_count,
            bounds=parsed.bounds,
            # SNOW-1309: drawn flat until its terrain is sampled.
            track_render=build_track_render(
                parsed.points,
                None,
                ascent_m=parsed.ascent_m,
                descent_m=parsed.descent_m,
            ),
        )

    logger.info(
        "Route created: user=%s uuid=%s points=%d/%d tracks=%d",
        user.pk,
        route.uuid,
        parsed.point_count,
        parsed.source_point_count,
        parsed.track_count,
    )

    # SNOW-910: terrain sampling, AFTER the transaction has committed and
    # deliberately not inside it. Under the ImmediateBackend — dev, test
    # AND staging — ``.enqueue()`` runs the worker inline, so enqueuing a
    # line earlier would make one tile-origin round trip per terrain tile
    # the track crosses while still holding the ``select_for_update`` lock
    # on the user row. Every backend looks identical on a green test run;
    # only production would pay for it.
    #
    # The upload's own response does not wait for it either way, which is
    # the other half of the decision: a route is drawn flat until the
    # sampling lands, and a first paint is worth more than a coloured one
    # twenty seconds later.
    enqueue_route_slope_sampling(route)
    return route


def create_drawn_route(
    user: "User", points: list[list[float | None]], name: str = ""
) -> Route:
    """Store waypoints drawn on the map as a Route for the given user.

    The drawn twin of ``create_route`` (SNOW-1255): the same unlocked early
    exit, the same locked re-check inside the transaction, and the same
    terrain sampling enqueued after the commit — so a drawn route is
    sampled, coloured and leg-split by exactly the path an upload takes.

    Args:
        user: The authenticated user saving the route.
        points: Waypoints already through ``drawn.clean_waypoints``.
        name: The route's name, already through ``drawn.clean_name``;
            "Drawn route" when empty.

    Returns:
        The newly created Route.

    Raises:
        RouteLimitReached: When the user already holds
            ``settings.ROUTES_MAX_PER_USER`` routes.

    """
    _assert_under_cap(user)

    # A drawn route has no file to borrow a name from, and a row with no
    # name reads as a broken one, so an unnamed draft gets a plain label
    # its owner can rename.
    draft = draft_route(points, name=name or gettext("Drawn route"), user=user)
    with transaction.atomic():
        _locked_cap_recheck(user)
        stamp_render(draft)
        draft.save()

    logger.info(
        "Drawn route created: user=%s uuid=%s points=%d",
        user.pk,
        draft.uuid,
        draft.point_count,
    )

    # After the commit, never inside it — see create_route's own note.
    enqueue_route_slope_sampling(draft)
    return draft


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
