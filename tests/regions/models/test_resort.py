"""
tests/regions/models/test_resort.py — Tests for the Resort model.

Covers model creation, ordering, string representation, cascade
deletion, natural key support on MicroRegion, importing the curated sheet,
the SNOW-796 stored ``slug`` (minted once, never regenerated, suffixed on a
collision) and ``get_absolute_url``.

The sheet assertions read ``apps/regions/data/resorts.tsv`` directly.
SNOW-817 made it the only file that describes a resort — ``resorts.json``
is gone — so these are checks on committed data, not on a fixture.
"""

import csv
from pathlib import Path

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError

from apps.regions.management.commands.import_resorts import DELETE_MARKER
from apps.regions.models import MicroRegion, Resort
from tests.factories import MicroRegionFactory, ResortFactory

SHEET_PATH = Path("apps/regions/data/resorts.tsv")


def live_sheet_rows() -> list[dict[str, str]]:
    """Return the sheet's live rows — those the ``status`` column has not retired.

    Returns:
        One dict per live row, keyed by column name.

    """
    with SHEET_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return [
        row
        for row in rows
        if (row.get("status") or "").strip().upper() != DELETE_MARKER
    ]


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

    def test_slug_is_minted_from_name_on_first_save(self) -> None:
        """A new row gets slugify(name) without anyone setting it (SNOW-796)."""
        resort = ResortFactory.create(name="Crans-Montana")
        assert resort.slug == "crans-montana"

    def test_slug_is_never_regenerated_on_rename(self) -> None:
        """A rename leaves the slug alone — it is an indexed URL."""
        resort = ResortFactory.create(name="Old Name")
        resort.name = "New Name"
        resort.save()
        resort.refresh_from_db()
        assert resort.slug == "old-name"

    def test_explicit_slug_is_kept(self) -> None:
        """A slug supplied at creation wins over the name-derived one."""
        resort = ResortFactory.create(name="Verbier", slug="verbier-4-vallees")
        assert resort.slug == "verbier-4-vallees"

    def test_duplicate_name_gets_a_numeric_suffix(self) -> None:
        """Two resorts called the same thing do not collide on the slug."""
        first = ResortFactory.create(name="Verbier")
        second = ResortFactory.create(name="Verbier")
        third = ResortFactory.create(name="Verbier")
        assert first.slug == "verbier"
        assert second.slug == "verbier-2"
        assert third.slug == "verbier-3"

    def test_slug_is_unique_at_the_database(self) -> None:
        """The unique constraint is the backstop behind the save() hook."""
        ResortFactory.create(name="Verbier")
        with pytest.raises(IntegrityError):
            ResortFactory.create(name="Other", slug="verbier")

    def test_get_absolute_url_returns_resort_page_url(self) -> None:
        """get_absolute_url() builds /resorts/<slug>/ with no pk in it."""
        resort = ResortFactory.create(name="Verbier")
        assert resort.get_absolute_url() == "/resorts/verbier/"

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
class TestResortSheet:
    """Tests for the curated sheet, apps/regions/data/resorts.tsv."""

    def test_sheet_imports_successfully(self) -> None:
        """Every live sheet row becomes a Resort when its region exists."""
        rows = live_sheet_rows()
        for region_id in {row["region"] for row in rows}:
            MicroRegionFactory.create(region_id=region_id, name=f"Region {region_id}")

        call_command("import_resorts", commit=True, verbosity=0)
        assert Resort.objects.count() == len(rows)


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

    def test_committed_sheet_tiers_are_all_valid(self) -> None:
        """Every live sheet row carries a real Tier value, not a stray string."""
        tiers = {row["tier"].strip().upper() for row in live_sheet_rows()} - {""}
        assert tiers
        assert tiers <= set(Resort.Tier.values)

    def test_imported_coordinates_fall_inside_their_own_region(self) -> None:
        """Sheet-sourced pins are checked by the suite, not just by eye.

        Rows stamped ``geocode_source="IMPORT"`` (SNOW-544) got their
        coordinate from a reference rather than from an operator placing a
        pin, and their ``region`` was derived from that coordinate. If a
        coordinate were wrong, the resort would show a neighbouring
        region's bulletin — the one failure mode that is invisible on the
        map but wrong in the product.
        """
        from apps.regions.services.point_match import region_for_point

        call_command("loaddata", "eaws_CH", verbosity=0)

        imported = [
            row
            for row in live_sheet_rows()
            if row["geocode_source"].strip().upper() == Resort.GeocodeSource.IMPORT
        ]
        assert imported, "expected the sheet to carry sheet-imported rows"

        mismatched = []
        for row in imported:
            matched = region_for_point(float(row["latitude"]), float(row["longitude"]))
            expected = row["region"]
            if matched is None or matched.region_id != expected:
                found = matched.region_id if matched else None
                mismatched.append(f"{row['name']}: {expected} != {found}")

        assert not mismatched, "coordinate/region disagreement: " + "; ".join(
            mismatched
        )


class TestResortGeocodeSource:
    """Coordinate provenance (SNOW-582: UPPER CASE storage).

    ``GeocodeSource`` replaced the bare ``GEOCODE_SOURCES`` tuple list with a
    proper ``TextChoices`` class storing upper-case values, matching
    ``Kind``/``Tier`` directly above it on the model.
    """

    def test_choices_are_upper_case(self) -> None:
        """Every GeocodeSource member value is its own upper-case form."""
        for value in Resort.GeocodeSource.values:
            assert value == value.upper()

    def test_committed_sheet_geocode_source_values_are_valid(self) -> None:
        """Every non-blank sheet row carries a real GeocodeSource value."""
        sources = {
            row["geocode_source"].strip().upper() for row in live_sheet_rows()
        } - {""}
        assert sources
        assert sources <= set(Resort.GeocodeSource.values)
