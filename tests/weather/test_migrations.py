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
"""

import pytest
from django.db.models import Model

from apps.weather.models import (
    ForecastPoint,
    ForecastPointWeather,
    ForecastPointWeatherHistory,
    WeatherSnapshot,
)


@pytest.mark.parametrize(
    ("model", "expected_table"),
    [
        (WeatherSnapshot, "bulletins_weathersnapshot"),
        (ForecastPoint, "bulletins_forecastpoint"),
        (ForecastPointWeather, "bulletins_forecastpointweather"),
        (ForecastPointWeatherHistory, "bulletins_forecastpointweatherhistory"),
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
            ForecastPoint,
            ForecastPointWeather,
            ForecastPointWeatherHistory,
        )
    }
    assert not any(table.startswith("weather_") for table in tables)
