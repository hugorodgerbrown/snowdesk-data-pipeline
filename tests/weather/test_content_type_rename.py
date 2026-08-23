"""
tests/weather/test_content_type_rename.py — ContentType rows follow the rename.

``django_content_type`` is keyed on ``(app_label, model)``, so renaming a
model strands its row unless a migration rewrites the ``model`` column.
``weather/0005_rename_content_types`` does that for SNOW-703; without it the
``contenttypes`` ``post_migrate`` hook mints fresh rows under the new names
and orphans every ``auth_permission`` and admin ``LogEntry`` pointing at the
old ones.

Sibling of ``test_content_types.py``, which covers the SNOW-654 app-label
move. Same shape, different column.

The end-state assertions pass trivially here, because the test database is
built by ``migrate`` and its ``post_migrate`` hook writes the new-name rows
before the migration could rename anything. The branch a production database
actually takes is therefore exercised by calling ``_rename`` / ``forwards``
/ ``backwards`` directly, after forcing a row back to its old name.
"""

import importlib

import pytest
from django.apps import apps as global_apps
from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType

# The migration module's name starts with a digit, so it can only be
# reached via importlib — a plain ``from … import`` is a syntax error.
_migration = importlib.import_module(
    "apps.weather.migrations.0005_rename_content_types"
)

RENAMED = _migration.RENAMED_MODELS


@pytest.mark.django_db
@pytest.mark.parametrize(("old_model", "new_model"), RENAMED)
def test_only_the_new_name_has_a_row(old_model: str, new_model: str) -> None:
    """Each renamed model has exactly one row, under its new name."""
    assert ContentType.objects.filter(app_label="weather", model=new_model).count() == 1
    assert not ContentType.objects.filter(app_label="weather", model=old_model).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(("old_model", "new_model"), RENAMED)
def test_the_row_resolves_to_the_live_model_class(
    old_model: str, new_model: str
) -> None:
    """The renamed row still resolves to a real model class.

    A row whose ``model`` no longer matches any registered model resolves
    to ``None`` — which is what a missed rename would look like, and what
    every consumer of ``LogEntry.content_type`` would then see.
    """
    content_type = ContentType.objects.get(app_label="weather", model=new_model)
    assert content_type.model_class() is not None


@pytest.mark.django_db
def test_rename_moves_a_pre_rename_row_keeping_its_pk() -> None:
    """A row still under the old name is renamed in place.

    This is the case a real production database is in — the test database's
    ``post_migrate`` already wrote the new-name rows, so the migration would
    otherwise never hit its interesting branch here.
    """
    row = ContentType.objects.get(app_label="weather", model="forecastcell")
    original_pk = row.pk
    ContentType.objects.filter(pk=original_pk).update(model="forecastpoint")

    _migration.forwards(global_apps, None)

    renamed = ContentType.objects.get(app_label="weather", model="forecastcell")
    assert renamed.pk == original_pk
    assert not ContentType.objects.filter(
        app_label="weather", model="forecastpoint"
    ).exists()


@pytest.mark.django_db
def test_the_pre_rename_row_wins_when_both_names_hold_one() -> None:
    """When both names hold a row, the pre-rename one survives.

    A fresh database's ``post_migrate`` can mint the new-name row before
    this migration runs, leaving two rows for one table. The old-name row is
    the one every ``auth_permission`` and admin ``LogEntry`` points at, so it
    must survive and the history-less one must go — deleting the other way
    round cascades away exactly what this migration exists to preserve.
    """
    post_migrate_row = ContentType.objects.get(
        app_label="weather", model="forecastcell"
    )
    pre_rename_row = ContentType.objects.create(
        app_label="weather", model="forecastpoint"
    )

    _migration.forwards(global_apps, None)

    surviving = ContentType.objects.get(app_label="weather", model="forecastcell")
    assert surviving.pk == pre_rename_row.pk
    assert not ContentType.objects.filter(pk=post_migrate_row.pk).exists()


@pytest.mark.django_db
def test_rename_preserves_permissions_and_their_assignments() -> None:
    """A permission on the pre-rename row survives, still granted to its user.

    The migration's whole purpose, stated as an assertion rather than
    proxied through a surviving primary key: ``auth_permission`` holds a
    ``content_type_id``, so renaming the row keeps the grant intact while
    deleting it would cascade the permission — and the grant — away.
    """
    row = ContentType.objects.get(app_label="weather", model="forecastcell")
    ContentType.objects.filter(pk=row.pk).update(model="forecastpoint")
    row.refresh_from_db()

    permission = Permission.objects.create(
        content_type=row,
        codename="snow703_can_read_cells",
        name="Can read cells",
    )
    user = User.objects.create_user(username="snow703", password="unused-in-this-test")
    user.user_permissions.add(permission)

    _migration.forwards(global_apps, None)

    permission.refresh_from_db()
    assert permission.content_type.model == "forecastcell"
    assert (
        User.objects.get(pk=user.pk).user_permissions.filter(pk=permission.pk).exists()
    )


@pytest.mark.django_db
def test_forwards_and_backwards_move_in_opposite_directions() -> None:
    """The two ``RunPython`` wrappers pass their pairs the right way round.

    ``_rename`` is symmetric, so a swapped pair order in either wrapper
    would be invisible to every test that only calls the helper.
    """
    row = ContentType.objects.get(app_label="weather", model="forecastcellweather")
    ContentType.objects.filter(pk=row.pk).update(model="forecastpointweather")

    _migration.forwards(global_apps, None)
    assert ContentType.objects.get(pk=row.pk).model == "forecastcellweather"

    _migration.backwards(global_apps, None)
    assert ContentType.objects.get(pk=row.pk).model == "forecastpointweather"


@pytest.mark.django_db
def test_rename_is_a_no_op_when_nothing_carries_the_old_name() -> None:
    """Re-running the migration changes nothing."""
    before = set(
        ContentType.objects.filter(app_label="weather").values_list("pk", flat=True)
    )

    _migration.forwards(global_apps, None)

    after = set(
        ContentType.objects.filter(app_label="weather").values_list("pk", flat=True)
    )
    assert after == before
