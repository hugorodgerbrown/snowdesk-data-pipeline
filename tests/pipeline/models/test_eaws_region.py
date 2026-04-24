"""
tests/pipeline/models/test_eaws_region.py — Tests for EAWS region hierarchy.

Covers the L1 (``EawsMajorRegion``) and L2 (``EawsSubRegion``) reference
models: factory validity, natural keys, string representations, the
``Region.major_region`` traversal, and fixture loading.
"""

import pytest
from django.core.management import call_command

from pipeline.models import EawsMajorRegion, EawsSubRegion, Region
from tests.factories import (
    EawsMajorRegionFactory,
    EawsSubRegionFactory,
    RegionFactory,
)


@pytest.mark.django_db
class TestEawsMajorRegion:
    """Tests for the EawsMajorRegion (L1) model."""

    def test_str_is_prefix_and_native_name(self) -> None:
        """String representation is '<prefix> — <name_native>'."""
        major = EawsMajorRegionFactory.create(
            prefix="CH-4", name_native="Wallis", name_en="Valais"
        )
        assert str(major) == "CH-4 — Wallis"

    def test_to_string(self) -> None:
        """to_string() returns the canonical prefix + native-name form."""
        major = EawsMajorRegionFactory.create(prefix="CH-6", name_native="Tessin")
        assert major.to_string() == "CH-6 Tessin"

    def test_ordered_by_prefix(self) -> None:
        """Default ordering is ascending by prefix."""
        EawsMajorRegionFactory.create(prefix="CH-3", name_native="Östliche Voralpen")
        EawsMajorRegionFactory.create(prefix="CH-1", name_native="Westliche Voralpen")
        prefixes = list(EawsMajorRegion.objects.values_list("prefix", flat=True))
        assert prefixes == sorted(prefixes)

    def test_natural_key_round_trip(self) -> None:
        """natural_key() / get_by_natural_key() round-trip the prefix."""
        major = EawsMajorRegionFactory.create(prefix="CH-5")
        assert major.natural_key() == ("CH-5",)
        assert EawsMajorRegion.objects.get_by_natural_key("CH-5").pk == major.pk

    def test_factory_creates_valid_instance(self) -> None:
        """The default factory produces a saved, valid major region."""
        major = EawsMajorRegionFactory.create()
        assert major.pk is not None
        major.full_clean()


@pytest.mark.django_db
class TestEawsSubRegion:
    """Tests for the EawsSubRegion (L2) model."""

    def test_str_is_prefix_and_native_name(self) -> None:
        """String representation is '<prefix> — <name_native>'."""
        sub = EawsSubRegionFactory.create(
            prefix="CH-41", name_native="Bas-Valais", name_en="Lower Valais"
        )
        assert str(sub) == "CH-41 — Bas-Valais"

    def test_to_string(self) -> None:
        """to_string() returns the canonical prefix + native-name form."""
        sub = EawsSubRegionFactory.create(prefix="CH-42", name_native="Oberwallis")
        assert sub.to_string() == "CH-42 Oberwallis"

    def test_major_fk_is_protected(self) -> None:
        """Deleting a major region with subregions raises ProtectedError."""
        from django.db.models import ProtectedError

        major = EawsMajorRegionFactory.create(prefix="CH-4")
        EawsSubRegionFactory.create(prefix="CH-41", major=major)
        with pytest.raises(ProtectedError):
            major.delete()

    def test_reverse_related_name(self) -> None:
        """``major.subregions`` exposes the reverse FK relation."""
        major = EawsMajorRegionFactory.create(prefix="CH-4")
        EawsSubRegionFactory.create(prefix="CH-41", major=major)
        EawsSubRegionFactory.create(prefix="CH-42", major=major)
        assert major.subregions.count() == 2

    def test_natural_key_round_trip(self) -> None:
        """natural_key() / get_by_natural_key() round-trip the prefix."""
        sub = EawsSubRegionFactory.create(prefix="CH-52")
        assert sub.natural_key() == ("CH-52",)
        assert EawsSubRegion.objects.get_by_natural_key("CH-52").pk == sub.pk


@pytest.mark.django_db
class TestRegionSubregionFK:
    """Tests for Region.subregion and the major_region traversal."""

    def test_subregion_is_required(self) -> None:
        """Creating a Region without a subregion raises IntegrityError."""
        from django.db import IntegrityError, transaction

        with pytest.raises(IntegrityError):  # noqa: PT012
            with transaction.atomic():
                Region.objects.create(
                    region_id="CH-8888", name="No Parent", slug="ch-8888"
                )

    def test_major_region_traversal(self) -> None:
        """``region.major_region`` returns the grand-parent L1 region."""
        major = EawsMajorRegionFactory.create(prefix="CH-4")
        sub = EawsSubRegionFactory.create(prefix="CH-41", major=major)
        region = RegionFactory.create(region_id="CH-4115", subregion=sub)
        assert region.major_region.pk == major.pk

    def test_reverse_related_name_on_subregion(self) -> None:
        """``subregion.micro_regions`` exposes the reverse FK relation."""
        sub = EawsSubRegionFactory.create(prefix="CH-41")
        RegionFactory.create(region_id="CH-4115", subregion=sub)
        RegionFactory.create(region_id="CH-4116", subregion=sub)
        assert sub.micro_regions.count() == 2

    def test_subregion_protected_delete(self) -> None:
        """Deleting a subregion with micro-regions raises ProtectedError."""
        from django.db.models import ProtectedError

        sub = EawsSubRegionFactory.create(prefix="CH-41")
        RegionFactory.create(region_id="CH-4115", subregion=sub)
        with pytest.raises(ProtectedError):
            sub.delete()


@pytest.mark.django_db
class TestEawsFixtures:
    """Tests for the EAWS L1 and L2 reference fixtures."""

    def test_major_fixture_loads(self) -> None:
        """eaws_major_regions.json loads cleanly.

        Note: migration 0012 already loaded this fixture during test DB
        setup, so ``loaddata`` here is a no-op idempotency check.
        """
        call_command("loaddata", "eaws_major_regions", verbosity=0)
        assert EawsMajorRegion.objects.count() >= 9
        assert EawsMajorRegion.objects.filter(prefix="CH-4").exists()

    def test_sub_fixture_loads(self) -> None:
        """eaws_sub_regions.json loads cleanly and links to majors."""
        call_command("loaddata", "eaws_sub_regions", verbosity=0)
        assert EawsSubRegion.objects.count() >= 21
        sub = EawsSubRegion.objects.get(prefix="CH-41")
        assert sub.major.prefix == "CH-4"

    def test_regions_fixture_links_to_subregions(self) -> None:
        """Loading regions.json populates subregion FKs via natural key."""
        call_command("loaddata", "regions", verbosity=0)
        region = Region.objects.get(region_id="CH-4115")
        assert region.subregion is not None
        assert region.subregion.prefix == "CH-41"
        assert region.major_region.prefix == "CH-4"
