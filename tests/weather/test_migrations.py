"""
tests/weather/test_migrations.py — The weather tables kept their old names.

SNOW-654 split the Open-Meteo models out of ``apps.bulletins`` into
``apps.weather`` as a pure code move: every model pins ``Meta.db_table``
to the ``bulletins_*`` name it had before, and every migration the split
generated is wrapped in ``SeparateDatabaseAndState`` with an empty
``database_operations`` list, so ``migrate`` emits no DDL at all.

That premise is invisible in the model definitions unless you go looking
for the ``db_table`` lines, and a later ticket dropping one of them would
silently ask production for a table rename. This module is the guard: if
one of these assertions fails, either the rename is deliberate — in which
case it needs its own migration and its own ticket — or a ``Meta`` was
edited without noticing what it was holding in place.

SNOW-703 renamed the three forecast models to ``ForecastCell*`` on exactly
the same terms, so the same guard now also covers the two renamed foreign
keys' ``db_column`` pins and the index names, and asserts directly that
every state-only migration emits no SQL.
"""

import pytest
from django.db import migrations
from django.db.models import ForeignKey, Model

from apps.weather.models import (
    ForecastCell,
    ForecastCellWeather,
    ForecastCellWeatherHistory,
    WeatherSnapshot,
)


@pytest.mark.parametrize(
    ("model", "expected_table"),
    [
        (WeatherSnapshot, "bulletins_weathersnapshot"),
        (ForecastCell, "bulletins_forecastpoint"),
        (ForecastCellWeather, "bulletins_forecastpointweather"),
        (ForecastCellWeatherHistory, "bulletins_forecastpointweatherhistory"),
    ],
)
def test_model_keeps_its_pre_split_table_name(
    model: type[Model], expected_table: str
) -> None:
    """The app label moved; the table name did not."""
    assert model._meta.db_table == expected_table


def test_no_weather_table_is_named_after_the_new_app() -> None:
    """A ``weather_*`` table name would mean the pin was dropped."""
    tables = {
        model._meta.db_table
        for model in (
            WeatherSnapshot,
            ForecastCell,
            ForecastCellWeather,
            ForecastCellWeatherHistory,
        )
    }
    assert not any(table.startswith("weather_") for table in tables)


@pytest.mark.parametrize(
    ("model", "expected_column"),
    [
        (ForecastCellWeather, "forecast_point_id"),
        (ForecastCellWeatherHistory, "forecast_point_id"),
    ],
)
def test_renamed_fk_keeps_its_pre_rename_column(
    model: type[Model], expected_column: str
) -> None:
    """SNOW-703 renamed the field; the column did not move.

    Dropping this pin would turn the rename into a real ``ALTER`` on the
    two largest tables in the schema.
    """
    field = model._meta.get_field("forecast_cell")
    # get_field can return a reverse relation, which has no column; this
    # one is a concrete FK, so narrow for mypy.
    assert isinstance(field, ForeignKey)
    assert field.column == expected_column


@pytest.mark.parametrize(
    ("model", "expected_names"),
    [
        (ForecastCellWeather, {"bulletins_f_forecas_e18a91_idx"}),
        (
            ForecastCellWeatherHistory,
            {"bulletins_f_forecas_95f2d5_idx", "bulletins_f_valid_f_dd24eb_idx"},
        ),
    ],
)
def test_indexes_keep_their_pre_rename_names(
    model: type[Model], expected_names: set[str]
) -> None:
    """Index names are pinned, so the field rename did not rebuild them.

    An unnamed ``Index`` derives its name from the fields it covers, so
    renaming ``forecast_point`` to ``forecast_cell`` would have changed
    every one of these — a DROP/CREATE pair, and a real lock.
    """
    assert {index.name for index in model._meta.indexes} == expected_names


@pytest.mark.parametrize(
    ("app_label", "migration"),
    [
        ("weather", "0003_rename_forecastpoint_to_forecastcell"),
        ("weather", "0004_alter_forecastcellweather_options_and_more"),
        ("favourites", "0007_alter_favourite_forecast_point"),
    ],
)
def test_rename_migration_carries_no_database_operations(
    app_label: str, migration: str
) -> None:
    """Every SNOW-703 migration is state-only, top to bottom.

    The premise of the whole ticket, asserted rather than checked by hand
    once: code moved, the database did not. Any operation that is not a
    ``SeparateDatabaseAndState`` with an empty ``database_operations``
    list would emit DDL — a table rename, a column rename or a DROP/CREATE
    INDEX pair on the two largest tables in the schema.

    Asserted structurally rather than through ``sqlmigrate`` because the
    SQLite schema editor refuses to run inside a test transaction, and
    because this states the invariant directly.
    """
    import importlib

    module = importlib.import_module(f"apps.{app_label}.migrations.{migration}")

    operations = module.Migration.operations
    assert operations, f"{migration} has no operations"
    for operation in operations:
        assert isinstance(operation, migrations.SeparateDatabaseAndState), (
            f"{migration}: {type(operation).__name__} would emit DDL"
        )
        assert operation.database_operations == []
