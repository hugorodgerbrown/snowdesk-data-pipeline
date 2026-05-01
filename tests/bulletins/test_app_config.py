"""Smoke tests for the bulletins app shell (SNOW-90)."""

from django.apps import apps

from bulletins.apps import BulletinsConfig


def test_bulletins_app_is_registered():
    config = apps.get_app_config("bulletins")
    assert isinstance(config, BulletinsConfig)
    assert config.name == "bulletins"
    assert config.label == "bulletins"
    assert config.default_auto_field == "django.db.models.BigAutoField"


def test_bulletins_app_has_no_models_yet():
    """Models land in SNOW-92; the shell ships empty."""
    config = apps.get_app_config("bulletins")
    assert list(config.get_models()) == []
