"""
tests/downloads/test_migrations.py — Tests for apps.downloads migrations.

downloads.0002_downloadarea_terrain (SNOW-1072):
  adds ``terrain`` as a BooleanField defaulting to False, so every row that
    predates it reads as an area downloaded without terrain data — which is
    what every one of them was;
  depends on 0001 and is the app's leaf, so the graph has one head.

Schema-only, so the operation itself is inspected rather than run through
the migration executor: the executor would rebuild the schema to prove what
the operation's own field definition already states.
"""

from __future__ import annotations

from importlib import import_module

from django.db import models
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations import AddField

# ``import_module`` rather than a plain import: the module name starts with
# a digit, so it is not a legal Python identifier.
_MIGRATION = import_module("apps.downloads.migrations.0002_downloadarea_terrain")


class TestDownloadAreaTerrainMigration:
    """The migration that adds DownloadArea.terrain."""

    def test_adds_a_boolean_defaulting_to_false(self) -> None:
        """One AddField: terrain, a BooleanField, default False."""
        operations = _MIGRATION.Migration.operations
        assert len(operations) == 1
        operation = operations[0]
        assert isinstance(operation, AddField)
        assert operation.model_name == "downloadarea"
        assert operation.name == "terrain"
        assert isinstance(operation.field, models.BooleanField)
        assert operation.field.default is False

    def test_follows_the_initial_migration(self) -> None:
        """It builds on 0001 and nothing else."""
        assert _MIGRATION.Migration.dependencies == [("downloads", "0001_initial")]

    def test_is_the_apps_only_leaf(self) -> None:
        """The downloads graph has exactly one head, and it is this one."""
        loader = MigrationLoader(None, ignore_no_migrations=True)
        leaves = [name for app, name in loader.graph.leaf_nodes() if app == "downloads"]
        assert leaves == ["0002_downloadarea_terrain"]
