"""
apps/map/models.py — the map app's one model: a 3-D map share token.

``Map3dShare`` lets a reader of the flagged 3-D map hand it to people the
flag does not cover, signed in or not. One token per user, minted the first
time they open /map/3d/; any link that carries it (``?share=<token>``) opens
the page without the flag or a sign-in. Deleting the row in the admin stops
every link that carries it.
"""

from __future__ import annotations

import logging
import secrets

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

logger = logging.getLogger(__name__)


def new_share_token() -> str:
    """Return a fresh URL-safe token, as ``RouteShare`` mints them.

    Returns:
        11 URL-safe characters, about 66 bits of entropy.

    """
    return secrets.token_urlsafe(8)


class Map3dShareQuerySet(models.QuerySet["Map3dShare"]):
    """Custom queryset for Map3dShare."""

    def for_token(self, token: str) -> Map3dShareQuerySet:
        """Return the share carrying ``token``, as a queryset of at most one.

        Args:
            token: The ``?share=`` value from a link.

        Returns:
            The matching share, or an empty queryset for a blank token.

        """
        if not token:
            return self.none()
        return self.filter(token=token)


class Map3dShare(BaseModel):
    """A per-user token that opens /map/3d/ for anyone holding the link.

    The 3-D map is a sandbox behind the ``map_3d`` flag. This row is the
    one way past the flag: whoever opens a link carrying ``token`` sees the
    page as an anonymous visitor would (the sharer's routes stay private).
    Revoking it is deleting the row.
    """

    token = models.CharField(
        max_length=32,
        unique=True,
        default=new_share_token,
        help_text="URL-safe random token carried as ?share=<token> on /map/3d/.",
    )
    created_by = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="map_3d_share",
        help_text="The reader whose links carry this token.",
    )

    objects = Map3dShareQuerySet.as_manager()

    class Meta:
        """Model metadata."""

        ordering = ["-created_at"]
        verbose_name = "3-D map share"

    def __str__(self) -> str:
        """Return the readable label."""
        return self.to_string()

    def to_string(self) -> str:
        """Return a readable label: the token and who it belongs to.

        Returns:
            The label.

        """
        return f"3-D map share {self.token} ({self.created_by_id})"
