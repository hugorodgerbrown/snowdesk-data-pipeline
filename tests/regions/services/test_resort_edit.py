"""
tests/regions/services/test_resort_edit.py — Tests for apps.regions.services.resort_edit.

Covers the two rules the resort write paths share (SNOW-1401):
  canton_for_region — the sibling resorts vote; ties break alphabetically;
                      an empty region has no answer.
  nearby_resorts    — pinned resorts within the radius, nearest first;
                      unpinned and distant ones left out.
"""

from __future__ import annotations

import pytest

from apps.regions.services.resort_edit import canton_for_region, nearby_resorts
from tests.factories import MicroRegionFactory, ResortFactory


@pytest.mark.django_db
class TestCantonForRegion:
    """canton_for_region: the canton a region's resorts agree on."""

    def test_most_common_wins(self) -> None:
        """The majority canton is returned; blanks do not vote."""
        region = MicroRegionFactory.create()
        for canton in ("VS", "VS", "BE", " "):
            ResortFactory.create(region=region, canton=canton)
        assert canton_for_region(region) == "VS"

    def test_tie_breaks_alphabetically(self) -> None:
        """A tie returns the alphabetically first canton, reproducibly."""
        region = MicroRegionFactory.create()
        ResortFactory.create(region=region, canton="VS")
        ResortFactory.create(region=region, canton="BE")
        assert canton_for_region(region) == "BE"

    def test_empty_region(self) -> None:
        """A region with no resorts has nothing to inherit."""
        assert canton_for_region(MicroRegionFactory.create()) == ""


@pytest.mark.django_db
class TestNearbyResorts:
    """nearby_resorts: pinned resorts within a radius, nearest first."""

    def test_within_radius_nearest_first(self) -> None:
        """Two resorts in range come back nearest first, with distances."""
        further = ResortFactory.create(name="Further", latitude=46.21, longitude=7.2)
        nearer = ResortFactory.create(name="Nearer", latitude=46.201, longitude=7.2)
        ResortFactory.create(name="Far", latitude=46.5, longitude=7.2)
        ResortFactory.create(name="Unpinned")
        hits = nearby_resorts(46.2, 7.2)
        assert [hit.resort for hit in hits] == [nearer, further]
        assert hits[0].distance_km < hits[1].distance_km <= 2.0

    def test_radius_is_honoured(self) -> None:
        """A smaller radius leaves out a resort the default would include."""
        ResortFactory.create(latitude=46.21, longitude=7.2)
        assert nearby_resorts(46.2, 7.2, radius_km=0.5) == []
