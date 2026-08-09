"""
tests/weather/test_logging.py — The weather app has its own logger entry.

Loggers are named after the module that creates them, so before SNOW-654
every Open-Meteo module logged under ``apps.bulletins.*`` and inherited
that app's ``LOGGING`` entry: DEBUG level, routed to ``pipeline.log``.
Moving the code to ``apps.weather.*`` renamed those loggers, and an app
with no entry of its own falls through to the root logger — which sits at
WARNING and has no ``file_pipeline`` handler.

That failure is silent in exactly the way that matters: nothing raises,
every test still passes, and the only symptom is that ``fetch_weather``'s
INFO lines stop appearing in ``pipeline.log`` on the server. These tests
exist so the next app split does not rediscover it in production.
"""

import logging
from typing import Any, cast

from django.conf import settings

PIPELINE_APPS = ("apps.weather", "apps.bulletins")


def _loggers() -> dict[str, Any]:
    """Return the ``loggers`` mapping from the active LOGGING config.

    django-stubs types ``settings.LOGGING`` as ``object``, so the cast is
    what lets these tests index into it.

    Returns:
        The configured logger entries, keyed by logger name.

    """
    config = cast(dict[str, Any], settings.LOGGING)
    return cast(dict[str, Any], config["loggers"])


def test_weather_has_a_logger_entry_of_its_own() -> None:
    """``apps.weather`` is configured, not left to inherit from root."""
    assert "apps.weather" in _loggers()


def test_weather_logging_matches_the_app_it_was_split_from() -> None:
    """Weather routes exactly where it did as part of ``bulletins``.

    The move was not meant to change any logging behaviour, so the two
    entries staying identical is the property worth asserting — it catches
    a drift in either direction, not just a missing key.
    """
    loggers = _loggers()
    weather, bulletins = (loggers[app] for app in PIPELINE_APPS)
    assert weather == bulletins


def test_weather_ingest_logs_reach_the_pipeline_file() -> None:
    """A weather module's logger carries the pipeline handler at DEBUG."""
    entry = _loggers()["apps.weather"]
    assert entry["level"] == "DEBUG"
    assert "file_pipeline" in entry["handlers"]


def test_a_weather_module_logger_does_not_fall_through_to_root() -> None:
    """The real logger a weather module builds resolves to the entry above.

    ``settings.LOGGING`` being right is necessary but not sufficient — this
    asserts the resolved hierarchy, which is what actually decides whether
    a record is emitted or dropped.
    """
    logger = logging.getLogger("apps.weather.services.weather_fetcher")
    assert logger.getEffectiveLevel() == logging.DEBUG
    assert logger.isEnabledFor(logging.INFO)
