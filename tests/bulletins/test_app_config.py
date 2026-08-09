"""Smoke tests for the bulletins app shell (SNOW-90)."""

from django.apps import apps

from apps.bulletins.apps import BulletinsConfig


def test_bulletins_app_is_registered() -> None:
    config = apps.get_app_config("bulletins")
    assert isinstance(config, BulletinsConfig)
    assert config.name == "apps.bulletins"
    assert config.label == "bulletins"
    assert config.default_auto_field == "django.db.models.BigAutoField"


def test_bulletins_app_owns_the_expected_models() -> None:
    """SNOW-92 moved Bulletin / RegionBulletin / PipelineRun / RegionDayRating here.

    SNOW-217 adds BulletinShare and BulletinShareClick.
    SNOW-323 adds BulletinGrouping.
    SNOW-654 moved the four Open-Meteo models out to ``apps.weather``, so
    they are deliberately absent from this set.
    """
    config = apps.get_app_config("bulletins")
    model_names = {m.__name__ for m in config.get_models()}
    assert model_names == {
        "Bulletin",
        "BulletinGrouping",
        "RegionBulletin",
        "PipelineRun",
        "RegionDayRating",
        "BulletinShare",
        "BulletinShareClick",
    }
