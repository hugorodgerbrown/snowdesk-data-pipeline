"""
tests/mcp_server/test_ui_resources.py — Tests for apps.mcp_server.ui_resources.

Checks the registry's ``resources/list`` and ``resources/read`` shapes
against the MCP Apps spec (2026-01-26): a ``ui://`` URI, the
``text/html;profile=mcp-app`` MIME type, and CSP metadata on the content
item. The protocol-level round trip is in ``test_protocol.py``.
"""

from __future__ import annotations

from apps.mcp_server.ui_resources import (
    DANGER_MAP_URI,
    RESOURCE_MIME_TYPE,
    UI_RESOURCES,
)


def test_every_resource_uses_the_ui_scheme_and_its_own_key() -> None:
    """Each registry key is its resource's ``ui://`` URI."""
    for uri, resource in UI_RESOURCES.items():
        assert uri.startswith("ui://")
        assert resource.uri == uri


def test_listing_omits_the_html() -> None:
    """The listing is metadata only — the HTML comes from ``resources/read``."""
    listing = UI_RESOURCES[DANGER_MAP_URI].listing()
    assert listing["mimeType"] == RESOURCE_MIME_TYPE
    assert "text" not in listing


def test_contents_carries_html_csp_and_border() -> None:
    """The content item holds the document and its ``_meta.ui`` block."""
    contents = UI_RESOURCES[DANGER_MAP_URI].contents()
    assert contents["mimeType"] == RESOURCE_MIME_TYPE
    assert "<title>Avalanche danger map</title>" in contents["text"]
    ui = contents["_meta"]["ui"]
    assert ui["prefersBorder"] is True
    assert "https://cdn.jsdelivr.net" in ui["csp"]["resourceDomains"]


def test_view_loads_only_origins_its_csp_declares() -> None:
    """Every https:// origin the view's src/href attributes load is in the CSP."""
    resource = UI_RESOURCES[DANGER_MAP_URI]
    html = resource.contents()["text"]
    declared = set(resource.csp["resourceDomains"])
    for marker in ('src="https://', 'href="https://'):
        for chunk in html.split(marker)[1:]:
            origin = "https://" + chunk.split("/", 1)[0]
            assert origin in declared
