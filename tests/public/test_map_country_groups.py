"""
tests/public/test_map_country_groups.py — the layers menu's provider→country
routing, checked against its one JS declaration.

SNOW-658 merged the Austria and Italy rows into a single "ALBINA (AT, IT)"
row, because a row in that menu switches one bulletin PROVIDER on, and ALBINA
publishes the EUREGIO bulletin for both countries. Nothing below the menu
learned about providers: ``countryState``, the per-code localStorage keys and
``applyCountryFilters``'s filter are all still per-code, so one row now writes
two of them.

That routing is declared once, in ``COUNTRY_GROUPS`` (static/js/map_state.js),
and read directly by the two modules that load with the map bundle
(map.js, map_basemap_picker.js). ``map_layer_sync_status.js`` cannot read it:
home.html loads that module BEFORE the bundle, so the declaration is in its
temporal dead zone at parse time, and it is unit-tested on its own where the
identifier does not exist at all. It therefore reads each row's
``data-country-codes`` attribute instead — the DOM projection of the same
mapping.

Two projections of one fact is exactly the drift these dots cannot afford: a
sync dot that judges the wrong set of countries is a dot that lies about what
is available offline, which is the failure SNOW-524 built them to prevent. So
this module compares the rendered attributes against the JS declaration
directly, rather than asserting either against a third hardcoded copy.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.test import Client
from django.urls import reverse

_MAP_STATE_SOURCE = (
    Path(__file__).resolve().parents[2] / "static" / "js" / "map_state.js"
)

# ``const COUNTRY_GROUPS = { … };`` — the object literal's body.
_GROUPS_BLOCK_RE = re.compile(r"const COUNTRY_GROUPS = \{(.*?)\n\};", re.DOTALL)

# ``'country.albina': ['at', 'it'],`` — one entry inside that body.
_GROUP_ENTRY_RE = re.compile(r"'([^']+)':\s*\[([^\]]*)\]")

# ``data-overlay-key="country.ch" … data-country-codes="ch"`` — one country row
# of the rendered menu. The two attributes are adjacent in the template, and
# djangofmt keeps them so.
_ROW_RE = re.compile(
    r'data-overlay-key="(country\.[a-z]+)"\s+data-country-codes="([a-z ]+)"'
)


def _declared_groups() -> dict[str, list[str]]:
    """Parse ``COUNTRY_GROUPS`` out of static/js/map_state.js.

    Returns:
        Mapping of overlay key to the country codes it switches. Only the
        grouped keys appear — a key absent from the literal maps to the single
        code in its own suffix, which is what ``countryCodesFor`` does.

    """
    block = _GROUPS_BLOCK_RE.search(_MAP_STATE_SOURCE.read_text())
    assert block is not None, "COUNTRY_GROUPS not found in static/js/map_state.js"
    return {
        key: re.findall(r"'([a-z]+)'", codes)
        for key, codes in _GROUP_ENTRY_RE.findall(block.group(1))
    }


def _rendered_rows(client: Client) -> dict[str, list[str]]:
    """Read every country row's declared codes off the rendered map page.

    Args:
        client: The Django test client.

    Returns:
        Mapping of ``data-overlay-key`` to the codes in ``data-country-codes``.

    """
    # ``public:map`` 301s to the homepage, which is where the map (and its
    # layers menu) actually renders.
    response = client.get(reverse("public:home"))
    assert response.status_code == 200
    return {
        key: codes.split() for key, codes in _ROW_RE.findall(response.content.decode())
    }


@pytest.mark.django_db
class TestCountryRowCodes:
    """Every country row declares the codes the JS mapping says it switches."""

    def test_every_country_row_declares_its_codes(self, client: Client) -> None:
        """No country row may be missing the attribute the dots read."""
        rows = _rendered_rows(client)
        assert rows, "no country rows rendered in the layers menu"
        for key, codes in rows.items():
            assert codes, f"{key} declares no country codes"

    def test_rendered_codes_match_the_js_declaration(self, client: Client) -> None:
        """The DOM projection agrees with COUNTRY_GROUPS, entry for entry."""
        declared = _declared_groups()
        for key, codes in _rendered_rows(client).items():
            # A key absent from COUNTRY_GROUPS carries the single code in its
            # own suffix — countryCodesFor's fallback.
            expected = declared.get(key, [key.removeprefix("country.")])
            assert codes == expected, f"{key}: rendered {codes}, declared {expected}"

    def test_albina_is_one_row_covering_two_countries(self, client: Client) -> None:
        """The merge itself, stated once so its removal is a failing test."""
        rows = _rendered_rows(client)
        assert rows["country.albina"] == ["at", "it"]
        assert "country.at" not in rows
        assert "country.it" not in rows

    def test_no_code_is_claimed_by_two_rows(self, client: Client) -> None:
        """A code switched by two rows would make each row's dot ambiguous."""
        claimed: list[str] = []
        for codes in _rendered_rows(client).values():
            claimed.extend(codes)
        assert len(claimed) == len(set(claimed)), f"duplicate codes: {claimed}"
