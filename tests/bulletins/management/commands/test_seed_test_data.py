"""
tests/bulletins/management/commands/test_seed_test_data.py — Tests for seed_test_data.

Covers:
  - Selection flags are required and mutually exclusive (--all / --include /
    --exclude); an unknown model name or no selection is rejected.
  - Dry-run (no --commit) writes nothing.
  - --commit --all creates the full build_test_data dataset (178 of each model)
    via the factories, once the region fixtures are pre-loaded.
  - Every seeded Bulletin has render_model_version == RENDER_MODEL_VERSION.
  - CH-4115 gets the full-April detail layer; a non-detail region gets a single
    map-date snapshot.
  - --include seeds only the named model plus its FK prerequisites; --exclude
    omits the named model.

Like build_test_data, seed_test_data needs the ``eaws_CH`` and ``resorts``
fixtures pre-loaded (it wires real MicroRegion FKs), so the commit tests load
them first.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from bulletins.models import Bulletin, RegionBulletin, RegionDayRating, WeatherSnapshot
from bulletins.services.render_model import RENDER_MODEL_VERSION

# The dataset build_test_data produces: 149 map-coverage + 29 CH-4115 detail.
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
        ):
            assert name in out


@pytest.mark.django_db
class TestDryRun:
    """Without --commit the command writes nothing."""

    def test_dry_run_writes_no_rows(self) -> None:
        """--all without --commit leaves the DB untouched."""
        call_command("seed_test_data", "--all", verbosity=0)
        assert Bulletin.objects.count() == 0
        assert WeatherSnapshot.objects.count() == 0

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
        """All seeded snapshots use WMO weather code 1, matching build_test_data."""
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

    def test_reseeding_populated_db_errors_cleanly(self) -> None:
        """Re-seeding a populated DB raises CommandError, not a raw IntegrityError."""
        call_command(
            "seed_test_data", "--include", "bulletin", commit=True, verbosity=0
        )
        with pytest.raises(CommandError, match="empty database"):
            call_command(
                "seed_test_data", "--include", "bulletin", commit=True, verbosity=0
            )
