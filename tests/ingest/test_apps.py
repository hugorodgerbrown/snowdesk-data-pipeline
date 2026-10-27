"""
tests/ingest/test_apps.py — Smoke tests for the ingest app shell (SNOW-1248).

The app is registered under its own label and, until SNOW-1250 gives it a
table, owns no models.
"""

from django.apps import apps

from apps.ingest.apps import IngestConfig


def test_ingest_app_is_registered() -> None:
    """The app config is installed under the ``ingest`` label."""
    config = apps.get_app_config("ingest")
    assert isinstance(config, IngestConfig)
    assert config.name == "apps.ingest"
    assert config.default_auto_field == "django.db.models.BigAutoField"


def test_ingest_app_owns_no_models_yet() -> None:
    """Its tables arrive with SNOW-1250 and SNOW-1253."""
    assert list(apps.get_app_config("ingest").get_models()) == []
