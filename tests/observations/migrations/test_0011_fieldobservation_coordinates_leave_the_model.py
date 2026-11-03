"""
tests/observations/migrations/test_0011_fieldobservation_coordinates_leave_the_model.py.

Migrates to just before observations 0011 and checks its guard (SNOW-714):
a report with no Location stops the migration with a plain message; a report
with one migrates, and the database keeps the old columns, now nullable, for
the release that drops them. Ends on the latest migration so later tests see
the full schema.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from tests.factories import LocationFactory

BEFORE = [("observations", "0010_fieldobservation_submission_and_types")]
AFTER = [("observations", "0011_fieldobservation_coordinates_leave_the_model")]


def _migrate(targets: list[tuple[str, str]]) -> MigrationExecutor:
    """Migrate to ``targets`` and return a fresh executor for its state."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return MigrationExecutor(connection)


def _leaf() -> None:
    """Migrate everything forward again."""
    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


def _columns() -> dict[str, bool]:
    """Return the report table's columns, each mapped to whether it is nullable."""
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(
            cursor, "observations_fieldobservation"
        )
    return {column.name: column.null_ok for column in description}


def _create(old: object, **kwargs: object) -> object:
    """Create one report through the historical model."""
    User = old.get_model("auth", "User")  # type: ignore[attr-defined]
    FieldObservation = old.get_model(  # type: ignore[attr-defined]
        "observations", "FieldObservation"
    )
    user = User.objects.create(username="reporter")
    return FieldObservation.objects.create(
        user=user,
        latitude=46.1,
        longitude=7.2,
        location_source="GPS",
        observation_type="WHUMPFING",
        **kwargs,
    )


@pytest.mark.django_db(transaction=True)
def test_a_located_report_migrates_and_the_columns_stay_nullable() -> None:
    """The model forgets the columns; the table keeps them for one release."""
    old = _migrate(BEFORE).loader.project_state(BEFORE).apps
    # The current Location model: only observations is rolled back here.
    _create(old, location_id=LocationFactory.create(anonymous=True).pk)

    _migrate(AFTER)

    columns = _columns()
    assert columns["latitude"] is True
    assert columns["longitude"] is True
    assert columns["location_id"] is False
    _leaf()


@pytest.mark.django_db(transaction=True)
def test_a_report_with_no_location_stops_the_migration() -> None:
    """The guard refuses, and the column is left as it was."""
    old = _migrate(BEFORE).loader.project_state(BEFORE).apps
    report = _create(old, location_id=None)

    with pytest.raises(RuntimeError, match="1 field observation"):
        _migrate(AFTER)

    assert _columns()["location_id"] is True
    report.delete()  # type: ignore[attr-defined]
    _leaf()
