"""
tests/regions/models/test_major_sub_region.py — Tests for EAWS region hierarchy.

Covers the L1 (``MajorRegion``) and L2 (``SubRegion``) reference models:
factory validity, natural keys, string representations, the
``MicroRegion.major_region`` traversal, and fixture loading.
"""

import pytest
from django.core.management import call_command

from regions.models import MajorRegion, MicroRegion, SubRegion
from tests.factories import (
    MajorRegionFactory,
    MicroRegionFactory,
    SubRegionFactory,
)


@pytest.mark.django_db
class TestMajorRegion:
    """Tests for the MajorRegion (L1) model."""

    def test_str_is_prefix_and_native_name(self) -> None:
        """String representation is '<prefix> — <name_native>'."""
        major = MajorRegionFactory.create(
            prefix="CH-4", name_native="Wallis", name_en="Valais"
        )
        assert str(major) == "CH-4 — Wallis"

    def test_to_string(self) -> None:
        """to_string() returns the canonical prefix + native-name form."""
        major = MajorRegionFactory.create(prefix="CH-6", name_native="Tessin")
        assert major.to_string() == "CH-6 Tessin"

    def test_ordered_by_prefix(self) -> None:
        """Default ordering is ascending by prefix."""
        MajorRegionFactory.create(prefix="CH-3", name_native="Östliche Voralpen")
        MajorRegionFactory.create(prefix="CH-1", name_native="Westliche Voralpen")
        prefixes = list(MajorRegion.objects.values_list("prefix", flat=True))
        assert prefixes == sorted(prefixes)

    def test_natural_key_round_trip(self) -> None:
        """natural_key() / get_by_natural_key() round-trip the prefix."""
        major = MajorRegionFactory.create(prefix="CH-5")
        assert major.natural_key() == ("CH-5",)
        assert MajorRegion.objects.get_by_natural_key("CH-5").pk == major.pk

    def test_factory_creates_valid_instance(self) -> None:
        """The default factory produces a saved, valid major region."""
        major = MajorRegionFactory.create()
        assert major.pk is not None
        major.full_clean()


@pytest.mark.django_db
class TestSubRegion:
    """Tests for the SubRegion (L2) model."""

    def test_str_is_prefix_and_native_name(self) -> None:
        """String representation is '<prefix> — <name_native>'."""
        sub = SubRegionFactory.create(
            prefix="CH-41", name_native="Bas-Valais", name_en="Lower Valais"
        )
        assert str(sub) == "CH-41 — Bas-Valais"

    def test_to_string(self) -> None:
        """to_string() returns the canonical prefix + native-name form."""
        sub = SubRegionFactory.create(prefix="CH-42", name_native="Oberwallis")
        assert sub.to_string() == "CH-42 Oberwallis"

    def test_major_fk_is_protected(self) -> None:
        """Deleting a major region with subregions raises ProtectedError."""
        from django.db.models import ProtectedError

        major = MajorRegionFactory.create(prefix="CH-4")
        SubRegionFactory.create(prefix="CH-41", major=major)
        with pytest.raises(ProtectedError):
            major.delete()

    def test_reverse_related_name(self) -> None:
        """``major.subregions`` exposes the reverse FK relation."""
        major = MajorRegionFactory.create(prefix="CH-4")
        SubRegionFactory.create(prefix="CH-41", major=major)
        SubRegionFactory.create(prefix="CH-42", major=major)
        assert major.subregions.count() == 2

    def test_natural_key_round_trip(self) -> None:
        """natural_key() / get_by_natural_key() round-trip the prefix."""
        sub = SubRegionFactory.create(prefix="CH-52")
        assert sub.natural_key() == ("CH-52",)
        assert SubRegion.objects.get_by_natural_key("CH-52").pk == sub.pk


@pytest.mark.django_db
class TestMicroRegionSubregionFK:
    """Tests for MicroRegion.subregion and the major_region traversal."""

    def test_subregion_is_required(self) -> None:
        """Creating a MicroRegion without a subregion raises IntegrityError."""
        from django.db import IntegrityError, transaction

        with pytest.raises(IntegrityError):  # noqa: PT012
            with transaction.atomic():
                MicroRegion.objects.create(
                    region_id="CH-8888", name="No Parent", slug="ch-8888"
                )

    def test_major_region_traversal(self) -> None:
        """``region.major_region`` returns the grand-parent L1 region."""
        major = MajorRegionFactory.create(prefix="CH-4")
        sub = SubRegionFactory.create(prefix="CH-41", major=major)
        region = MicroRegionFactory.create(region_id="CH-4115", subregion=sub)
        assert region.major_region.pk == major.pk

    def test_reverse_related_name_on_subregion(self) -> None:
        """``subregion.micro_regions`` exposes the reverse FK relation."""
        sub = SubRegionFactory.create(prefix="CH-41")
        MicroRegionFactory.create(region_id="CH-4115", subregion=sub)
        MicroRegionFactory.create(region_id="CH-4116", subregion=sub)
        assert sub.micro_regions.count() == 2

    def test_subregion_protected_delete(self) -> None:
        """Deleting a subregion with micro-regions raises ProtectedError."""
        from django.db.models import ProtectedError

        sub = SubRegionFactory.create(prefix="CH-41")
        MicroRegionFactory.create(region_id="CH-4115", subregion=sub)
        with pytest.raises(ProtectedError):
            sub.delete()


@pytest.mark.django_db
class TestEawsFixtures:
    """Tests for the EAWS reference fixtures (consolidated eaws_ch.json)."""

    def test_major_fixture_loads(self) -> None:
        """eaws_ch.json loads cleanly and populates MajorRegion rows.

        Note: migration 0012 already loaded the EAWS fixtures during test DB
        setup, so ``loaddata`` here is a no-op idempotency check.
        """
        call_command("loaddata", "regions/fixtures/eaws_ch.json", verbosity=0)
        assert MajorRegion.objects.count() >= 9
        assert MajorRegion.objects.filter(prefix="CH-4").exists()

    def test_sub_fixture_loads(self) -> None:
        """eaws_ch.json loads cleanly and links SubRegion rows to MajorRegion."""
        call_command("loaddata", "regions/fixtures/eaws_ch.json", verbosity=0)
        assert SubRegion.objects.count() >= 21
        sub = SubRegion.objects.get(prefix="CH-41")
        assert sub.major.prefix == "CH-4"

    def test_regions_fixture_links_to_subregions(self) -> None:
        """Loading eaws_ch.json populates subregion FKs via natural key."""
        call_command("loaddata", "regions/fixtures/eaws_ch.json", verbosity=0)
        region = MicroRegion.objects.get(region_id="CH-4115")
        assert region.subregion is not None
        assert region.subregion.prefix == "CH-41"
        assert region.major_region.prefix == "CH-4"
