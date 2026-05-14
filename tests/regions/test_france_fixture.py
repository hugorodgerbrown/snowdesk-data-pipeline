"""
tests/regions/test_france_fixture.py

Smoke tests that load regions/fixtures/france.json into the test database
and verify the expected row counts, FK relationships, and spot-check values.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from regions.models import MajorRegion, MicroRegion


@pytest.mark.django_db
def test_france_fixture_loads() -> None:
    """loaddata succeeds and inserts 4 + 4 + 35 rows."""
    call_command("loaddata", "regions/fixtures/france.json", verbosity=0)

    assert MicroRegion.objects.filter(subregion__major__country="FR").count() == 35
    assert MajorRegion.objects.filter(country="FR").count() == 4


@pytest.mark.django_db
def test_france_fixture_fr68_spot_check() -> None:
    """FR-68 loads with the expected name from fr_names.json."""
    call_command("loaddata", "regions/fixtures/france.json", verbosity=0)

    region = MicroRegion.objects.get(region_id="FR-68")
    assert region.name == "Louchonnais"
    assert region.slug == "fr-68"


@pytest.mark.django_db
def test_france_fixture_all_l1_have_boundary() -> None:
    """All 4 L1 MajorRegions carry a non-null boundary after fixture load."""
    call_command("loaddata", "regions/fixtures/france.json", verbosity=0)

    majors = MajorRegion.objects.filter(country="FR")
    assert majors.count() == 4
    assert all(m.boundary is not None for m in majors)


@pytest.mark.django_db
def test_france_fixture_fk_relationships() -> None:
    """Each L4 MicroRegion can navigate to its L1 MajorRegion via FKs."""
    call_command("loaddata", "regions/fixtures/france.json", verbosity=0)

    for region in MicroRegion.objects.filter(subregion__major__country="FR"):
        assert region.subregion is not None
        assert region.major_region is not None
        assert region.major_region.country == "FR"


@pytest.mark.django_db
def test_france_fixture_mountain_groupings() -> None:
    """The 4 mountains each map to the expected EAWS L1 prefix."""
    call_command("loaddata", "regions/fixtures/france.json", verbosity=0)

    prefixes = set(
        MajorRegion.objects.filter(country="FR").values_list("prefix", flat=True)
    )
    assert prefixes == {"FR-1", "FR-2", "FR-3", "FR-4"}
