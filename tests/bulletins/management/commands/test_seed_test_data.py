"""
tests/bulletins/management/commands/test_seed_test_data.py — Tests for seed_test_data.

Covers:
  - Selection flags are required and mutually exclusive (--all / --include /
    --exclude); an unknown model name or no selection is rejected.
  - Dry-run (no --commit) writes nothing.
  - --commit --all creates the full navigable dataset (178 of each bulletin-layer
    model) via the factories, once the region fixtures are pre-loaded.
  - Every seeded Bulletin has render_model_version == RENDER_MODEL_VERSION.
  - CH-4115 gets the full-April detail layer; a non-detail region gets a single
    map-date snapshot.
  - --include seeds only the named model plus its FK prerequisites; --exclude
    omits the named model.
  - --include user seeds the two named dev accounts (superuser + subscribed
    normal user, folded in from the former seed_dev_users command); --all
    includes them and the seeded Favourites are owned by the normal dev user.

seed_test_data needs the ``eaws_CH`` and ``resorts`` fixtures pre-loaded (it
wires real MicroRegion FKs), so the commit tests load them first.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from accounts.models import Account, Subscription
from bulletins.management.commands.seed_test_data import (
    DEV_USER_PASSWORD,
    NORMAL_USER_EMAIL,
    SUBSCRIBED_REGION_ID,
    SUPERUSER_EMAIL,
)
from bulletins.models import (
    Bulletin,
    ForecastPoint,
    ForecastPointWeather,
    RegionBulletin,
    RegionDayRating,
    WeatherSnapshot,
)
from bulletins.services.render_model import RENDER_MODEL_VERSION
from favourites.models import Favourite

User = get_user_model()

# The point-weather layer seeded alongside the bulletin dataset.
_EXPECTED_FORECAST_POINTS = 5
_EXPECTED_FORECAST_POINT_WEATHER = 150  # 5 points × 30 April dates
_EXPECTED_FAVOURITES = 5

# Bulletin-layer total: 149 map-coverage + 29 CH-4115 detail.
_EXPECTED_TOTAL = 178


@pytest.mark.django_db
class TestSelectionValidation:
    """The required, mutually-exclusive selection group is enforced."""

    def test_no_selection_is_rejected(self) -> None:
        """Running with none of --all/--include/--exclude exits non-zero."""
        with pytest.raises((CommandError, SystemExit)):
            call_command("seed_test_data")

    def test_two_selection_flags_are_rejected(self) -> None:
        """Passing two of the mutually-exclusive flags exits non-zero."""
        with pytest.raises((CommandError, SystemExit)):
            call_command("seed_test_data", "--all", "--include", "bulletin")

    def test_unknown_model_name_lists_available_models(self) -> None:
        """An --include value outside the enumeration errors and lists the models."""
        with pytest.raises(CommandError, match="Available models:.*weathersnapshot"):
            call_command("seed_test_data", "--include", "notamodel", "--commit")

    def test_include_without_a_model_lists_available_models(self) -> None:
        """--include with no model errors and lists the available models."""
        with pytest.raises(CommandError, match="at least one model.*Available models"):
            call_command("seed_test_data", "--include", "--commit")

    def test_exclude_without_a_model_lists_available_models(self) -> None:
        """--exclude with no model errors and lists the available models."""
        with pytest.raises(CommandError, match="at least one model.*Available models"):
            call_command("seed_test_data", "--exclude", "--commit")

    def test_help_lists_the_model_choices(self, capsys: pytest.CaptureFixture) -> None:
        """--help surfaces the exact seedable model names."""
        with pytest.raises(SystemExit):
            call_command("seed_test_data", "--help")
        out = capsys.readouterr().out
        for name in (
            "bulletin",
            "regionbulletin",
            "regiondayrating",
            "weathersnapshot",
            "forecastpoint",
            "forecastpointweather",
            "favourite",
            "user",
        ):
            assert name in out

    @override_settings(DEBUG=False)
    def test_refuses_to_run_when_debug_is_false(self) -> None:
        """The command refuses to touch a non-DEBUG (production) database."""
        with pytest.raises(CommandError, match="DEBUG=True"):
            call_command("seed_test_data", "--all", commit=True)


@pytest.mark.django_db
class TestDryRun:
    """Without --commit the command writes nothing."""

    @pytest.fixture(autouse=True)
    def _load_region_fixtures(self) -> None:
        """Pre-load region data — --all seeds the USER model, which needs CH-4115."""
        call_command("loaddata", "eaws_CH", "resorts", verbosity=0)

    def test_dry_run_writes_no_rows(self) -> None:
        """--all without --commit leaves the DB untouched (incl. the seeded users)."""
        call_command("seed_test_data", "--all", verbosity=0)
        assert Bulletin.objects.count() == 0
        assert WeatherSnapshot.objects.count() == 0
        # The USER layer is rolled back with everything else in dry-run.
        assert User.objects.count() == 0

    def test_dry_run_prints_read_only_banner(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """A READ-ONLY banner is printed in dry-run mode."""
        call_command("seed_test_data", "--all", verbosity=1)
        assert "READ-ONLY" in capsys.readouterr().out


@pytest.mark.django_db
class TestCommit:
    """--commit persists the dataset. Region fixtures are pre-loaded."""

    @pytest.fixture(autouse=True)
    def _load_region_fixtures(self) -> None:
        """Pre-load the region reference data seed_test_data depends on."""
        call_command("loaddata", "eaws_CH", "resorts", verbosity=0)

    def test_all_creates_full_dataset(self) -> None:
        """--commit --all creates 178 of each seeded model."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        assert Bulletin.objects.count() == _EXPECTED_TOTAL
        assert RegionBulletin.objects.count() == _EXPECTED_TOTAL
        assert RegionDayRating.objects.count() == _EXPECTED_TOTAL
        assert WeatherSnapshot.objects.count() == _EXPECTED_TOTAL

    def test_all_bulletins_have_current_render_model_version(self) -> None:
        """Every seeded Bulletin carries the current render-model version."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        for bulletin in Bulletin.objects.all():
            assert bulletin.render_model_version == RENDER_MODEL_VERSION
            assert bulletin.render_model.get("version") == RENDER_MODEL_VERSION

    def test_ch4115_has_full_april_detail(self) -> None:
        """CH-4115 gets a rating and a snapshot for every April day (30 each)."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        assert RegionDayRating.objects.filter(region__region_id="CH-4115").count() == 30
        assert WeatherSnapshot.objects.filter(region__region_id="CH-4115").count() == 30

    def test_non_detail_region_has_single_map_date_snapshot(self) -> None:
        """A non-detail region gets exactly one snapshot, on the map date."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        snapshots = WeatherSnapshot.objects.filter(region__region_id="CH-4222")
        assert snapshots.count() == 1
        assert str(snapshots.get().valid_for_date) == "2026-04-08"

    def test_weather_snapshots_use_wmo_code_1(self) -> None:
        """All seeded snapshots use WMO weather code 1."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        assert set(
            WeatherSnapshot.objects.order_by().values_list("weather_code", flat=True)
        ) == {1}

    def test_include_weathersnapshot_only(self) -> None:
        """--include weathersnapshot seeds snapshots and nothing else (no deps)."""
        call_command(
            "seed_test_data", "--include", "weathersnapshot", commit=True, verbosity=0
        )
        assert WeatherSnapshot.objects.count() == _EXPECTED_TOTAL
        assert Bulletin.objects.count() == 0
        assert RegionDayRating.objects.count() == 0

    def test_exclude_weathersnapshot(self) -> None:
        """--exclude weathersnapshot seeds everything else, no snapshots."""
        call_command(
            "seed_test_data", "--exclude", "weathersnapshot", commit=True, verbosity=0
        )
        assert WeatherSnapshot.objects.count() == 0
        assert Bulletin.objects.count() == _EXPECTED_TOTAL
        assert RegionBulletin.objects.count() == _EXPECTED_TOTAL
        assert RegionDayRating.objects.count() == _EXPECTED_TOTAL

    def test_include_regiondayrating_produces_ratings(self) -> None:
        """--include regiondayrating pulls in RegionBulletin so ratings are non-zero.

        apply_bulletin_day_ratings reads bulletin.regions.all() (the RegionBulletin
        M2M); without those links it would silently create zero ratings.
        """
        call_command(
            "seed_test_data", "--include", "regiondayrating", commit=True, verbosity=0
        )
        assert RegionDayRating.objects.count() == _EXPECTED_TOTAL
        assert Bulletin.objects.count() == _EXPECTED_TOTAL
        assert RegionBulletin.objects.count() == _EXPECTED_TOTAL
        assert WeatherSnapshot.objects.count() == 0

    def test_include_regionbulletin_pulls_in_bulletin_prerequisite(self) -> None:
        """--include regionbulletin auto-creates the Bulletin prerequisite."""
        call_command(
            "seed_test_data", "--include", "regionbulletin", commit=True, verbosity=0
        )
        assert Bulletin.objects.count() == _EXPECTED_TOTAL
        assert RegionBulletin.objects.count() == _EXPECTED_TOTAL
        assert RegionDayRating.objects.count() == 0
        assert WeatherSnapshot.objects.count() == 0

    def test_prerequisite_note_is_printed(self, capsys: pytest.CaptureFixture) -> None:
        """Pulled-in prerequisites are reported to the caller."""
        call_command(
            "seed_test_data", "--include", "regionbulletin", commit=True, verbosity=1
        )
        assert "bulletin" in capsys.readouterr().out

    def test_all_seeds_the_new_models(self) -> None:
        """--all seeds the stage-2 ForecastPoint/weather/Favourite layer."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        assert ForecastPoint.objects.count() == _EXPECTED_FORECAST_POINTS
        assert ForecastPointWeather.objects.count() == _EXPECTED_FORECAST_POINT_WEATHER
        assert Favourite.objects.count() == _EXPECTED_FAVOURITES

    def test_include_forecastpoint_only(self) -> None:
        """--include forecastpoint seeds points but no weather or favourites."""
        call_command(
            "seed_test_data", "--include", "forecastpoint", commit=True, verbosity=0
        )
        assert ForecastPoint.objects.count() == _EXPECTED_FORECAST_POINTS
        assert ForecastPointWeather.objects.count() == 0
        assert Favourite.objects.count() == 0

    def test_include_forecastpointweather_pulls_in_forecast_point(self) -> None:
        """--include forecastpointweather auto-creates its ForecastPoint prerequisite."""
        call_command(
            "seed_test_data",
            "--include",
            "forecastpointweather",
            commit=True,
            verbosity=0,
        )
        assert ForecastPoint.objects.count() == _EXPECTED_FORECAST_POINTS
        assert ForecastPointWeather.objects.count() == _EXPECTED_FORECAST_POINT_WEATHER
        assert Favourite.objects.count() == 0

    def test_include_favourite_pulls_in_forecast_point(self) -> None:
        """--include favourite auto-creates the ForecastPoint prerequisite."""
        call_command(
            "seed_test_data", "--include", "favourite", commit=True, verbosity=0
        )
        assert ForecastPoint.objects.count() == _EXPECTED_FORECAST_POINTS
        assert Favourite.objects.count() == _EXPECTED_FAVOURITES
        assert ForecastPointWeather.objects.count() == 0

    def test_favourites_reference_seeded_points(self) -> None:
        """Each Favourite points at a seeded ForecastPoint with matching coords."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        seeded_point_ids = set(ForecastPoint.objects.values_list("pk", flat=True))
        for favourite in Favourite.objects.select_related("forecast_point"):
            assert favourite.forecast_point.pk in seeded_point_ids
            assert favourite.latitude == favourite.forecast_point.latitude

    def test_reseeding_populated_db_errors_cleanly(self) -> None:
        """Re-seeding a populated DB raises CommandError, not a raw IntegrityError."""
        call_command(
            "seed_test_data", "--include", "bulletin", commit=True, verbosity=0
        )
        with pytest.raises(CommandError, match="empty database"):
            call_command(
                "seed_test_data", "--include", "bulletin", commit=True, verbosity=0
            )


@pytest.mark.django_db
class TestUserSeeding:
    """The USER model seeds the two named dev accounts (from seed_dev_users).

    Region fixtures are pre-loaded because the normal user is subscribed to the
    CH-4115 MicroRegion.
    """

    @pytest.fixture(autouse=True)
    def _load_region_fixtures(self) -> None:
        """Pre-load the region reference data the CH-4115 subscription needs."""
        call_command("loaddata", "eaws_CH", "resorts", verbosity=0)

    def test_include_user_creates_superuser(self) -> None:
        """--include user creates a superuser with is_staff and is_superuser."""
        call_command("seed_test_data", "--include", "user", commit=True, verbosity=0)
        user = User.objects.get(username=SUPERUSER_EMAIL.lower())
        assert user.is_staff
        assert user.is_superuser
        assert user.email == SUPERUSER_EMAIL.lower()
        assert user.check_password(DEV_USER_PASSWORD)

    def test_include_user_creates_subscribed_normal_user(self) -> None:
        """--include user creates a verified normal user subscribed to CH-4115."""
        call_command("seed_test_data", "--include", "user", commit=True, verbosity=0)
        user = User.objects.get(username=NORMAL_USER_EMAIL.lower())
        assert not user.is_staff
        assert user.email == NORMAL_USER_EMAIL.lower()
        assert user.check_password(DEV_USER_PASSWORD)
        account = Account.objects.get(user=user)
        assert account.is_verified
        sub = Subscription.objects.get(
            account=account, region__region_id=SUBSCRIBED_REGION_ID
        )
        assert sub.geo_match_kind == Subscription.GeoMatchKind.IN_REGION

    def test_include_user_seeds_nothing_else(self) -> None:
        """--include user creates only the accounts, no bulletin/point rows."""
        call_command("seed_test_data", "--include", "user", commit=True, verbosity=0)
        assert Bulletin.objects.count() == 0
        assert ForecastPoint.objects.count() == 0
        assert Favourite.objects.count() == 0

    def test_include_user_is_idempotent(self) -> None:
        """--include user twice creates no duplicate users or subscriptions.

        User seeding uses update_or_create / get_or_create, so unlike the
        bulletin layer it can be re-run without hitting the empty-DB guard.
        """
        call_command("seed_test_data", "--include", "user", commit=True, verbosity=0)
        call_command("seed_test_data", "--include", "user", commit=True, verbosity=0)
        assert User.objects.filter(username=SUPERUSER_EMAIL.lower()).count() == 1
        assert User.objects.filter(username=NORMAL_USER_EMAIL.lower()).count() == 1
        account = Account.objects.get(user__username=NORMAL_USER_EMAIL.lower())
        assert (
            Subscription.objects.filter(
                account=account, region__region_id=SUBSCRIBED_REGION_ID
            ).count()
            == 1
        )

    def test_all_seeds_dev_users(self) -> None:
        """--all includes the two named dev accounts."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        assert User.objects.filter(username=SUPERUSER_EMAIL.lower()).exists()
        assert User.objects.filter(username=NORMAL_USER_EMAIL.lower()).exists()

    def test_include_favourite_pulls_in_user_as_owner(self) -> None:
        """--include favourite pulls in USER and the dev user owns the Favourites."""
        call_command(
            "seed_test_data", "--include", "favourite", commit=True, verbosity=0
        )
        dev_user = User.objects.get(username=NORMAL_USER_EMAIL.lower())
        favourites = Favourite.objects.select_related("user")
        assert favourites.exists()
        for favourite in favourites:
            assert favourite.user == dev_user

    def test_favourite_prerequisite_note_reports_user(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        """--include favourite reports USER among the pulled-in prerequisites."""
        call_command(
            "seed_test_data", "--include", "favourite", commit=True, verbosity=1
        )
        assert "user" in capsys.readouterr().out


@pytest.mark.django_db
class TestUserSeedingWithoutRegion:
    """USER seeding requires the CH-4115 MicroRegion to exist."""

    def test_missing_ch4115_region_errors_cleanly(self) -> None:
        """--include user without region fixtures raises a clear CommandError."""
        with pytest.raises(CommandError, match=SUBSCRIBED_REGION_ID):
            call_command(
                "seed_test_data", "--include", "user", commit=True, verbosity=0
            )
