"""
tests/weather/test_content_types.py — ContentType rows follow the models.

``django_content_type`` is keyed on ``(app_label, model)``, so moving a
model between apps strands its row unless a migration rewrites the label.
``weather/0002_move_content_types`` does that rewrite; without it the
``contenttypes`` ``post_migrate`` hook would mint fresh rows under
``weather`` and orphan every ``auth_permission`` and admin ``LogEntry``
that points at the old ones.

These tests assert the end state a migrated database must be in: exactly
one row per moved model, under ``weather``, and nothing left behind under
``bulletins``.

Those end-state assertions pass trivially here, because the test database
is built by ``migrate`` and its ``post_migrate`` hook writes the
``weather`` rows before the migration could move anything. The branch a
production database actually takes is therefore exercised by calling
``_relabel`` / ``forwards`` / ``backwards`` directly, after forcing a row
back under the old label.
"""

import importlib

import pytest
from django.apps import apps as global_apps
from django.contrib.auth.models import Permission, User
from django.contrib.contenttypes.models import ContentType

# The migration module's name starts with a digit, so it can only be
# reached via importlib — a plain ``from … import`` is a syntax error.
_migration = importlib.import_module("apps.weather.migrations.0002_move_content_types")

MOVED_MODELS = (
    "weathersnapshot",
    "forecastpoint",
    "forecastpointweather",
    "forecastpointweatherhistory",
)


@pytest.mark.django_db
@pytest.mark.parametrize("model_name", MOVED_MODELS)
def test_content_type_resolves_under_the_weather_label(model_name: str) -> None:
    """Each moved model has exactly one ContentType, labelled ``weather``."""
    rows = ContentType.objects.filter(app_label="weather", model=model_name)
    assert rows.count() == 1


@pytest.mark.django_db
@pytest.mark.parametrize("model_name", MOVED_MODELS)
def test_no_orphan_bulletins_content_type_survives(model_name: str) -> None:
    """No stale ``bulletins`` row is left pointing at the same table."""
    assert not ContentType.objects.filter(
        app_label="bulletins", model=model_name
    ).exists()


@pytest.mark.django_db
def test_content_type_round_trips_to_the_model_class() -> None:
    """The relabelled row still resolves to the live model class."""
    from apps.weather.models import WeatherSnapshot

    content_type = ContentType.objects.get(app_label="weather", model="weathersnapshot")
    assert content_type.model_class() is WeatherSnapshot


@pytest.mark.django_db
def test_relabel_moves_a_pre_split_row() -> None:
    """A row still labelled ``bulletins`` is relabelled, keeping its pk.

    This is the case a real production database is in — the test database
    is built by ``migrate``, whose ``post_migrate`` hook already wrote the
    ``weather`` rows, so the migration itself would otherwise never hit
    its interesting branch here.
    """
    row = ContentType.objects.get(app_label="weather", model="weathersnapshot")
    original_pk = row.pk
    ContentType.objects.filter(pk=original_pk).update(app_label="bulletins")

    _migration._relabel(global_apps, "bulletins", "weather")

    moved = ContentType.objects.get(app_label="weather", model="weathersnapshot")
    assert moved.pk == original_pk
    assert not ContentType.objects.filter(
        app_label="bulletins", model="weathersnapshot"
    ).exists()


@pytest.mark.django_db
def test_relabel_keeps_the_pre_split_row_when_both_labels_hold_one() -> None:
    """When both labels hold a row, the pre-split one is the survivor.

    A fresh database's ``post_migrate`` can mint the ``weather`` row before
    this migration runs, leaving two rows for one table. The ``bulletins``
    row is the one every ``auth_permission`` and admin ``LogEntry`` points
    at, so it must survive and the history-less ``weather`` row must go —
    deleting the other way round cascades away exactly what this migration
    exists to preserve.
    """
    post_migrate_row = ContentType.objects.get(
        app_label="weather", model="forecastpoint"
    )
    pre_split_row = ContentType.objects.create(
        app_label="bulletins", model="forecastpoint"
    )

    _migration._relabel(global_apps, "bulletins", "weather")

    surviving = ContentType.objects.get(app_label="weather", model="forecastpoint")
    assert surviving.pk == pre_split_row.pk
    assert not ContentType.objects.filter(pk=post_migrate_row.pk).exists()
    assert not ContentType.objects.filter(
        app_label="bulletins", model="forecastpoint"
    ).exists()


@pytest.mark.django_db
def test_relabel_preserves_permissions_and_their_assignments() -> None:
    """A permission on the pre-split row survives, still granted to its user.

    This is the migration's whole purpose, stated as an assertion rather
    than proxied through a surviving primary key: ``auth_permission`` holds
    a ``content_type_id``, so relabelling the row keeps the grant intact
    while deleting it would cascade the permission — and the grant — away.
    """
    row = ContentType.objects.get(app_label="weather", model="weathersnapshot")
    ContentType.objects.filter(pk=row.pk).update(app_label="bulletins")
    row.refresh_from_db()

    permission = Permission.objects.create(
        content_type=row,
        codename="snow654_can_brew_weather",
        name="Can brew weather",
    )
    user = User.objects.create_user(username="snow654", password="unused-in-this-test")
    user.user_permissions.add(permission)

    _migration._relabel(global_apps, "bulletins", "weather")

    permission.refresh_from_db()
    assert permission.content_type.app_label == "weather"
    assert (
        User.objects.get(pk=user.pk).user_permissions.filter(pk=permission.pk).exists()
    )


@pytest.mark.django_db
def test_forwards_and_backwards_move_the_rows_in_opposite_directions() -> None:
    """The two ``RunPython`` wrappers pass their labels the right way round.

    ``_relabel`` is symmetric, so a swapped argument order in either
    wrapper would be invisible to every test that only calls the helper.
    """
    row = ContentType.objects.get(app_label="weather", model="forecastpointweather")
    ContentType.objects.filter(pk=row.pk).update(app_label="bulletins")

    _migration.forwards(global_apps, None)
    assert ContentType.objects.get(pk=row.pk).app_label == "weather"

    _migration.backwards(global_apps, None)
    assert ContentType.objects.get(pk=row.pk).app_label == "bulletins"


@pytest.mark.django_db
def test_relabel_is_a_no_op_when_nothing_carries_the_old_label() -> None:
    """Re-running the migration changes nothing."""
    before = set(
        ContentType.objects.filter(app_label="weather").values_list("pk", flat=True)
    )

    _migration._relabel(global_apps, "bulletins", "weather")

    after = set(
        ContentType.objects.filter(app_label="weather").values_list("pk", flat=True)
    )
    assert after == before
