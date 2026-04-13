"""
tests/pipeline/models/test_resort.py — Tests for the Resort model.

Covers model creation, ordering, string representation, cascade
deletion, natural key support on Region, and fixture loading.
"""

import pytest
from django.core.management import call_command

from pipeline.models import Region, Resort
from tests.factories import RegionFactory, ResortFactory


@pytest.mark.django_db
class TestResortModel:
    """Tests for the Resort model."""

    def test_str_returns_name_and_region_id(self) -> None:
        """String representation includes the resort name and region_id."""
        resort = ResortFactory.create(name="Zermatt")
        assert str(resort) == f"Zermatt ({resort.region.region_id})"

    def test_default_ordering_is_by_name(self) -> None:
        """Resorts are ordered alphabetically by name."""
        region = RegionFactory.create()
        ResortFactory.create(name="Zermatt", region=region)
        ResortFactory.create(name="Arosa", region=region)
        ResortFactory.create(name="Davos", region=region)
        names = list(Resort.objects.values_list("name", flat=True))
        assert names == ["Arosa", "Davos", "Zermatt"]

    def test_region_cascade_deletes_resort(self) -> None:
        """Deleting a region cascades to its resorts."""
        resort = ResortFactory.create()
        region_pk = resort.region.pk
        Region.objects.filter(pk=region_pk).delete()
        assert not Resort.objects.filter(pk=resort.pk).exists()

    def test_name_alt_blank_allowed(self) -> None:
        """A resort can be created with an empty name_alt."""
        resort = ResortFactory.create(name_alt="")
        resort.full_clean()
        assert resort.name_alt == ""

    def test_notes_blank_allowed(self) -> None:
        """A resort can be created with empty notes."""
        resort = ResortFactory.create(notes="")
        resort.full_clean()
        assert resort.notes == ""

    def test_factory_creates_valid_instance(self) -> None:
        """The default factory produces a saved, valid Resort."""
        resort = ResortFactory.create()
        assert resort.pk is not None
        resort.full_clean()


@pytest.mark.django_db
class TestRegionNaturalKey:
    """Tests for Region natural key support (used by fixture loading)."""

    def test_natural_key_returns_region_id_tuple(self) -> None:
        """natural_key() returns a one-element tuple of region_id."""
        region = RegionFactory.create(region_id="CH-9999")
        assert region.natural_key() == ("CH-9999",)

    def test_get_by_natural_key_returns_correct_region(self) -> None:
        """get_by_natural_key() looks up by region_id."""
        region = RegionFactory.create(region_id="CH-8888")
        found = Region.objects.get_by_natural_key("CH-8888")
        assert found.pk == region.pk


@pytest.mark.django_db
class TestResortFixture:
    """Tests for the resorts.json fixture."""

    def test_fixture_loads_successfully(self) -> None:
        """The resorts fixture loads without errors when regions exist."""
        # The fixture references specific region_ids via natural keys.
        # Create all referenced regions so the FK lookup succeeds.
        region_ids = set()
        import json
        from pathlib import Path

        fixture_path = Path("pipeline/fixtures/resorts.json")
        data = json.loads(fixture_path.read_text())
        for entry in data:
            region_ids.add(entry["fields"]["region"][0])

        for rid in region_ids:
            RegionFactory.create(region_id=rid, name=f"Region {rid}")

        call_command("loaddata", "resorts", verbosity=0)
        assert Resort.objects.count() == len(data)
