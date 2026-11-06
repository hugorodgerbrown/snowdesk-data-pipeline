"""
apps/map/models.py — the map app's models: a share token and 3D test results.

``Map3dShare`` lets a reader of the flagged 3-D map hand it to people the
flag does not cover, signed in or not. One token per user, minted the first
time they open /map/3d/; any link that carries it (``?share=<token>``) opens
the page without the flag or a sign-in. Deleting the row in the admin stops
every link that carries it.

``Map3dDeviceTest`` (SNOW-1456) is one run of /map/'s "Test 3D on this
device", sent anonymously by a reader who chose to: the verdict, the
graphics chip, the screen and each camera pass's frame times. It holds no
user, no session and no IP, so the frame budget can be read across real
devices without knowing whose they are.
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


# The verdicts a sent 3D test carries (static/js/map_3d_core.js
# TEST_VERDICTS). An unmeasured run is never sent.
MAP_3D_TEST_VERDICTS = ("smooth", "stutter", "slow")

# The layers-menu switches a 3D test records as on (each row's
# data-overlay-key on /map/, apps/map/templates/public/partials/
# _map_embed.html). The panel reads this list from the page, and the
# endpoint refuses any other key.
MAP_3D_TEST_OVERLAYS = (
    "terrain",
    "ski_tours",
    "ski_summits",
    "rest_zones",
    "problem_terrain",
    "weather",
    "resorts",
    "community_reports",
    "favourites",
    "routes",
    "downloads",
    "unsupported",
)


class Map3dDeviceTestQuerySet(models.QuerySet["Map3dDeviceTest"]):
    """Custom queryset for Map3dDeviceTest."""

    def from_readers(self) -> Map3dDeviceTestQuerySet:
        """Return the runs readers sent, leaving out the staff probe's.

        Returns:
            The runs whose ``probe`` is False.

        """
        return self.filter(probe=False)


class Map3dDeviceTest(BaseModel):
    """One 3D test result a reader sent from /map/, anonymously.

    Written by ``POST /api/map-3d-test/`` (``apps.map.api.map_3d_test_receive``)
    and read in the admin, filtered by verdict and probe. Deliberately
    holds nothing about who sent it.
    """

    class Verdict(models.TextChoices):
        """The test's verdict, by the watchdog's own rule."""

        SMOOTH = "smooth", "Smooth"
        STUTTER = "stutter", "May stutter zoomed out"
        SLOW = "slow", "Too slow"

    verdict = models.CharField(
        max_length=16,
        choices=Verdict.choices,
        help_text="smooth: both passes kept up; stutter: only z9 fell behind; slow.",
    )
    gpu = models.CharField(
        max_length=255,
        blank=True,
        help_text="The graphics chip's name, where the browser gives it.",
    )
    device_pixel_ratio = models.FloatField(
        help_text="window.devicePixelRatio: the screen's own density."
    )
    pixel_ratio = models.FloatField(
        help_text="The map's pixel ratio in 3D, capped at MAX_PIXEL_RATIO."
    )
    canvas_width = models.PositiveIntegerField(help_text="The canvas's width, px.")
    canvas_height = models.PositiveIntegerField(help_text="The canvas's height, px.")
    user_agent = models.CharField(max_length=512, blank=True)
    client_version = models.CharField(
        max_length=64, blank=True, help_text="The page's pwa-app-version."
    )
    probe = models.BooleanField(
        default=False,
        help_text="Staff re-measure (/map/?probe=1), on the budget's layers.",
    )
    overlays = models.JSONField(
        default=list,
        blank=True,
        help_text="The layers-menu switches that were on (MAP_3D_TEST_OVERLAYS).",
    )
    passes = models.JSONField(
        default=list,
        help_text="Each camera pass's frame figures, in PROBE_PASSES order.",
    )

    objects = Map3dDeviceTestQuerySet.as_manager()

    class Meta:
        """Model metadata."""

        ordering = ["-created_at"]
        verbose_name = "3D device test"

    def __str__(self) -> str:
        """Return the readable label."""
        return self.to_string()

    def to_string(self) -> str:
        """Return a readable label: the verdict and the graphics chip.

        Returns:
            The label.

        """
        return f"3D device test {self.verdict} ({self.gpu or 'unknown GPU'})"
