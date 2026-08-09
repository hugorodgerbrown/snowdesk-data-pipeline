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
"""

import importlib

import pytest
from django.apps import apps as global_apps
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
def test_relabel_drops_a_duplicate_left_under_the_old_label() -> None:
    """When both labels hold a row, the stale one is deleted.

    That happens on a fresh database whose ``post_migrate`` created the
    ``weather`` row before this migration ran.
    """
    keeper = ContentType.objects.get(app_label="weather", model="forecastpoint")
    stale = ContentType.objects.create(app_label="bulletins", model="forecastpoint")

    _migration._relabel(global_apps, "bulletins", "weather")

    assert not ContentType.objects.filter(pk=stale.pk).exists()
    assert ContentType.objects.filter(pk=keeper.pk).exists()


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
