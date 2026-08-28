"""
apps/downloads/models.py — Database models for the downloads application.

Defines ``DownloadArea``: the *definition* of an offline basemap area an
authenticated user has downloaded, synced to their account so it can be
re-downloaded on another device (SNOW-749).

**What this table is not.** It holds no tiles, no tile indices, and no byte
count. The tiles live in the downloading device's own pinned Cache Storage
bucket and never leave it; a row here is the handful of facts another
device needs to fetch the same area for itself. The industry split this
follows — definition in the account, bytes on the device — is Komoot's for
purchased regions and Gaia GPS's for saved maps; Google Maps, Apple Maps
and OsmAnd keep offline areas strictly per-device with no account row at
all.

Three fields the client stores locally are deliberately absent:

* ``band`` — always ``basemap_download_core.js``'s ``MICRO_BAND``
  (``[10, 14]``), mirroring ``apps.regions.services.basemap_tiles``. It is
  a constant, not a user choice, so a column would record the same two
  numbers on every row and invite a reader to believe otherwise.
* ``template`` — the tile URL template, derived from settings plus the
  basemap key. Storing it would pin an external endpoint into a database
  row, which is exactly the thing settings exist to keep out of the schema.
* ``bytes`` — a per-device measurement of what one device's bucket holds,
  not a property of the area. Two devices downloading the same region
  legitimately report different sizes.

``area_id`` is the join key, and it is minted by the CLIENT rather than
here. That is not a shortcut: ``basemap_download_core.js``'s
``areaIdForRegion`` already produces ``region-<region_id>`` deterministically,
so the same region carries the same id on every device the user owns, and a
custom area's ``custom-<uuid>`` is already a uuid. Reusing it means
``basemap_manage_core.js``'s ``reconcileAreas`` stays a single keyed join
with no server-id ↔ local-id mapping for either side to drift from.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models

from apps.core.models import BaseModel

if TYPE_CHECKING:
    from django.contrib.auth.models import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# QuerySet / Manager
# ---------------------------------------------------------------------------


class DownloadAreaQuerySet(models.QuerySet["DownloadArea"]):
    """Custom queryset for DownloadArea."""

    def for_user(self, user: "User") -> "DownloadAreaQuerySet":
        """Return the download areas belonging to the given user.

        Every view scopes its lookups through this rather than filtering on
        the primary key alone — an area is only ever addressable by its
        owner.

        Args:
            user: The user to filter by.

        Returns:
            Filtered queryset.

        """
        return self.filter(user=user)

    def regions(self) -> "DownloadAreaQuerySet":
        """Return only the whole-region areas.

        Returns:
            Filtered queryset.

        """
        return self.filter(kind=DownloadArea.KIND.REGION)

    def custom(self) -> "DownloadAreaQuerySet":
        """Return only the user-framed custom areas.

        Returns:
            Filtered queryset.

        """
        return self.filter(kind=DownloadArea.KIND.CUSTOM)


# ---------------------------------------------------------------------------
# DownloadArea
# ---------------------------------------------------------------------------


class DownloadArea(BaseModel):
    """One offline basemap area recorded against a user's account.

    A row says "this user downloaded this area somewhere". It does not say
    which device, and it deliberately cannot: the manage sheet answers
    "is it on THIS device" by looking at this device's own Cache Storage,
    which is the only place that question has a truthful answer.

    ``region_id`` is populated for ``KIND.REGION`` and blank otherwise;
    ``bbox`` is the reverse. Neither is enforced by a database constraint —
    a check constraint spanning ``kind`` would have to be rewritten for
    every future kind, and the two views that write rows already validate
    the pairing. ``to_string`` reads whichever one its ``kind`` names.

    ``bbox`` is ``[west, south, east, north]`` in **(lon, lat) order**,
    matching ``apps.regions.services.basemap_tiles`` and
    ``basemap_download_core.js`` — both of which carve out of the project's
    usual (lat, lon) argument order for the same reason, and are the two
    modules this value is handed straight back to.
    """

    class KIND(models.TextChoices):
        """What shape of area this row describes.

        A typed constant rather than a bare string, so a comparison against
        an unknown kind fails loudly at the point of use.
        """

        REGION = "REGION", "Region"
        CUSTOM = "CUSTOM", "Custom area"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="download_areas",
        help_text="User whose account this area is recorded against.",
    )
    area_id = models.CharField(
        max_length=100,
        help_text=(
            "The client-minted area id — 'region-<region_id>' or "
            "'custom-<uuid>'. Also names the pinned Cache Storage bucket on "
            "each device, which is what makes it the join key."
        ),
    )
    kind = models.CharField(
        max_length=16,
        choices=KIND.choices,
        help_text="Whether this is a whole region or a user-framed area.",
    )
    region_id = models.CharField(
        max_length=50,
        blank=True,
        help_text=(
            "EAWS micro-region id, for a region area. Blank for a custom "
            "area. Not an FK: a region that leaves the fixture should not "
            "delete a user's record of having downloaded it."
        ),
    )
    bbox = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "[west, south, east, north] in (lon, lat) order, for a custom "
            "area. Null for a region, whose tiles are computed server-side "
            "from its own boundary."
        ),
    )
    basemap_key = models.CharField(
        max_length=50,
        blank=True,
        help_text=(
            "The basemap the area was downloaded under, as the picker keys "
            "it. Blank when the client could not resolve one — which reads "
            "as an unknown basemap, never a wrong one."
        ),
    )
    name = models.CharField(
        max_length=100,
        blank=True,
        help_text=(
            "User-supplied label for a custom area. Blank for a region "
            "(whose name is the region's own) and for a custom area the "
            "user has never renamed."
        ),
    )

    objects = DownloadAreaQuerySet.as_manager()

    class Meta(BaseModel.Meta):
        """Model metadata."""

        ordering = ["-created_at"]
        constraints = [
            # One row per area per user. This is what makes the sync
            # endpoint idempotent under a mutation-queue replay without
            # depending on IdempotencyMiddleware: the write is an
            # update_or_create against exactly this key.
            models.UniqueConstraint(
                fields=["user", "area_id"],
                name="unique_download_area_per_user",
            ),
        ]

    def to_string(self) -> str:
        """Return a concise human-readable description of this area.

        Format: ``"{user} — {label} ({kind})"``, where the label is the
        stored name, the region id, or the area id, in that order — the
        same fallback chain the manage sheet's row label walks.
        """
        label = self.name or self.region_id or self.area_id
        return f"{self.user} — {label} ({self.get_kind_display()})"

    def __str__(self) -> str:
        """Return a human-readable representation."""
        return self.to_string()
