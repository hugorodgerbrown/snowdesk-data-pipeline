"""Smoke tests for the bulletins app shell (SNOW-90)."""

from django.apps import apps

from bulletins.apps import BulletinsConfig


def test_bulletins_app_is_registered():
    config = apps.get_app_config("bulletins")
    assert isinstance(config, BulletinsConfig)
    assert config.name == "bulletins"
    assert config.label == "bulletins"
    assert config.default_auto_field == "django.db.models.BigAutoField"


def test_bulletins_app_owns_the_expected_models():
    """SNOW-92 moved Bulletin / RegionBulletin / PipelineRun / RegionDayRating here."""
    config = apps.get_app_config("bulletins")
    model_names = {m.__name__ for m in config.get_models()}
    assert model_names == {
        "Bulletin",
        "RegionBulletin",
        "PipelineRun",
        "RegionDayRating",
    }
