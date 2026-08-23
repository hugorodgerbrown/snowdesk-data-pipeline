"""
tests/regions/test_italy_fixture.py

Smoke tests that load apps/regions/fixtures/eaws_IT.json into the test database
and verify the expected row counts, FK relationships, and spot-check values.
Names are now resolved from EAWS it/en.json (no more placeholder IDs).
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from django.db.models import F

from apps.regions.models import MajorRegion, MicroRegion

# Pinned to one xdist worker so the module-scoped load below is paid once
# rather than once per worker that happens to receive one of these tests.
pytestmark = pytest.mark.xdist_group(name="eaws_it_fixture")


@pytest.fixture(scope="module", autouse=True)
def _loaded(load_eaws_fixture_once: Callable[[str], None]) -> None:
    """Load the country fixture once for this module (see tests/regions/conftest.py)."""
    load_eaws_fixture_once("apps/regions/fixtures/eaws_IT.json")


@pytest.mark.django_db
def test_italy_fixture_loads() -> None:
    """loaddata succeeds and inserts 7 + 58 + 124 rows."""
    assert MajorRegion.objects.filter(country="IT").count() == 7
    assert MicroRegion.objects.filter(subregion__major__country="IT").count() == 124


@pytest.mark.django_db
def test_italy_fixture_region_prefixes() -> None:
    """All seven Italian region L1 prefixes are present."""
    prefixes = set(
        MajorRegion.objects.filter(country="IT").values_list("prefix", flat=True)
    )
    assert prefixes == {
        "IT-21",
        "IT-23",
        "IT-25",
        "IT-32-BZ",
        "IT-32-TN",
        "IT-34",
        "IT-36",
    }


@pytest.mark.django_db
def test_italy_fixture_it32bz_spot_check() -> None:
    """IT-32-BZ-01 loads with the expected slug and canonical name."""
    region = MicroRegion.objects.get(region_id="IT-32-BZ-01")
    assert region.slug == "it-32-bz-01"
    # Name comes from EAWS it.json — no longer a placeholder ID.
    assert region.name != "IT-32-BZ-01"
    # 1:1 synthetic L2: subregion prefix == region_id
    assert region.subregion.prefix == "IT-32-BZ-01"
    assert region.subregion.major.prefix == "IT-32-BZ"


@pytest.mark.django_db
def test_italy_fixture_all_l1_have_boundary() -> None:
    """All 7 L1 MajorRegions carry a non-null boundary after fixture load."""
    majors = MajorRegion.objects.filter(country="IT")
    assert majors.count() == 7
    assert all(m.boundary is not None for m in majors)


@pytest.mark.django_db
def test_italy_fixture_fk_relationships() -> None:
    """Each L4 MicroRegion can navigate to its L1 MajorRegion via FKs."""
    for region in MicroRegion.objects.filter(subregion__major__country="IT"):
        assert region.subregion is not None
        assert region.major_region is not None
        assert region.major_region.country == "IT"


@pytest.mark.django_db
def test_italy_fixture_canonical_l1_names() -> None:
    """L1 MajorRegions have canonical EAWS names, not placeholder IDs.

    EAWS en.json uses the Italian regional name 'Piemonte' for IT-21 in
    both the Italian and English files — not 'Piedmont'. This is correct
    EAWS upstream behaviour.
    """
    it21 = MajorRegion.objects.get(prefix="IT-21")
    assert it21.name_native == "Piemonte"
    assert it21.name_en == "Piemonte"


@pytest.mark.django_db
def test_italy_fixture_no_placeholder_names() -> None:
    """No L4 MicroRegion name should equal its region_id (no more placeholders)."""
    placeholders = MicroRegion.objects.filter(
        subregion__major__country="IT",
        name=F("region_id"),
    )
    assert placeholders.count() == 0, (
        f"Found {placeholders.count()} placeholder names: "
        f"{list(placeholders.values_list('region_id', flat=True)[:5])}"
    )
