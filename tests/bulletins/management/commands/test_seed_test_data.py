"""
tests/bulletins/management/commands/test_seed_test_data.py — Tests for seed_test_data.

Covers:
  - Selection flags are required and mutually exclusive (--all / --include /
    --exclude); an unknown model name or no selection is rejected.
  - Dry-run (no --commit) writes nothing.
  - --commit --all creates the full navigable dataset via the factories, once the
    region fixtures are pre-loaded: 178 region-scoped rows (RegionBulletin /
    RegionDayRating / WeatherSnapshot) but only 39 Bulletins, since the map date
    is covered by 10 multi-region groups (SNOW-534).
  - The map-date bulletins group contiguous micro-regions, cover every region
    exactly once, and each gets a dissolved BulletinGrouping; CH-4115's April
    detail bulletins stay single-region.
  - ``_contiguous_groups`` partitions deterministically, respects the size cap,
    and only ever groups regions that touch.
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

import json
from collections.abc import Iterator
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from pytest_django import DjangoDbBlocker

from apps.accounts.models import Account, Subscription
from apps.bulletins.management.commands.seed_test_data import (
    _EAWS_CH_FIXTURE,
    DEV_USER_PASSWORD,
    MAP_DATE,
    NORMAL_USER_EMAIL,
    SUBSCRIBED_REGION_ID,
    SUPERUSER_EMAIL,
    _adjacency_from_fixture,
    _contiguous_groups,
)
from apps.bulletins.models import (
    Bulletin,
    BulletinGrouping,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.services.render_model import RENDER_MODEL_VERSION
from apps.favourites.models import Favourite
from apps.weather.models import (
    ForecastCell,
    ForecastCellWeather,
    WeatherSnapshot,
)

User = get_user_model()

# The point-weather layer seeded alongside the bulletin dataset.
_EXPECTED_FORECAST_POINTS = 5
_EXPECTED_FORECAST_POINT_WEATHER = 150  # 5 points × 30 April dates
_EXPECTED_FAVOURITES = 5

# Region-scoped bulletin-layer rows: one per (region, date) pair — 149
# map-coverage regions on MAP_DATE + 29 CH-4115 detail days. Unchanged by
# SNOW-534's grouping: every region still gets its own RegionBulletin,
# RegionDayRating and WeatherSnapshot.
_EXPECTED_TOTAL = 178

# Bulletin rows. Far lower than _EXPECTED_TOTAL since SNOW-534: the map date is
# covered by contiguous GROUPS of up to _GROUP_TARGET_SIZE micro-regions
# (10 groups across the 149 CH regions), plus the 29 single-region CH-4115
# detail-day bulletins. One BulletinGrouping is computed per bulletin.
_EXPECTED_BULLETINS = 39
_EXPECTED_MAP_DATE_GROUPS = 10


@pytest.fixture(scope="class")
def _regions(
    django_db_setup: None,
    django_db_blocker: DjangoDbBlocker,
) -> Iterator[None]:
    """Load the region reference data once per class, then clean the worker DB.

    ``loaddata eaws_CH resorts`` costs ~0.4s, and it used to run per *test* —
    around thirty times in this module alone. Class scope commits the rows
    outside any test transaction, so each test's own writes still roll back
    around them; the ``flush`` on teardown puts the worker database back to
    empty for whatever runs next, which is what lets
    ``TestUserSeedingWithoutRegion`` still observe a database with no
    CH-4115 in it. Mirrors the ``_regions`` fixture in
    ``test_seed_test_week.py``.
    """
    del django_db_setup
    with django_db_blocker.unblock():
        call_command("loaddata", "eaws_CH", "resorts", verbosity=0)

    yield

    with django_db_blocker.unblock():
        call_command("flush", interactive=False, verbosity=0)


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
            "forecastcell",
            "forecastcellweather",
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
@pytest.mark.xdist_group(name="seed_data_dry_run")
@pytest.mark.usefixtures("_regions")
class TestDryRun:
    """Without --commit the command writes nothing.

    Region data is pre-loaded — --all seeds the USER model, which needs CH-4115.
    """

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
@pytest.mark.xdist_group(name="seed_data_commit")
@pytest.mark.usefixtures("_regions")
class TestCommit:
    """--commit persists the dataset. Region fixtures are pre-loaded."""

    def test_all_creates_full_dataset(self) -> None:
        """--commit --all creates 178 region rows across 39 grouped bulletins."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        assert Bulletin.objects.count() == _EXPECTED_BULLETINS
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

    def test_map_date_bulletins_cover_regions_in_groups(self) -> None:
        """The map date is covered by 10 multi-region bulletins (SNOW-534)."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        map_date_bulletins = Bulletin.objects.filter(valid_from__date=MAP_DATE)
        assert map_date_bulletins.count() == _EXPECTED_MAP_DATE_GROUPS
        # Every CH micro-region is still covered, exactly once — the grouping
        # changes how many bulletins there are, not the region coverage.
        region_ids = list(
            RegionBulletin.objects.filter(bulletin__in=map_date_bulletins).values_list(
                "region__region_id", flat=True
            )
        )
        assert len(region_ids) == len(set(region_ids))
        assert len(region_ids) == _EXPECTED_TOTAL - 29
        # And at least one bulletin genuinely spans several regions, which is
        # the whole point — a pass with 10 single-region bulletins would be a
        # regression this assertion catches.
        biggest = max(b.regions.count() for b in map_date_bulletins)
        assert biggest > 1

    def test_detail_bulletins_stay_single_region(self) -> None:
        """CH-4115's April detail bulletins cover that region alone."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        detail = Bulletin.objects.filter(valid_from__date=date(2026, 4, 9))
        assert detail.count() == 1
        links = RegionBulletin.objects.filter(bulletin=detail.get())
        assert [link.region.region_id for link in links] == ["CH-4115"]

    def test_bulletin_groupings_are_seeded_for_every_bulletin(self) -> None:
        """Each bulletin gets a dissolved BulletinGrouping with real geometry."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        assert BulletinGrouping.objects.count() == _EXPECTED_BULLETINS
        for grouping in BulletinGrouping.objects.filter(target_date=MAP_DATE):
            assert grouping.boundary["type"] in {"Polygon", "MultiPolygon"}
            assert grouping.countries == ["CH"]

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
        assert Bulletin.objects.count() == _EXPECTED_BULLETINS
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
        assert Bulletin.objects.count() == _EXPECTED_BULLETINS
        assert RegionBulletin.objects.count() == _EXPECTED_TOTAL
        assert WeatherSnapshot.objects.count() == 0

    def test_include_regionbulletin_pulls_in_bulletin_prerequisite(self) -> None:
        """--include regionbulletin auto-creates the Bulletin prerequisite."""
        call_command(
            "seed_test_data", "--include", "regionbulletin", commit=True, verbosity=0
        )
        assert Bulletin.objects.count() == _EXPECTED_BULLETINS
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
        """--all seeds the stage-2 ForecastCell/weather/Favourite layer."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        assert ForecastCell.objects.count() == _EXPECTED_FORECAST_POINTS
        assert ForecastCellWeather.objects.count() == _EXPECTED_FORECAST_POINT_WEATHER
        assert Favourite.objects.count() == _EXPECTED_FAVOURITES

    def test_include_forecastcell_only(self) -> None:
        """--include forecastcell seeds points but no weather or favourites."""
        call_command(
            "seed_test_data", "--include", "forecastcell", commit=True, verbosity=0
        )
        assert ForecastCell.objects.count() == _EXPECTED_FORECAST_POINTS
        assert ForecastCellWeather.objects.count() == 0
        assert Favourite.objects.count() == 0

    def test_include_forecastcellweather_pulls_in_forecast_cell(self) -> None:
        """--include forecastcellweather auto-creates its ForecastCell prerequisite."""
        call_command(
            "seed_test_data",
            "--include",
            "forecastcellweather",
            commit=True,
            verbosity=0,
        )
        assert ForecastCell.objects.count() == _EXPECTED_FORECAST_POINTS
        assert ForecastCellWeather.objects.count() == _EXPECTED_FORECAST_POINT_WEATHER
        assert Favourite.objects.count() == 0

    def test_include_favourite_pulls_in_forecast_point(self) -> None:
        """--include favourite auto-creates the ForecastCell prerequisite."""
        call_command(
            "seed_test_data", "--include", "favourite", commit=True, verbosity=0
        )
        assert ForecastCell.objects.count() == _EXPECTED_FORECAST_POINTS
        assert Favourite.objects.count() == _EXPECTED_FAVOURITES
        assert ForecastCellWeather.objects.count() == 0

    def test_favourites_reference_seeded_points(self) -> None:
        """Each Favourite points at a seeded ForecastCell with matching coords."""
        call_command("seed_test_data", "--all", commit=True, verbosity=0)
        seeded_point_ids = set(ForecastCell.objects.values_list("pk", flat=True))
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
@pytest.mark.xdist_group(name="seed_data_users")
@pytest.mark.usefixtures("_regions")
class TestUserSeeding:
    """The USER model seeds the two named dev accounts (from seed_dev_users).

    Region fixtures are pre-loaded because the normal user is subscribed to the
    CH-4115 MicroRegion.
    """

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
        assert ForecastCell.objects.count() == 0
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
@pytest.mark.xdist_group(name="seed_data_no_region")
class TestUserSeedingWithoutRegion:
    """USER seeding requires the CH-4115 MicroRegion to exist."""

    def test_missing_ch4115_region_errors_cleanly(self) -> None:
        """--include user without region fixtures raises a clear CommandError."""
        with pytest.raises(CommandError, match=SUBSCRIBED_REGION_ID):
            call_command(
                "seed_test_data", "--include", "user", commit=True, verbosity=0
            )


class TestContiguousGroups:
    """``_contiguous_groups`` partitions regions into touching groups (SNOW-534)."""

    # A 2x3 grid: a-b-c on the top row, d-e-f beneath, each cell touching its
    # horizontal and vertical neighbours.
    GRID = {
        "a": {"b", "d"},
        "b": {"a", "c", "e"},
        "c": {"b", "f"},
        "d": {"a", "e"},
        "e": {"b", "d", "f"},
        "f": {"c", "e"},
    }

    def test_every_region_appears_exactly_once(self) -> None:
        """The partition covers the input with no duplicates and no drops."""
        groups = _contiguous_groups(list(self.GRID), self.GRID, target_size=2)
        flat = [region for group in groups for region in group]
        assert sorted(flat) == sorted(self.GRID)
        assert len(flat) == len(set(flat))

    def test_groups_respect_the_size_cap(self) -> None:
        """No group exceeds ``target_size``."""
        groups = _contiguous_groups(list(self.GRID), self.GRID, target_size=3)
        assert max(len(group) for group in groups) <= 3

    def test_every_group_is_connected(self) -> None:
        """Each region past the first touches one already in its group.

        Contiguity is the property the layer depends on: ``unary_union`` over a
        disconnected set yields a MultiPolygon of separate outlines, which reads
        as no grouping at all.
        """
        for group in _contiguous_groups(list(self.GRID), self.GRID, target_size=4):
            reached = {group[0]}
            for region in group[1:]:
                assert self.GRID[region] & reached, (
                    f"{region} touches nothing in {group}"
                )
                reached.add(region)

    def test_partition_is_deterministic(self) -> None:
        """Repeated calls produce identical groups, so bulletin IDs are stable."""
        first = _contiguous_groups(list(self.GRID), self.GRID, target_size=2)
        second = _contiguous_groups(sorted(self.GRID, reverse=True), self.GRID, 2)
        assert first == second

    def test_isolated_region_forms_its_own_group(self) -> None:
        """A region with no unassigned neighbours is a group of one."""
        adjacency = {**self.GRID, "z": set()}
        groups = _contiguous_groups([*self.GRID, "z"], adjacency, target_size=4)
        assert ["z"] in groups

    def test_real_fixture_partitions_into_ten_groups(self) -> None:
        """The CH fixture yields the group count the seeder's tests assume.

        Guards the tuned ``_GROUP_TARGET_SIZE``: a fixture change that shifts
        the adjacency graph would otherwise silently change how many bulletins
        the seeder creates.
        """
        region_data = json.loads(_EAWS_CH_FIXTURE.read_text())
        region_ids = [
            row["fields"]["region_id"]
            for row in region_data
            if row["model"] == "regions.microregion"
        ]
        groups = _contiguous_groups(region_ids, _adjacency_from_fixture(region_data))
        assert len(groups) == _EXPECTED_MAP_DATE_GROUPS
        assert sum(len(group) for group in groups) == len(region_ids)


class TestAdjacencyFromFixture:
    """``_adjacency_from_fixture`` flattens and symmetrises the fixture graph."""

    def test_natural_key_lists_are_flattened_and_symmetrised(self) -> None:
        """Neighbour entries are one-element natural keys; edges go both ways."""
        rows = [
            {
                "model": "regions.microregion",
                "fields": {"region_id": "CH-1", "neighbours": [["CH-2"]]},
            },
            {
                "model": "regions.microregion",
                "fields": {"region_id": "CH-2", "neighbours": []},
            },
            {"model": "regions.majorregion", "fields": {"prefix": "CH-1x"}},
        ]
        adjacency = _adjacency_from_fixture(rows)
        assert adjacency["CH-1"] == {"CH-2"}
        # CH-2 lists no neighbours, but the edge exists from CH-1's side.
        assert adjacency["CH-2"] == {"CH-1"}
