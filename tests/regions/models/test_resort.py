"""
tests/regions/models/test_resort.py — Tests for the Resort model.

Covers model creation, ordering, string representation, cascade
deletion, natural key support on MicroRegion, fixture loading, and the
SNOW-504 resort-page URL helpers (``name_slug`` / ``get_absolute_url``).
"""

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command

from apps.regions.models import MicroRegion, Resort
from tests.factories import MicroRegionFactory, ResortFactory


@pytest.mark.django_db
class TestResortModel:
    """Tests for the Resort model."""

    def test_str_returns_name_and_region_id(self) -> None:
        """String representation includes the resort name and region_id."""
        resort = ResortFactory.create(name="Zermatt")
        assert str(resort) == f"Zermatt ({resort.region.region_id})"

    def test_str_delegates_to_to_string(self) -> None:
        """__str__ returns the same value as to_string()."""
        resort = ResortFactory.create(name="Zermatt")
        assert str(resort) == resort.to_string()

    def test_to_string_returns_name_and_region_id(self) -> None:
        """to_string() includes the resort name and region_id."""
        resort = ResortFactory.create(name="Zermatt")
        assert resort.to_string() == f"Zermatt ({resort.region.region_id})"

    def test_name_slug_is_slugified_name(self) -> None:
        """name_slug is the slugified resort name (SNOW-504)."""
        resort = ResortFactory.create(name="Crans-Montana")
        assert resort.name_slug == "crans-montana"

    def test_name_slug_reflects_current_name(self) -> None:
        """name_slug is derived live from name, not cached at creation."""
        resort = ResortFactory.create(name="Old Name")
        resort.name = "New Name"
        assert resort.name_slug == "new-name"

    def test_get_absolute_url_returns_resort_page_url(self) -> None:
        """get_absolute_url() builds /resorts/<id>/<slug>/ (SNOW-504)."""
        resort = ResortFactory.create(name="Verbier")
        assert resort.get_absolute_url() == f"/resorts/{resort.pk}/verbier/"

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

    def test_metadata_fields_default_to_empty(self) -> None:
        """A bare factory instance has the metadata fields unset (SNOW-500)."""
        resort = ResortFactory.create()
        assert resort.operator_name == ""
        assert resort.website == ""
        assert resort.num_lifts is None
        assert resort.num_runs is None
        assert resort.total_piste_km is None
        assert resort.base_elevation_m is None
        assert resort.top_elevation_m is None
        assert resort.typical_season_open == ""
        assert resort.typical_season_close == ""

    def test_typical_season_rejects_malformed_month_day(self) -> None:
        """A malformed season value fails full_clean() (SNOW-500)."""
        resort = ResortFactory.create(typical_season_open="13-40")
        with pytest.raises(ValidationError):
            resort.full_clean()

    def test_typical_season_accepts_valid_month_day(self) -> None:
        """A well-formed month-day value passes full_clean() (SNOW-500)."""
        resort = ResortFactory.create(typical_season_open="12-01")
        resort.full_clean()

    def test_typical_season_accepts_blank(self) -> None:
        """A blank season value passes full_clean() (SNOW-500)."""
        resort = ResortFactory.create(typical_season_open="")
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

        fixture_path = Path("apps/regions/fixtures/resorts.json")
        data = json.loads(fixture_path.read_text())
        for entry in data:
            region_ids.add(entry["fields"]["region"][0])

        for rid in region_ids:
            MicroRegionFactory.create(region_id=rid, name=f"Region {rid}")

        call_command("loaddata", "resorts", verbosity=0)
        assert Resort.objects.count() == len(data)


@pytest.mark.django_db
class TestResortKind:
    """The RESORT / TOURING_TERRAIN discriminator (SNOW-544).

    ``regions/data/resorts.tsv`` grew as one row per SLF micro-region
    with a representative place name typed into ``name``, so it
    accumulated entries that are real avalanche terrain but not resorts
    — high passes, side valleys and glacier basins with no lifts at all.

    Before this field the sheet's only verdict was ``NOT_A_SKI_RESORT``,
    which means delete. There was no way to say "keep this, just not as
    a resort", so 22 rows worth keeping were queued for deletion.
    """

    def test_kind_defaults_to_resort(self) -> None:
        """An unspecified kind is RESORT — the overwhelming majority of rows."""
        resort = ResortFactory.create()
        assert resort.kind == Resort.Kind.RESORT

    def test_queryset_filters_partition_the_table(self) -> None:
        """resorts() and touring() are complementary and exhaustive."""
        ResortFactory.create(name="Verbier")
        ResortFactory.create(name="Zermatt")
        ResortFactory.create(name="Grimsel", kind=Resort.Kind.TOURING_TERRAIN)

        assert Resort.objects.resorts().count() == 2
        assert Resort.objects.touring().count() == 1
        assert (
            Resort.objects.resorts().count() + Resort.objects.touring().count()
            == Resort.objects.count()
        )

    def test_touring_terrain_is_excluded_from_resorts(self) -> None:
        """The filter selects by kind, not by ordering luck."""
        ResortFactory.create(name="Grimsel", kind=Resort.Kind.TOURING_TERRAIN)
        assert not Resort.objects.resorts().filter(name="Grimsel").exists()


@pytest.mark.django_db
class TestResortTier:
    """The map-prominence tier (SNOW-543).

    Stored and curated, not derived: the resort-tiering review's finding is
    that scale is the wrong axis — small areas high in the Alps carry more
    avalanche decision-making per visitor than a large low resort, and piste
    km would rank them last. A stored column is what lets a curator promote
    a place that is interesting beyond what its numbers say.
    """

    def test_tier_defaults_to_standard(self) -> None:
        """An untiered resort is Standard — the middle of the three."""
        resort = Resort.objects.create(
            name="Untiered", region=MicroRegionFactory.create(), canton="VS"
        )
        assert resort.tier == Resort.Tier.STANDARD

    def test_tier_is_independent_of_size(self) -> None:
        """A four-lift area can outrank a large domain — that is the point."""
        big = ResortFactory.create(
            name="Large low resort", total_piste_km=120.0, top_elevation_m=1900
        )
        small = ResortFactory.create(
            name="Avers",
            total_piste_km=8.0,
            top_elevation_m=2539,
            tier=Resort.Tier.CORE,
        )

        assert small.tier == Resort.Tier.CORE
        assert big.tier == Resort.Tier.STANDARD

    def test_committed_fixture_tiers_are_all_valid(self) -> None:
        """Every seeded row carries a real Tier value, not a stray string."""
        import json
        from pathlib import Path

        data = json.loads(
            Path("apps/regions/fixtures/resorts.json").read_text(encoding="utf-8")
        )
        tiers = {entry["fields"]["tier"] for entry in data}
        assert tiers
        assert tiers <= set(Resort.Tier.values)

    def test_imported_coordinates_fall_inside_their_own_region(self) -> None:
        """Sheet-sourced pins are checked by the suite, not just by eye.

        Rows stamped ``geocode_source="import"`` (SNOW-544) got their
        coordinate from a reference rather than from an operator placing a
        pin, and their ``region`` was derived from that coordinate. If a
        coordinate were wrong, the resort would show a neighbouring
        region's bulletin — the one failure mode that is invisible on the
        map but wrong in the product.
        """
        import json
        from pathlib import Path

        from apps.regions.services.point_match import region_for_point

        call_command("loaddata", "eaws_CH", "resorts", verbosity=0)

        data = json.loads(
            Path("apps/regions/fixtures/resorts.json").read_text(encoding="utf-8")
        )
        imported = [
            entry for entry in data if entry["fields"].get("geocode_source") == "import"
        ]
        assert imported, "expected the fixture to carry sheet-imported rows"

        mismatched = []
        for entry in imported:
            fields = entry["fields"]
            matched = region_for_point(fields["latitude"], fields["longitude"])
            expected = fields["region"][0]
            if matched is None or matched.region_id != expected:
                found = matched.region_id if matched else None
                mismatched.append(f"{fields['name']}: {expected} != {found}")

        assert not mismatched, "coordinate/region disagreement: " + "; ".join(
            mismatched
        )
