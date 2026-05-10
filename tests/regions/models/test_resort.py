"""
tests/regions/models/test_resort.py — Tests for the Resort model.

Covers model creation, ordering, string representation, cascade
deletion, natural key support on MicroRegion, and fixture loading.
"""

import pytest
from django.core.management import call_command

from regions.models import MicroRegion, Resort
from tests.factories import MicroRegionFactory, ResortFactory


@pytest.mark.django_db
class TestResortModel:
    """Tests for the Resort model."""

    def test_str_returns_name_and_region_id(self) -> None:
        """String representation includes the resort name and region_id."""
        resort = ResortFactory.create(name="Zermatt")
        assert str(resort) == f"Zermatt ({resort.region.region_id})"

    def test_default_ordering_is_by_name(self) -> None:
        """Resorts are ordered alphabetically by name."""
        region = MicroRegionFactory.create()
        ResortFactory.create(name="Zermatt", region=region)
        ResortFactory.create(name="Arosa", region=region)
        ResortFactory.create(name="Davos", region=region)
        names = list(Resort.objects.values_list("name", flat=True))
        assert names == ["Arosa", "Davos", "Zermatt"]

    def test_region_cascade_deletes_resort(self) -> None:
        """Deleting a region cascades to its resorts."""
        resort = ResortFactory.create()
        region_pk = resort.region.pk
        MicroRegion.objects.filter(pk=region_pk).delete()
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
class TestResortQueryset:
    """Tests for the custom ResortQuerySet methods (SNOW-74)."""

    def test_geocoded_excludes_resort_missing_latitude(self) -> None:
        """A resort with longitude but no latitude is not geocoded()."""
        ResortFactory.create(name="A", latitude=None, longitude=7.0)
        assert Resort.objects.geocoded().count() == 0

    def test_geocoded_excludes_resort_missing_longitude(self) -> None:
        """A resort with latitude but no longitude is not geocoded()."""
        ResortFactory.create(name="A", latitude=46.0, longitude=None)
        assert Resort.objects.geocoded().count() == 0

    def test_geocoded_includes_fully_set_resort(self) -> None:
        """A resort with both latitude and longitude is geocoded()."""
        resort = ResortFactory.create(name="A", latitude=46.0, longitude=7.0)
        assert list(Resort.objects.geocoded()) == [resort]

    def test_geocoded_ignores_needs_review_flag(self) -> None:
        """needs_review does not gate the geocoded() result."""
        ResortFactory.create(
            name="A",
            latitude=46.0,
            longitude=7.0,
            needs_review=True,
        )
        assert Resort.objects.geocoded().count() == 1

    def test_needs_geocoding_includes_unset_resort(self) -> None:
        """A resort missing coords appears in needs_geocoding()."""
        resort = ResortFactory.create(name="A")
        assert list(Resort.objects.needs_geocoding()) == [resort]

    def test_needs_geocoding_includes_review_flagged_resort(self) -> None:
        """A geocoded resort flagged for review is in needs_geocoding()."""
        resort = ResortFactory.create(
            name="A",
            latitude=46.0,
            longitude=7.0,
            needs_review=True,
        )
        assert list(Resort.objects.needs_geocoding()) == [resort]

    def test_needs_geocoding_excludes_clean_geocoded_resort(self) -> None:
        """A geocoded resort with needs_review=False is excluded."""
        ResortFactory.create(name="A", latitude=46.0, longitude=7.0)
        assert Resort.objects.needs_geocoding().count() == 0


@pytest.mark.django_db
class TestRegionNaturalKey:
    """Tests for MicroRegion natural key support (used by fixture loading)."""

    def test_natural_key_returns_region_id_tuple(self) -> None:
        """natural_key() returns a one-element tuple of region_id."""
        region = MicroRegionFactory.create(region_id="CH-9999")
        assert region.natural_key() == ("CH-9999",)

    def test_get_by_natural_key_returns_correct_region(self) -> None:
        """get_by_natural_key() looks up by region_id."""
        region = MicroRegionFactory.create(region_id="CH-8888")
        found = MicroRegion.objects.get_by_natural_key("CH-8888")
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

        fixture_path = Path("regions/fixtures/resorts.json")
        data = json.loads(fixture_path.read_text())
        for entry in data:
            region_ids.add(entry["fields"]["region"][0])

        for rid in region_ids:
            MicroRegionFactory.create(region_id=rid, name=f"Region {rid}")

        call_command("loaddata", "resorts", verbosity=0)
        assert Resort.objects.count() == len(data)
