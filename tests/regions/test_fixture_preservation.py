"""
tests/regions/test_fixture_preservation.py — a rebuild must not drop
per-estate fields the upstream source does not carry.

SNOW-771 put ``centroid_elevation_m`` in the EAWS fixtures: one Open-Meteo
call per region, resolved once, so every environment derives its region
centroids offline. The ``build_*_fixture`` commands rebuild each L4 entry's
fields from the upstream EAWS GeoJSON, which knows nothing about it — so
without ``carry_forward_preserved_fields`` a rebuild would silently drop all
461 elevations, and nothing downstream would say so.

That is the same shape as the deploy-time wipe SNOW-771 fixed, one layer up:
a fresh dict written over committed data, losing a column the writer never
knew about. Unlike ``basemap_download``, which ``compute_basemap_download``
recomputes on every deploy, nothing restores this one without a network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.regions.fixture_utils import (
    PRESERVED_L4_FIELDS,
    carry_forward_preserved_fields,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "apps" / "regions" / "fixtures"
_EAWS = ["eaws_CH.json", "eaws_FR.json", "eaws_AT.json", "eaws_IT.json"]


def _rebuilt_entry(region_id: str) -> dict[str, Any]:
    """Return an entry shaped like a fresh build — no preserved fields."""
    return {
        "model": "regions.microregion",
        "fields": {
            "region_id": region_id,
            "name": "Rebuilt",
            "boundary": {
                "type": "Polygon",
                "coordinates": [[[7, 46], [8, 46], [8, 47], [7, 46]]],
            },
        },
    }


class TestCarryForwardPreservedFields:
    """The helper the four build commands call before writing."""

    @pytest.mark.parametrize("fixture_name", _EAWS)
    def test_a_rebuild_keeps_every_committed_elevation(self, fixture_name: str) -> None:
        """Simulate a full rebuild and assert nothing is lost."""
        path = FIXTURES_DIR / fixture_name
        committed = json.loads(path.read_text(encoding="utf-8"))
        micro = [e for e in committed if e["model"] == "regions.microregion"]
        assert micro

        rebuilt = [_rebuilt_entry(e["fields"]["region_id"]) for e in micro]
        carry_forward_preserved_fields(path, rebuilt)

        before = {
            e["fields"]["region_id"]: e["fields"].get("centroid_elevation_m")
            for e in micro
        }
        after = {
            e["fields"]["region_id"]: e["fields"].get("centroid_elevation_m")
            for e in rebuilt
        }
        assert after == before

    def test_an_unpreserved_field_is_not_carried(self) -> None:
        """Only the declared set moves — this is not a blanket merge.

        A blanket merge would resurrect fields the upstream source
        deliberately changed, which is the opposite of what a rebuild is
        for.
        """
        path = FIXTURES_DIR / "eaws_CH.json"
        committed = json.loads(path.read_text(encoding="utf-8"))
        first = next(e for e in committed if e["model"] == "regions.microregion")
        rebuilt = [_rebuilt_entry(first["fields"]["region_id"])]

        carry_forward_preserved_fields(path, rebuilt)

        assert rebuilt[0]["fields"]["name"] == "Rebuilt"
        assert "centre" not in rebuilt[0]["fields"]

    def test_a_new_region_without_a_committed_value_is_left_alone(self) -> None:
        """A region added by this rebuild has nothing to carry forward."""
        path = FIXTURES_DIR / "eaws_CH.json"
        rebuilt = [_rebuilt_entry("CH-9999")]

        carry_forward_preserved_fields(path, rebuilt)

        assert "centroid_elevation_m" not in rebuilt[0]["fields"]

    def test_the_preserved_set_is_declared_not_inferred(self) -> None:
        """Guards the constant against being emptied by accident."""
        assert "centroid_elevation_m" in PRESERVED_L4_FIELDS
