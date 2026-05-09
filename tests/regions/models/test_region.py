"""
tests/regions/models/test_region.py — Tests for the Region model.

Covers model creation, string representation, ordering, natural key support,
the auto-slug behaviour, and the new centre and boundary JSON fields.
"""

import pytest
from django.core.management import call_command

from regions.models import Region
from tests.factories import EawsSubRegionFactory, RegionFactory


@pytest.mark.django_db
class TestRegionStr:
    """Tests for Region.__str__."""

    def test_str_includes_region_id_and_name(self) -> None:
        """String representation is '<region_id> — <name>'."""
        region = RegionFactory.create(region_id="CH-4115", name="Val Ferret")
        assert str(region) == "CH-4115 — Val Ferret"


@pytest.mark.django_db
class TestRegionOrdering:
    """Tests for Region default ordering."""

    def test_ordered_by_region_id(self) -> None:
        """Regions are returned in ascending region_id order."""
        RegionFactory.create(region_id="CH-9999")
        RegionFactory.create(region_id="CH-1001")
        RegionFactory.create(region_id="CH-5555")
        ids = list(Region.objects.values_list("region_id", flat=True))
        assert ids == sorted(ids)


@pytest.mark.django_db
class TestRegionSlug:
    """Tests for Region auto-slug generation."""

    def test_slug_auto_generated_from_region_id(self) -> None:
        """When no slug is provided, save() derives it from region_id."""
        sub = EawsSubRegionFactory.create(prefix="CH-41")
        region = Region(region_id="CH-4115", name="Val Ferret", subregion=sub)
        region.save()
        assert region.slug == "ch-4115"

    def test_existing_slug_not_overwritten(self) -> None:
        """A pre-set slug is preserved by save()."""
        region = RegionFactory.create(region_id="CH-4115", slug="custom-slug")
        region.save()
        assert region.slug == "custom-slug"


@pytest.mark.django_db
class TestRegionNaturalKey:
    """Tests for Region natural key support."""

    def test_natural_key_returns_tuple(self) -> None:
        """natural_key() returns a one-element tuple of region_id."""
        region = RegionFactory.create(region_id="CH-9001")
        assert region.natural_key() == ("CH-9001",)

    def test_get_by_natural_key_returns_correct_region(self) -> None:
        """get_by_natural_key() looks up by region_id."""
        region = RegionFactory.create(region_id="CH-8001")
        found = Region.objects.get_by_natural_key("CH-8001")
        assert found.pk == region.pk


@pytest.mark.django_db
class TestRegionCentreField:
    """Tests for the Region.centre JSON field."""

    def test_centre_stored_and_retrieved_as_dict(self) -> None:
        """centre is persisted as a dict and returned as a dict."""
        region = RegionFactory.create(centre={"lon": 7.5, "lat": 46.8})
        region.refresh_from_db()
        assert region.centre == {"lon": 7.5, "lat": 46.8}

    def test_centre_can_be_null(self) -> None:
        """centre is optional; null is accepted and round-trips as None."""
        region = RegionFactory.create(centre=None)
        region.refresh_from_db()
        assert region.centre is None

    def test_centre_full_clean_accepts_null(self) -> None:
        """full_clean() passes when centre is None."""
        region = RegionFactory.create(centre=None)
        region.full_clean()  # should not raise

    def test_factory_default_centre_is_dict(self) -> None:
        """The factory default produces a dict with lon and lat keys."""
        region = RegionFactory.create()
        assert isinstance(region.centre, dict)
        assert "lon" in region.centre
        assert "lat" in region.centre


@pytest.mark.django_db
class TestRegionBoundaryField:
    """Tests for the Region.boundary JSON field."""

    def test_boundary_stored_and_retrieved_as_dict(self) -> None:
        """boundary is persisted as a dict and returned as a dict."""
        polygon = {
            "type": "Polygon",
            "coordinates": [[[7.0, 46.0], [7.1, 46.0], [7.1, 46.1], [7.0, 46.0]]],
        }
        region = RegionFactory.create(boundary=polygon)
        region.refresh_from_db()
        assert region.boundary == polygon
        assert region.boundary["type"] == "Polygon"

    def test_boundary_can_be_null(self) -> None:
        """boundary is optional; null is accepted and round-trips as None."""
        region = RegionFactory.create(boundary=None)
        region.refresh_from_db()
        assert region.boundary is None

    def test_boundary_full_clean_accepts_null(self) -> None:
        """full_clean() passes when boundary is None."""
        region = RegionFactory.create(boundary=None)
        region.full_clean()  # should not raise

    def test_factory_default_boundary_is_none(self) -> None:
        """The factory default for boundary is None."""
        region = RegionFactory.create()
        assert region.boundary is None


@pytest.mark.django_db
class TestRegionFactory:
    """Tests for the RegionFactory itself."""

    def test_factory_creates_saved_instance(self) -> None:
        """RegionFactory() produces a persisted, valid Region."""
        region = RegionFactory.create()
        assert region.pk is not None
        region.full_clean()

    def test_factory_produces_unique_region_ids(self) -> None:
        """Sequential calls produce distinct region_ids."""
        r1 = RegionFactory.create()
        r2 = RegionFactory.create()
        assert r1.region_id != r2.region_id


@pytest.mark.django_db
class TestRegionNeighbours:
    """Tests for the Region.neighbours self-referential symmetric M2M."""

    def test_neighbours_default_is_empty(self) -> None:
        """A freshly-created region has no neighbours."""
        region = RegionFactory.create()
        assert list(region.neighbours.all()) == []

    def test_adding_a_neighbour_is_visible_from_both_sides(self) -> None:
        """The relation is symmetric — adding B to A's neighbours lists A in B's neighbours."""
        a = RegionFactory.create(region_id="CH-9001")
        b = RegionFactory.create(region_id="CH-9002")
        a.neighbours.add(b)
        assert b in a.neighbours.all()
        assert a in b.neighbours.all()

    def test_clearing_one_side_clears_the_other(self) -> None:
        """clear() on one endpoint removes the symmetric link."""
        a = RegionFactory.create(region_id="CH-9003")
        b = RegionFactory.create(region_id="CH-9004")
        a.neighbours.add(b)
        a.neighbours.clear()
        assert list(a.neighbours.all()) == []
        assert list(b.neighbours.all()) == []

    def test_set_replaces_neighbour_membership(self) -> None:
        """set() replaces the full neighbour set on both endpoints."""
        a = RegionFactory.create(region_id="CH-9005")
        b = RegionFactory.create(region_id="CH-9006")
        c = RegionFactory.create(region_id="CH-9007")
        a.neighbours.set([b])
        assert list(a.neighbours.all()) == [b]
        a.neighbours.set([c])
        assert list(a.neighbours.all()) == [c]
        # b is no longer a neighbour of a, so a should not be in b's neighbours.
        assert a not in b.neighbours.all()
        assert a in c.neighbours.all()


@pytest.mark.django_db
class TestRegionsFixture:
    """Tests for the regions.json fixture."""

    def test_fixture_loads_successfully(self) -> None:
        """The regions fixture loads 149 Region rows without errors."""
        call_command("loaddata", "regions", verbosity=0)
        assert Region.objects.count() == 149

    def test_fixture_regions_have_centre_and_boundary(self) -> None:
        """Every region loaded from the fixture has non-null centre and boundary."""
        call_command("loaddata", "regions", verbosity=0)
        without_centre = Region.objects.filter(centre__isnull=True).count()
        without_boundary = Region.objects.filter(boundary__isnull=True).count()
        assert without_centre == 0
        assert without_boundary == 0

    def test_fixture_first_region_has_expected_data(self) -> None:
        """The CH-1111 region has the correct centre coordinates."""
        call_command("loaddata", "regions", verbosity=0)
        region = Region.objects.get(region_id="CH-1111")
        assert region.name == "Aigle - Yvorne"
        assert region.centre is not None
        assert abs(region.centre["lon"] - 6.939685) < 1e-6
        assert abs(region.centre["lat"] - 46.470737) < 1e-6
        assert region.boundary is not None
        assert region.boundary["type"] == "Polygon"

    def test_fixture_loads_neighbour_pairs(self) -> None:
        """The fixture rehydrates Region.neighbours via natural-key M2M."""
        call_command("loaddata", "regions", verbosity=0)
        # Every region should have at least one neighbour — SLF micro-regions
        # tessellate a contiguous country, so isolated nodes would indicate a
        # bug in the build script.
        isolated = [
            r.region_id
            for r in Region.objects.prefetch_related("neighbours")
            if r.neighbours.count() == 0
        ]
        assert isolated == [], f"Regions with no neighbours: {isolated}"

    def test_fixture_neighbour_graph_is_symmetric(self) -> None:
        """For every (A, B) edge, A appears in B.neighbours and vice versa."""
        call_command("loaddata", "regions", verbosity=0)
        for region in Region.objects.prefetch_related("neighbours"):
            for neighbour in region.neighbours.all():
                back_edge = neighbour.neighbours.filter(pk=region.pk).exists()
                assert back_edge, (
                    f"Asymmetric edge: {region.region_id} -> "
                    f"{neighbour.region_id} but not the other way"
                )

    def test_fixture_known_neighbour_pair(self) -> None:
        """CH-4115 (Martigny-Verbier) borders CH-4116 (Haut Val de Bagnes)."""
        call_command("loaddata", "regions", verbosity=0)
        a = Region.objects.get(region_id="CH-4115")
        b = Region.objects.get(region_id="CH-4116")
        assert b in a.neighbours.all()
        assert a in b.neighbours.all()

    def test_fixture_polygon_rings_are_closed(self) -> None:
        """Every fixture polygon's linear rings satisfy RFC 7946 §3.1.6.

        An open ring renders as a visible gap on the map boundary layer
        even though MapLibre auto-closes the fill. This assertion guards
        the CSV source from regressing to unclosed rings.
        """
        call_command("loaddata", "regions", verbosity=0)
        offenders: list[str] = []
        for region in Region.objects.all():
            assert region.boundary is not None
            for ring_idx, ring in enumerate(region.boundary["coordinates"]):
                if ring[0] != ring[-1]:
                    offenders.append(f"{region.region_id}[ring {ring_idx}]")
        assert offenders == [], f"Unclosed rings: {offenders}"
