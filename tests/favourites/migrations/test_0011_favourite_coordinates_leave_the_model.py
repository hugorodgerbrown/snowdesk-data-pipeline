"""
tests/favourites/migrations/test_0011_favourite_coordinates_leave_the_model.py.

Migrates to just before favourites 0011 and checks its guard (SNOW-714): a
pin with no Location that is not a region pin stops the migration, because
it would lose its only coordinate once the columns are dropped; placed pins
and region pins pass. Ends on the latest migration so later tests see the
full schema.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from tests.factories import LocationFactory, MicroRegionFactory

BEFORE = [("favourites", "0010_favourite_alerts")]
AFTER = [("favourites", "0011_favourite_coordinates_leave_the_model")]


def _migrate(targets: list[tuple[str, str]]) -> MigrationExecutor:
    """Migrate to ``targets`` and return a fresh executor for its state."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return MigrationExecutor(connection)


def _leaf() -> None:
    """Migrate everything forward again."""
    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_placed_pins_and_region_pins_migrate() -> None:
    """Both valid shapes pass the guard."""
    old = _migrate(BEFORE).loader.project_state(BEFORE).apps
    User = old.get_model("auth", "User")
    Favourite = old.get_model("favourites", "Favourite")
    user = User.objects.create(username="pinner")
    # The current Location model: this test rolls back favourites only, so
    # the locations table is at its latest shape.
    location = LocationFactory.create(anonymous=True)
    region = MicroRegionFactory.create()
    Favourite.objects.create(
        user=user, location_id=location.pk, latitude=46.1, longitude=7.4
    )
    Favourite.objects.create(user=user, region_id=region.pk)

    new = _migrate(AFTER).loader.project_state(AFTER).apps

    assert new.get_model("favourites", "Favourite").objects.count() == 2
    _leaf()


@pytest.mark.django_db(transaction=True)
def test_an_unplaced_pin_stops_the_migration() -> None:
    """A row with a coordinate but no Location is refused, and nothing changes."""
    old = _migrate(BEFORE).loader.project_state(BEFORE).apps
    User = old.get_model("auth", "User")
    Favourite = old.get_model("favourites", "Favourite")
    user = User.objects.create(username="pinner")
    stranded = Favourite.objects.create(user=user, latitude=46.1, longitude=7.4)

    with pytest.raises(RuntimeError, match="1 favourite"):
        _migrate(AFTER)

    stranded.delete()
    _leaf()
