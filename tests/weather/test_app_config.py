"""Smoke tests for the weather app shell (SNOW-654)."""

from django.apps import apps

from apps.weather.apps import WeatherConfig


def test_weather_app_is_registered() -> None:
    """The app is installed under the short ``weather`` label."""
    config = apps.get_app_config("weather")
    assert isinstance(config, WeatherConfig)
    assert config.name == "apps.weather"
    assert config.label == "weather"
    assert config.default_auto_field == "django.db.models.BigAutoField"


def test_weather_app_owns_the_expected_models() -> None:
    """SNOW-654 moved the four Open-Meteo models here from ``bulletins``.

    Nothing else came with them: the CAAML bulletin models stayed put,
    and there is no foreign key in either direction.
    """
    config = apps.get_app_config("weather")
    model_names = {m.__name__ for m in config.get_models()}
    assert model_names == {
        "WeatherSnapshot",
        "ForecastPoint",
        "ForecastPointWeather",
        "ForecastPointWeatherHistory",
    }
