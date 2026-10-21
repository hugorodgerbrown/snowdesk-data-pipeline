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
from django.db.models import PROTECT

from apps.locations.models import Location
from apps.trips.models import Trip
from apps.weather.models import Weather
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    LocationFactory,
    MicroRegionFactory,
    ResortFactory,
    ResortLocationFactory,
    TripFactory,
    WeatherFactory,
)

COMMAND = "prune_orphan_locations"

# Every PROTECT relation onto Location, as (parametrisation id, reverse
# accessor). ``_orphans()`` must exclude each accessor; the meta guard
# ``test_every_protect_relation_is_excluded`` keeps this list honest against
# the model, so a new PROTECT foreign key fails a test before it can make
# ``--commit`` raise (SNOW-1171).
REFERRERS: tuple[tuple[str, str], ...] = (
    ("resort", "resort_locations"),
    ("region", "micro_regions"),
    ("resort_pin", "pinned_resorts"),
    ("favourite", "favourites"),
    ("report", "field_observations"),
    ("trip", "trips"),
)


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

    @pytest.mark.parametrize("referrer", [name for name, _ in REFERRERS])
    def test_a_referenced_location_survives(self, referrer: str) -> None:
        """Every inbound reference protects the row.

        Parametrised over all six because missing one would delete live
        data or, for a PROTECT relation, abort the whole sweep — and the
        reverse accessor names are easy to get wrong (``field_observations``,
        not ``observations``). Each branch is explicit, so a new entry in
        ``REFERRERS`` without one fails rather than quietly reusing another
        referrer's setup.
        """
        location = LocationFactory.create(anonymous=True)
        if referrer == "resort":
            ResortLocationFactory.create(location=location)
        elif referrer == "region":
            MicroRegionFactory.create(centroid_location=location)
        elif referrer == "resort_pin":
            ResortFactory.create(location=location)
        elif referrer == "favourite":
            FavouriteFactory.create(location=location)
        elif referrer == "report":
            FieldObservationFactory.create(location=location)
        elif referrer == "trip":
            TripFactory.create(meeting_point=location)
        else:
            pytest.fail(f"No setup branch for referrer {referrer!r}")

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert Location.objects.filter(pk=location.pk).exists()

    def test_every_protect_relation_is_excluded(self) -> None:
        """Every PROTECT relation onto Location is covered by ``REFERRERS``.

        SNOW-1171 was a PROTECT foreign key (``Trip.meeting_point``) added
        without a matching exclusion in ``_orphans()``, which made every
        ``--commit`` raise. This reads the relations off the model so the
        next one fails here, naming the accessor, instead of in production.

        ``Weather`` is deliberately absent: its foreign key is CASCADE, and
        deleting an orphan's weather with it is the point of the command.
        """
        # get_accessor_name() is typed str | None; str() satisfies mypy. A
        # related_name="+" relation is hidden, so _meta.related_objects never
        # lists it and this guard cannot see it -- nor could _orphans() filter
        # on it, since it has no reverse accessor.
        protected = {
            str(rel.get_accessor_name())
            for rel in Location._meta.related_objects
            if rel.on_delete is PROTECT
        }
        covered = {accessor for _, accessor in REFERRERS}

        missing = protected - covered
        assert not missing, (
            f"PROTECT relation(s) {sorted(missing)} onto Location are not "
            "excluded: add each accessor as `<accessor>__isnull=True` in "
            "prune_orphan_locations._orphans() and as a branch in REFERRERS "
            "and test_a_referenced_location_survives."
        )
        stale = covered - protected
        assert not stale, (
            f"REFERRERS lists {sorted(stale)}, which is no longer a PROTECT "
            "relation onto Location — remove or reclassify it."
        )

    def test_a_live_trip_meeting_point_is_not_an_orphan(self) -> None:
        """A trip's meeting point is spared, and the sweep still completes.

        SNOW-1171. ``Trip.meeting_point`` is PROTECT, so a sweep that counted
        a live meeting point as an orphan did not delete it — it raised
        ``ProtectedError`` and the one transaction rolled the whole sweep
        back. While any trip existed, no orphan could ever be cleared.
        """
        trip = TripFactory.create()
        orphan = LocationFactory.create(anonymous=True)
        WeatherFactory.create(location=orphan)

        out = StringIO()
        call_command(COMMAND, stdout=out)
        assert "1 orphaned location(s)" in out.getvalue()

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert not Location.objects.filter(pk=orphan.pk).exists()
        assert not Weather.objects.filter(location_id=orphan.pk).exists()
        assert Trip.objects.filter(pk=trip.pk).exists()
        assert Location.objects.filter(pk=trip.meeting_point_id).exists()

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
