"""
tests/bulletins/test_region_bulletin_model.py — Tests for RegionBulletin model.

Covers to_string() and the __str__ delegation to it (SNOW-350).
"""

from __future__ import annotations

import pytest

from tests.factories import RegionBulletinFactory


@pytest.mark.django_db
class TestRegionBulletinToString:
    """Tests for RegionBulletin.to_string and its __str__ delegation."""

    def test_to_string_format(self) -> None:
        """to_string() joins the bulletin id and the region id with an arrow."""
        rb = RegionBulletinFactory.create()

        assert rb.to_string() == f"{rb.bulletin.bulletin_id} ↔ {rb.region.region_id}"

    def test_str_delegates_to_to_string(self) -> None:
        """str(rb) is exactly to_string()."""
        rb = RegionBulletinFactory.create()

        assert str(rb) == rb.to_string()
