"""
apps/mcp_server/ui_resources.py — MCP Apps UI resources (``ui://`` templates).

Spike for the MCP Apps extension (``io.modelcontextprotocol/ui``, spec
revision 2026-01-26): a tool whose ``tools/list`` entry carries
``_meta.ui.resourceUri`` is rendered by an Apps-capable host (Claude,
Claude Desktop) as an interactive HTML view inside the conversation. The
host fetches that HTML with ``resources/read``; this module is the registry
those reads resolve against.

A resource's HTML is a self-contained document under
``apps/mcp_server/ui/``. It speaks the Apps postMessage dialect to the host
directly — no SDK — and reaches back to this server only through the host's
``tools/call`` proxy, so it needs no ``connectDomains`` entry for Snowdesk
itself. The CSP lists only the third-party origins the view loads from.

Hosts without Apps support ignore ``_meta`` and treat the tool as an
ordinary text tool, so nothing here is negotiated per client — the server
is stateless and always advertises the metadata.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: MIME type the Apps spec requires for an HTML view.
RESOURCE_MIME_TYPE = "text/html;profile=mcp-app"

#: URI of the danger-map view, referenced by the map tools' ``_meta``.
DANGER_MAP_URI = "ui://snowdesk/danger-map.html"

_UI_DIR = Path(__file__).resolve().parent / "ui"

#: MapLibre is pinned to the version vendored in ``static/js`` so the view
#: renders exactly as the map page does.
_MAPLIBRE_ORIGIN = "https://cdn.jsdelivr.net"
#: OpenFreeMap's Liberty style — the one basemap in ``static/js`` that needs
#: no key and covers every provider's country.
_BASEMAP_ORIGIN = "https://tiles.openfreemap.org"
#: Raster tiles for the SVG fallback, drawn when the host's CSP stops
#: MapLibre's blob: worker. An <image> tile loads under img-src, which
#: ``resourceDomains`` covers, so the fallback still shows a basemap:
#: swisstopo's winter map for a Swiss scope, OpenStreetMap elsewhere.
_SWISSTOPO_RASTER_ORIGIN = "https://wmts.geo.admin.ch"
_OSM_RASTER_ORIGIN = "https://tile.openstreetmap.org"


@dataclass(frozen=True)
class UiResource:
    """One ``ui://`` resource, as listed by ``resources/list``."""

    uri: str
    name: str
    description: str
    filename: str
    csp: dict[str, list[str]]
    prefers_border: bool = True

    def listing(self) -> dict[str, Any]:
        """Return the ``resources/list`` entry for this resource."""
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": RESOURCE_MIME_TYPE,
        }

    def contents(self) -> dict[str, Any]:
        """Return the ``resources/read`` content item, HTML included."""
        return {
            "uri": self.uri,
            "mimeType": RESOURCE_MIME_TYPE,
            "text": _read_html(self.filename),
            "_meta": {"ui": {"csp": self.csp, "prefersBorder": self.prefers_border}},
        }


@functools.cache
def _read_html(filename: str) -> str:
    """Read a view's HTML once per process.

    Args:
        filename: File name under ``apps/mcp_server/ui/``.

    Returns:
        The document text.

    """
    return (_UI_DIR / filename).read_text(encoding="utf-8")


UI_RESOURCES: dict[str, UiResource] = {
    DANGER_MAP_URI: UiResource(
        uri=DANGER_MAP_URI,
        name="Avalanche danger map",
        description=(
            "Interactive map of the day's peak avalanche danger rating per "
            "warning region, for one country or major region."
        ),
        filename="danger_map.html",
        csp={
            "connectDomains": [_BASEMAP_ORIGIN],
            "resourceDomains": [
                _MAPLIBRE_ORIGIN,
                _BASEMAP_ORIGIN,
                _SWISSTOPO_RASTER_ORIGIN,
                _OSM_RASTER_ORIGIN,
            ],
        },
    ),
}
