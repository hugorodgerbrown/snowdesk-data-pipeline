"""
tests/observations/migrations/test_0010_fieldobservation_submission_and_types.py.

Migrates to just before observations 0010, creates rows through the
historical models, migrates forward and checks every existing row became
its own report — a distinct ``submission`` each, never one shared value,
which is what a callable default on AddField alone would have produced.
Ends on the latest migration so later tests see the full schema.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("observations", "0009_fieldobservation_location_and_more")]
AFTER = [("observations", "0010_fieldobservation_submission_and_types")]


def _migrate(targets: list[tuple[str, str]]) -> MigrationExecutor:
    """Migrate to ``targets`` and return a fresh executor for its state."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return MigrationExecutor(connection)


@pytest.mark.django_db(transaction=True)
def test_every_existing_row_is_its_own_submission() -> None:
    """Three pre-existing rows get three different submissions."""
    old = _migrate(BEFORE).loader.project_state(BEFORE).apps
    User = old.get_model("auth", "User")
    FieldObservation = old.get_model("observations", "FieldObservation")
    user = User.objects.create(username="reporter")
    for observation_type in ("WHUMPFING", "PINWHEELS", "FRACTURES"):
        FieldObservation.objects.create(
            user=user,
            latitude=46.1,
            longitude=7.2,
            location_source="GPS",
            observation_type=observation_type,
        )

    new = _migrate(AFTER).loader.project_state(AFTER).apps
    migrated = new.get_model("observations", "FieldObservation")
    submissions = list(migrated.objects.values_list("submission", flat=True))
    assert len(submissions) == 3
    assert None not in submissions
    assert len(set(submissions)) == 3

    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
