"""
tests/ingest/test_apps.py — Smoke tests for the ingest app shell (SNOW-1248).

The app is registered under its own label and owns exactly the tables
SNOW-1250 gave it.
"""

from django.apps import apps

from apps.ingest.apps import IngestConfig


def test_ingest_app_is_registered() -> None:
    """The app config is installed under the ``ingest`` label."""
    config = apps.get_app_config("ingest")
    assert isinstance(config, IngestConfig)
    assert config.name == "apps.ingest"
    assert config.default_auto_field == "django.db.models.BigAutoField"


def test_ingest_app_owns_its_own_tables() -> None:
    """SourceBulletin (SNOW-1250); places and weather (SNOW-1253); passes and resorts (SNOW-1271); day ratings (SNOW-1270); ski lines (SNOW-1276)."""
    models = apps.get_app_config("ingest").get_models()
    assert sorted(model.__name__ for model in models) == [
        "SourceBulletin",
        "SourceDayRating",
        "SourceLocation",
        "SourcePass",
        "SourceResort",
        "SourceSkiSegment",
        "SourceSkiTour",
        "SourceWeather",
    ]
