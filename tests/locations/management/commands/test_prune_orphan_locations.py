"""
tests/locations/management/commands/test_prune_orphan_locations.py

Covers ``prune_orphan_locations`` (SNOW-771 cleanup).

The orphans exist because the deploy-time re-link used to mint a fresh
centroid ``Location`` each time instead of rebinding to the existing one,
stranding 461 rows and their weather per deploy. The reuse fix stops new
ones appearing; this command clears the backlog.

The tests that matter are the negative ones: a named location is curated
data and must never be swept, and a location something still points at must
survive even though it is anonymous.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command

from apps.locations.models import Location
from apps.weather.models import Weather
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortLocationFactory,
    WeatherFactory,
)

COMMAND = "prune_orphan_locations"


@pytest.mark.django_db
class TestPruneOrphanLocations:
    """--commit deletes anonymous locations nothing references."""

    def test_an_orphan_and_its_weather_are_deleted(self) -> None:
        """The case the command exists for."""
        orphan = LocationFactory.create(anonymous=True)
        WeatherFactory.create(location=orphan)

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert not Location.objects.filter(pk=orphan.pk).exists()
        assert not Weather.objects.exists()

    def test_a_named_location_is_never_swept(self) -> None:
        """Curated data, even when nothing points at it.

        An unreferenced curated place is a curation question for
        ``import_locations``, not garbage. Sweeping it would delete a row a
        human put there on purpose.
        """
        curated = LocationFactory.create(name="Mont Fort")

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert Location.objects.filter(pk=curated.pk).exists()

    @pytest.mark.parametrize("referrer", ["resort", "region", "favourite", "report"])
    def test_a_referenced_location_survives(self, referrer: str) -> None:
        """Every inbound reference protects the row.

        Parametrised over all four because missing one would delete live
        data — and the reverse accessor names are easy to get wrong
        (``field_observations``, not ``observations``).
        """
        location = LocationFactory.create(anonymous=True)
        if referrer == "resort":
            ResortLocationFactory.create(location=location)
        elif referrer == "region":
            MicroRegionFactory.create(centroid_location=location)
        elif referrer == "favourite":
            FavouriteFactory.create(location=location)
        else:
            FieldObservationFactory.create(location=location)

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert Location.objects.filter(pk=location.pk).exists()

    def test_dry_run_deletes_nothing(self) -> None:
        """Read-only by default, per the command design rules."""
        orphan = LocationFactory.create(anonymous=True)
        WeatherFactory.create(location=orphan)

        out = StringIO()
        call_command(COMMAND, stdout=out)

        assert Location.objects.filter(pk=orphan.pk).exists()
        assert Weather.objects.exists()
        assert "would be deleted" in out.getvalue()

    def test_reports_both_counts(self) -> None:
        """The weather count is the one that shows the real cost."""
        for _ in range(3):
            orphan = LocationFactory.create(anonymous=True)
            WeatherFactory.create(location=orphan)

        out = StringIO()
        call_command(COMMAND, stdout=out)

        assert "3 orphaned location(s), 3 weather row(s)" in out.getvalue()
