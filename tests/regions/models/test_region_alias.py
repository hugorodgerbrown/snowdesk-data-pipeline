"""
tests/regions/models/test_region_alias.py — Tests for the RegionAlias model.

Covers string representation, the unique-together constraint on
(region, alias_text), natural-key round trip, and default ordering.
"""

import pytest
from django.db import IntegrityError, transaction

from regions.models import RegionAlias
from tests.factories import MicroRegionFactory, RegionAliasFactory


@pytest.mark.django_db
class TestRegionAliasStr:
    """Tests for RegionAlias.__str__ and to_string()."""

    def test_str_includes_alias_and_region_id(self) -> None:
        """String representation is '<alias_text> → <region_id>'."""
        region = MicroRegionFactory.create(region_id="CH-4121")
        alias = RegionAliasFactory.create(region=region, alias_text="Sitten")
        assert str(alias) == "Sitten → CH-4121"

    def test_to_string(self) -> None:
        """to_string() returns the same canonical form as __str__."""
        region = MicroRegionFactory.create(region_id="CH-4215")
        alias = RegionAliasFactory.create(region=region, alias_text="Loèche")
        assert alias.to_string() == "Loèche → CH-4215"


@pytest.mark.django_db
class TestRegionAliasUniqueConstraint:
    """Tests for the unique_region_alias constraint."""

    def test_duplicate_region_and_alias_text_rejected(self) -> None:
        """The same (region, alias_text) pair cannot be created twice."""
        region = MicroRegionFactory.create()
        RegionAliasFactory.create(region=region, alias_text="Duplicate")
        with pytest.raises(IntegrityError):  # noqa: PT012
            with transaction.atomic():
                RegionAliasFactory.create(region=region, alias_text="Duplicate")

    def test_same_alias_text_on_different_regions_allowed(self) -> None:
        """The same alias_text may be reused for a different region."""
        first = MicroRegionFactory.create()
        second = MicroRegionFactory.create()
        RegionAliasFactory.create(region=first, alias_text="Shared")
        RegionAliasFactory.create(region=second, alias_text="Shared")
        assert RegionAlias.objects.filter(alias_text="Shared").count() == 2


@pytest.mark.django_db
class TestRegionAliasNaturalKey:
    """Tests for RegionAlias natural key support."""

    def test_natural_key_returns_tuple(self) -> None:
        """natural_key() returns (region_id, alias_text)."""
        region = MicroRegionFactory.create(region_id="CH-8212")
        alias = RegionAliasFactory.create(region=region, alias_text="Bienne")
        assert alias.natural_key() == ("CH-8212", "Bienne")

    def test_get_by_natural_key_returns_correct_alias(self) -> None:
        """get_by_natural_key() looks up by (region_id, alias_text)."""
        region = MicroRegionFactory.create(region_id="CH-8224")
        alias = RegionAliasFactory.create(region=region, alias_text="Delémont")
        found = RegionAlias.objects.get_by_natural_key("CH-8224", "Delémont")
        assert found.pk == alias.pk


@pytest.mark.django_db
class TestRegionAliasOrdering:
    """Tests for RegionAlias default ordering."""

    def test_ordered_by_alias_text(self) -> None:
        """Aliases are returned in ascending alias_text order."""
        RegionAliasFactory.create(alias_text="Zebra")
        RegionAliasFactory.create(alias_text="Apple")
        RegionAliasFactory.create(alias_text="Mango")
        texts = list(RegionAlias.objects.values_list("alias_text", flat=True))
        assert texts == sorted(texts)


@pytest.mark.django_db
class TestRegionAliasReverseRelation:
    """Tests for the MicroRegion.aliases reverse FK accessor."""

    def test_region_aliases_reverse_accessor(self) -> None:
        """``region.aliases`` exposes the reverse FK relation."""
        region = MicroRegionFactory.create()
        RegionAliasFactory.create(region=region, alias_text="One")
        RegionAliasFactory.create(region=region, alias_text="Two")
        assert region.aliases.count() == 2

    def test_region_protected_delete(self) -> None:
        """Deleting a region with aliases raises ProtectedError."""
        from django.db.models import ProtectedError

        region = MicroRegionFactory.create()
        RegionAliasFactory.create(region=region)
        with pytest.raises(ProtectedError):
            region.delete()


@pytest.mark.django_db
class TestRegionAliasFactory:
    """Tests for the RegionAliasFactory itself."""

    def test_factory_creates_saved_instance(self) -> None:
        """RegionAliasFactory.create() produces a persisted, valid RegionAlias."""
        alias = RegionAliasFactory.create()
        assert alias.pk is not None
        alias.full_clean()

    def test_factory_produces_unique_alias_texts(self) -> None:
        """Sequential calls produce distinct alias_text values."""
        a1 = RegionAliasFactory.create()
        a2 = RegionAliasFactory.create()
        assert a1.alias_text != a2.alias_text
