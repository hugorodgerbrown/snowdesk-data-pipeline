"""
tests/regions/test_france_fixture.py

Smoke tests that load apps/regions/fixtures/eaws_FR.json into the test database
and verify the expected row counts, FK relationships, and spot-check values.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from apps.regions.models import MajorRegion, MicroRegion

# Pinned to one xdist worker so the module-scoped load below is paid once
# rather than once per worker that happens to receive one of these tests.
pytestmark = pytest.mark.xdist_group(name="eaws_fr_fixture")


@pytest.fixture(scope="module", autouse=True)
def _loaded(load_eaws_fixture_once: Callable[[str], None]) -> None:
    """Load the country fixture once for this module (see tests/regions/conftest.py)."""
    load_eaws_fixture_once("apps/regions/fixtures/eaws_FR.json")


@pytest.mark.django_db
def test_france_fixture_loads() -> None:
    """loaddata succeeds and inserts 4 + 4 + 35 rows."""
    assert MicroRegion.objects.filter(subregion__major__country="FR").count() == 35
    assert MajorRegion.objects.filter(country="FR").count() == 4


@pytest.mark.django_db
def test_france_fixture_fr68_spot_check() -> None:
    """FR-68 loads with the expected name from EAWS fr.json lookup."""
    region = MicroRegion.objects.get(region_id="FR-68")
    assert region.name == "Louchonnais"
    assert region.slug == "fr-68"


@pytest.mark.django_db
def test_france_fixture_fr01_spot_check() -> None:
    """FR-01 loads with the expected name from EAWS fr.json lookup."""
    region = MicroRegion.objects.get(region_id="FR-01")
    assert region.name == "Chablais"


@pytest.mark.django_db
def test_france_fixture_all_l1_have_boundary() -> None:
    """All 4 L1 MajorRegions carry a non-null boundary after fixture load."""
    majors = MajorRegion.objects.filter(country="FR")
    assert majors.count() == 4
    assert all(m.boundary is not None for m in majors)


@pytest.mark.django_db
def test_france_fixture_fk_relationships() -> None:
    """Each L4 MicroRegion can navigate to its L1 MajorRegion via FKs."""
    for region in MicroRegion.objects.filter(subregion__major__country="FR"):
        assert region.subregion is not None
        assert region.major_region is not None
        assert region.major_region.country == "FR"


@pytest.mark.django_db
def test_france_fixture_mountain_groupings() -> None:
    """The 4 mountains each map to the expected EAWS L1 prefix."""
    prefixes = set(
        MajorRegion.objects.filter(country="FR").values_list("prefix", flat=True)
    )
    assert prefixes == {"FR-1", "FR-2", "FR-3", "FR-4"}
