"""
tests/regions/models/test_pass.py — Tests for the Pass model (SNOW-1083).

Covers the string representation, the slug-keyed lookup the resort sheet
uses, ordering and the resort link. Creating passes from ``passes.tsv`` is
``import_resorts``'s job and is tested there.
"""

import pytest

from apps.regions.models import Pass
from tests.factories import PassFactory, ResortFactory


@pytest.mark.django_db
class TestPass:
    """Tests for Pass."""

    def test_str_is_the_name(self) -> None:
        """``__str__`` delegates to ``to_string``, which is the pass's name."""
        ski_pass = PassFactory.create(name="Ikon Pass", slug="ikon")
        assert str(ski_pass) == "Ikon Pass"
        assert ski_pass.to_string() == "Ikon Pass"

    def test_by_slugs_keys_every_pass(self) -> None:
        """``by_slugs`` maps each slug to its row."""
        ikon = PassFactory.create(slug="ikon")
        by_slug = Pass.objects.by_slugs()
        assert by_slug["ikon"] == ikon
        assert set(by_slug) == {"ikon"}

    def test_ordered_by_name(self) -> None:
        """Passes list alphabetically, not by creation time."""
        PassFactory.create(name="Alpha Pass", slug="alpha")
        names = list(Pass.objects.values_list("name", flat=True))
        assert names == sorted(names)
        assert names[0] == "Alpha Pass"

    def test_resorts_reverse_relation(self) -> None:
        """A pass reaches the resorts sold on it through ``resorts``."""
        ski_pass = PassFactory.create(slug="ikon")
        resort = ResortFactory.create(name="Zermatt")
        resort.passes.add(ski_pass)
        assert list(ski_pass.resorts.all()) == [resort]
