"""
tests/observations/migrations/test_0012_drop_fieldobservation_coordinate_columns.py.

observations 0012 (SNOW-1413) drops the coordinate columns SNOW-714 took off
the model: present before it, gone after it, and back (empty and nullable)
when it is reversed. ``gps_*`` are not touched. Ends on the latest migration
so later tests see the full schema.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("observations", "0011_fieldobservation_coordinates_leave_the_model")]
AFTER = [("observations", "0012_drop_fieldobservation_coordinate_columns")]
DROPPED = {"latitude", "longitude"}


def _migrate(targets: list[tuple[str, str]]) -> None:
    """Migrate to ``targets``."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)


def _columns() -> dict[str, bool]:
    """Return the table's columns, each mapped to whether it is nullable."""
    with connection.cursor() as cursor:
        description = connection.introspection.get_table_description(
            cursor, "observations_fieldobservation"
        )
    return {column.name: column.null_ok for column in description}


@pytest.mark.django_db(transaction=True)
def test_the_columns_go_and_come_back_on_reverse() -> None:
    """Dropped going forward; re-added, nullable, going back."""
    _migrate(AFTER)
    assert DROPPED.isdisjoint(_columns())

    _migrate(BEFORE)
    columns = _columns()
    assert all(columns[name] is True for name in DROPPED)

    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
    assert DROPPED.isdisjoint(_columns())


@pytest.mark.django_db
def test_the_raw_device_fix_stays() -> None:
    """``gps_latitude`` / ``gps_longitude`` are provenance, not a copy."""
    columns = _columns()
    assert {"gps_latitude", "gps_longitude", "location_id"} <= set(columns)
