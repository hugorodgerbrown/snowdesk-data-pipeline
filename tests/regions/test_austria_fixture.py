"""
tests/regions/test_austria_fixture.py

Smoke tests that load regions/fixtures/eaws_AT.json into the test database
and verify the expected row counts, FK relationships, and spot-check values.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from regions.models import MajorRegion, MicroRegion


@pytest.mark.django_db
def test_austria_fixture_loads() -> None:
    """loaddata succeeds and inserts 7 + 111 + 153 rows."""
    call_command("loaddata", "regions/fixtures/eaws_AT.json", verbosity=0)

    assert MajorRegion.objects.filter(country="AT").count() == 7
    assert MicroRegion.objects.filter(subregion__major__country="AT").count() == 153


@pytest.mark.django_db
def test_austria_fixture_state_prefixes() -> None:
    """All seven Austrian state L1 prefixes are present."""
    call_command("loaddata", "regions/fixtures/eaws_AT.json", verbosity=0)

    prefixes = set(
        MajorRegion.objects.filter(country="AT").values_list("prefix", flat=True)
    )
    assert prefixes == {"AT-02", "AT-03", "AT-04", "AT-05", "AT-06", "AT-07", "AT-08"}


@pytest.mark.django_db
def test_austria_fixture_spot_check() -> None:
    """AT-02-01 loads as a MicroRegion with the expected slug and region_id."""
    call_command("loaddata", "regions/fixtures/eaws_AT.json", verbosity=0)

    region = MicroRegion.objects.get(region_id="AT-02-01")
    assert region.slug == "at-02-01"
    assert region.name == "AT-02-01"


@pytest.mark.django_db
def test_austria_fixture_all_l1_have_boundary() -> None:
    """All 7 L1 MajorRegions carry a non-null boundary after fixture load."""
    call_command("loaddata", "regions/fixtures/eaws_AT.json", verbosity=0)

    majors = MajorRegion.objects.filter(country="AT")
    assert majors.count() == 7
    assert all(m.boundary is not None for m in majors)


@pytest.mark.django_db
def test_austria_fixture_fk_relationships() -> None:
    """Each L4 MicroRegion can navigate to its L1 MajorRegion via FKs."""
    call_command("loaddata", "regions/fixtures/eaws_AT.json", verbosity=0)

    for region in MicroRegion.objects.filter(subregion__major__country="AT"):
        assert region.subregion is not None
        assert region.major_region is not None
        assert region.major_region.country == "AT"
