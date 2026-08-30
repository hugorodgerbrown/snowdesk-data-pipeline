"""
tests/observations/management/commands/test_backfill_observation_locations.py

Covers ``backfill_observation_locations`` (SNOW-709):
  - Mints a Location per pre-SNOW-709 report and points the FK.
  - **Every report survives with its coordinates, timestamp and provenance
    intact** — the assertion the ticket is really about.
  - The provenance fields do not move: gps coordinates, location_source and
    accuracy_radius_km stay on the report.
  - The minted location is anonymous and carries **no forecast cell**.
  - Dry-run writes nothing; idempotent under --commit.
  - One failing report does not abort the batch; the command exits non-zero.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.locations.models import Location
from apps.observations.models import FieldObservation
from tests.factories import FieldObservationFactory

COMMAND = "backfill_observation_locations"


def unmigrated(**kwargs: object) -> FieldObservation:
    """Build a pre-SNOW-709 report — one with no location.

    Args:
        **kwargs: Overrides forwarded to the factory.

    Returns:
        A persisted FieldObservation whose ``location`` is null.

    """
    return FieldObservationFactory.create(location=None, **kwargs)


@pytest.mark.django_db
class TestBackfillObservationLocations:
    """--commit mints a location per report and points the FK."""

    def test_mints_a_location_and_points_the_fk(self) -> None:
        """A pre-SNOW-709 report ends up with a location at its coordinate."""
        observation = unmigrated(latitude=46.4321, longitude=7.8765)

        call_command(COMMAND, "--commit", stdout=StringIO())

        observation.refresh_from_db()
        assert observation.location is not None
        assert observation.location.latitude == 46.4321
        assert observation.location.longitude == 7.8765

    def test_every_report_survives_intact(self) -> None:
        """Reports are immutable user records — nothing may change.

        The assertion this ticket is really about, captured before and
        compared after across every column on the row, including the
        provenance fields that must not move.
        """
        observations = [
            unmigrated(
                location_source=FieldObservation.LOCATION_SOURCE.GPS_REFINED,
                gps_latitude=46.1001,
                gps_longitude=7.1001,
                accuracy_radius_km=0.05,
            ),
            unmigrated(
                latitude=46.2,
                longitude=7.2,
                location_source=FieldObservation.LOCATION_SOURCE.MANUAL,
            ),
        ]
        before = {
            o.pk: (
                o.user_id,
                o.region_id,
                o.latitude,
                o.longitude,
                o.gps_latitude,
                o.gps_longitude,
                o.location_source,
                o.accuracy_radius_km,
                o.observation_type,
                o.observed_at,
                o.uuid,
                o.created_at,
            )
            for o in observations
        }

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert FieldObservation.objects.count() == len(observations)
        for observation in FieldObservation.objects.all():
            assert (
                observation.user_id,
                observation.region_id,
                observation.latitude,
                observation.longitude,
                observation.gps_latitude,
                observation.gps_longitude,
                observation.location_source,
                observation.accuracy_radius_km,
                observation.observation_type,
                observation.observed_at,
                observation.uuid,
                observation.created_at,
            ) == before[observation.pk]

    def test_the_gps_offset_stays_recoverable(self) -> None:
        """A refined pin's distance from the raw fix survives the migration.

        The gap between the report coordinate and the device fix is the
        difference between "I was standing here" and "I tapped roughly
        here" — load-bearing for a safety report, and recoverable only
        while both pairs remain on the row.
        """
        observation = unmigrated(
            latitude=46.1050,
            longitude=7.1050,
            gps_latitude=46.1000,
            gps_longitude=7.1000,
            location_source=FieldObservation.LOCATION_SOURCE.GPS_REFINED,
        )

        call_command(COMMAND, "--commit", stdout=StringIO())

        observation.refresh_from_db()
        assert observation.gps_latitude == 46.1000
        assert observation.gps_longitude == 7.1000
        assert observation.latitude != observation.gps_latitude

    def test_minted_location_is_anonymous(self) -> None:
        """No name, no kind, and no forecast cell.

        An observation shows no forecast panel; resolving a cell would mean
        an Open-Meteo round trip per historical report for weather nothing
        renders.
        """
        unmigrated()

        call_command(COMMAND, "--commit", stdout=StringIO())

        location = Location.objects.get()
        assert location.name == ""
        assert location.kind == ""

    def test_dry_run_writes_nothing(self) -> None:
        """The default invocation is read-only."""
        observation = unmigrated()

        out = StringIO()
        call_command(COMMAND, stdout=out)

        observation.refresh_from_db()
        assert observation.location is None
        assert not Location.objects.exists()
        assert "Read-only run complete" in out.getvalue()

    def test_second_run_selects_nothing(self) -> None:
        """Idempotent — an already-migrated report is not a candidate."""
        unmigrated()
        call_command(COMMAND, "--commit", stdout=StringIO())

        out = StringIO()
        call_command(COMMAND, "--commit", stdout=out)

        assert "0 location(s) minted" in out.getvalue()
        assert Location.objects.count() == 1

    def test_several_reports_get_their_own_locations(self) -> None:
        """One row per report — coordinates are not merged on equality.

        Exact float equality on a user coordinate is a false economy, and
        wrongly merging two reports into one place is worse than an extra
        row. Sharing happens by curation, not by automatic dedup.
        """
        unmigrated(latitude=46.1, longitude=7.1)
        unmigrated(latitude=46.1, longitude=7.1)

        call_command(COMMAND, "--commit", stdout=StringIO())

        assert Location.objects.count() == 2


@pytest.mark.django_db
class TestBackfillObservationLocationsFailures:
    """One failing report must not cost the others."""

    def test_one_failure_does_not_stop_the_batch(self) -> None:
        """A report that fails is counted; the rest still migrate."""
        unmigrated()
        unmigrated()
        real_create = Location.objects.create
        calls = {"n": 0}

        def fail_first(**kwargs: object) -> Location:
            """Raise on the first mint, then behave normally."""
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_create(**kwargs)

        with (
            patch.object(Location.objects, "create", side_effect=fail_first),
            pytest.raises(CommandError, match="1 failure"),
        ):
            call_command(COMMAND, "--commit", stdout=StringIO())

        assert FieldObservation.objects.filter(location__isnull=False).count() == 1
        assert FieldObservation.objects.filter(location__isnull=True).count() == 1

    def test_a_failed_report_is_left_untouched(self) -> None:
        """A failure rolls back its own savepoint and nothing else."""
        observation = unmigrated()

        with (
            patch.object(Location.objects, "create", side_effect=RuntimeError("boom")),
            pytest.raises(CommandError),
        ):
            call_command(COMMAND, "--commit", stdout=StringIO())

        observation.refresh_from_db()
        assert observation.location is None
        assert not Location.objects.exists()
