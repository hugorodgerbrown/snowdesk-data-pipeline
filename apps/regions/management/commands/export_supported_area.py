"""
export_supported_area — write the supported area as GeoJSON (SNOW-1444).

The supported area is the ground the self-hosted 3-D elevation extract
covers: every z12 tile that touches a ``MicroRegion.boundary``, plus a
ring of one tile round each, dissolved into one Polygon or MultiPolygon
(``apps.regions.services.basemap_tiles.supported_area_tiles`` and
``tiles_to_geometry``). The ``snowdesk-tiles`` repo's ``build-dem.sh``
passes this file to ``pmtiles extract --region=``; the seasonal refresh
that uses it is in ``docs/runbooks/self-hosted-tiles.md``.

Read-only. It writes a file and changes no data, so it takes no
``--commit``; like every ``RegisteredCommand`` it records its own
``CommandRun``. The GeoJSON always goes to ``--output`` (default
``supported-area.geojson``), never to stdout, because stdout carries
``iterate_rows``' per-region progress lines and the closing summary.

A region with no boundary is skipped and counted. With no boundary at
all there is no area, so the command writes nothing and exits non-zero.

Usage:
    uv run python manage.py export_supported_area
    uv run python manage.py export_supported_area --output /tmp/area.geojson
"""

from __future__ import annotations

import json
import logging
from argparse import ArgumentParser
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from django.core.management.base import CommandError

from apps.core.command_iteration import iterate_rows
from apps.core.command_register import RegisteredCommand
from apps.regions.models import MicroRegion
from apps.regions.services.basemap_tiles import (
    SUPPORTED_AREA_ZOOM,
    supported_area_tiles,
    tiles_to_geometry,
)

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = "supported-area.geojson"


class Command(RegisteredCommand):
    """Write the union of region boundaries, on z12 tiles, as GeoJSON."""

    help = (
        "Write the supported area (every z12 tile touching a micro-region "
        "boundary, plus a one-tile ring) as GeoJSON to --output. Read-only."
    )

    def add_arguments(self, parser: ArgumentParser) -> None:
        """Register command-line arguments."""
        parser.add_argument(
            "--output",
            metavar="PATH",
            default=DEFAULT_OUTPUT,
            help=f"Where to write the GeoJSON (overwrites). Default: {DEFAULT_OUTPUT}.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Collect the tiles, write the file, and report what was read."""
        verbosity: int = options["verbosity"]
        path = Path(options["output"])
        counts = {"regions_read": 0, "regions_skipped": 0}

        tiles = supported_area_tiles(self._boundaries(counts, verbosity))

        self.tally("regions_read", counts["regions_read"])
        self.tally("regions_skipped", counts["regions_skipped"])
        self.tally("tiles", len(tiles))

        if not tiles:
            raise CommandError(
                "No micro-region has a boundary, so there is no supported "
                "area to write. Load the region fixtures first."
            )

        collection = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "name": "Snowdesk supported area",
                        "zoom": SUPPORTED_AREA_ZOOM,
                        "tiles": len(tiles),
                        "regions": counts["regions_read"],
                    },
                    "geometry": tiles_to_geometry(tiles, SUPPORTED_AREA_ZOOM),
                }
            ],
        }
        path.write_text(json.dumps(collection), encoding="utf-8")

        logger.info(
            "export_supported_area wrote %s: regions_read=%d regions_skipped=%d "
            "tiles=%d",
            path,
            counts["regions_read"],
            counts["regions_skipped"],
            len(tiles),
        )
        if verbosity >= 1:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Wrote {len(tiles)} z{SUPPORTED_AREA_ZOOM} tiles from "
                    f"{counts['regions_read']} region(s) to {path}; "
                    f"{counts['regions_skipped']} skipped with no boundary."
                )
            )

    def _boundaries(
        self, counts: dict[str, int], verbosity: int
    ) -> Iterator[dict[str, Any]]:
        """Yield each region's boundary, counting the regions read and skipped.

        Streams ``MicroRegion`` with ``iterate_rows``, loading only the
        fields it reads, so no region is held once its tiles are counted.

        Args:
            counts: Updated in place with ``regions_read`` and
                ``regions_skipped``.
            verbosity: Django's ``--verbosity`` level, for the progress lines.

        Yields:
            Each non-empty ``boundary`` GeoJSON geometry.

        """
        regions = MicroRegion.objects.only("pk", "region_id", "boundary")
        for region in iterate_rows(
            self,
            regions,
            verbosity=verbosity,
            describe=lambda r: r.region_id,
        ):
            if not region.boundary:
                counts["regions_skipped"] += 1
                continue
            counts["regions_read"] += 1
            yield region.boundary
