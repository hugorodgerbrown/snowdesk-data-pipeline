# tests/bulletins/management/commands/test_relink_bulletin_regions.py —
# Tests for the relink_bulletin_regions management command.
#
# Reproduces SNOW-1187's production state — an ALBINA bulletin whose region
# links point at regions its payload never names, carrying their day ratings
# with them — and asserts the command puts the links, the ratings and the
# coverage back where the payload says, under the usual command contract
# (read-only by default, idempotent, never erasing what it cannot resolve).
"""Tests for the relink_bulletin_regions management command."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.management import CommandError, call_command

from apps.bulletins.models import (
    Bulletin,
    BulletinGrouping,
    RegionBulletin,
    RegionDayRating,
)
from apps.bulletins.services.coverage import covered_region_ids
from apps.bulletins.services.day_rating import refresh_day_ratings
from apps.bulletins.services.apply import upsert_bulletin
from apps.core.models import CommandRun
from apps.regions.models import MicroRegion, SubRegion
from tests.factories import MicroRegionFactory, PipelineRunFactory, SubRegionFactory

_FIXTURE = (
    Path(__file__).resolve().parents[3] / "fixtures" / "albina_sample_bulletin.json"
)

# Two South Tyrol regions the sample bulletin does NOT name — where the
# shifted links pointed in production.
_WRONG_IDS = ("IT-32-BZ-11", "IT-32-BZ-12")


def _sample() -> dict[str, Any]:
    """Return the committed ALBINA sample bulletin's CAAML properties."""
    data: dict[str, Any] = json.loads(_FIXTURE.read_text())
    return data


def _region(region_id: str) -> MicroRegion:
    """Create a micro region under a sub-region shared by its prefix.

    ``SubRegion.prefix`` is unique and the factory derives it from the first
    five characters, so every AT-07 region has to share one parent.

    Args:
        region_id: The EAWS region identifier.

    Returns:
        The created MicroRegion.

    """
    prefix = region_id[:5]
    subregion = SubRegion.objects.filter(
        prefix=prefix
    ).first() or SubRegionFactory.create(prefix=prefix)
    return MicroRegionFactory.create(region_id=region_id, subregion=subregion)


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    """Coverage is cached; every test reads it fresh."""
    cache.clear()


@pytest.fixture
def mislinked() -> Bulletin:
    """Ingest the sample bulletin, then shift its links onto the wrong regions.

    The ratings are refreshed for the wrong regions too, so the starting
    state is production's: the wrong regions rated and covered from a
    bulletin that never names them.

    Returns:
        The mislinked bulletin.

    """
    raw = _sample()
    for entry in raw["regions"]:
        _region(entry["regionID"])
    wrong = [_region(region_id) for region_id in _WRONG_IDS]

    upsert_bulletin(raw, PipelineRunFactory.create())
    bulletin = Bulletin.objects.get(bulletin_id=raw["bulletinID"])

    RegionBulletin.objects.filter(bulletin=bulletin).delete()
    for region in wrong:
        RegionBulletin.objects.create(
            bulletin=bulletin, region=region, region_name_at_time="Lechtal Alps West"
        )
    day = bulletin.target_date
    assert day is not None
    refresh_day_ratings({(region, day) for region in wrong})
    return bulletin


def _linked_ids(bulletin: Bulletin) -> set[str]:
    """Return the region_ids a bulletin is linked to."""
    return set(bulletin.regions.values_list("region_id", flat=True))


@pytest.mark.django_db
class TestRelink:
    """The links, ratings and coverage follow the payload."""

    def test_links_follow_the_payload(self, mislinked: Bulletin) -> None:
        """Every link names a region the payload lists, and none other."""
        call_command(
            "relink_bulletin_regions", source="albina", commit=True, verbosity=0
        )

        expected = {entry["regionID"] for entry in _sample()["regions"]}
        assert _linked_ids(mislinked) == expected

    def test_ratings_move_off_the_wrong_regions(self, mislinked: Bulletin) -> None:
        """The wrong regions lose their rows; the right ones are sourced from it."""
        assert RegionDayRating.objects.filter(region__region_id__in=_WRONG_IDS).exists()

        call_command(
            "relink_bulletin_regions", source="albina", commit=True, verbosity=0
        )

        assert not RegionDayRating.objects.filter(
            region__region_id__in=_WRONG_IDS
        ).exists()
        right = RegionDayRating.objects.filter(region__region_id="AT-07-14-01").get()
        assert right.source_bulletin_id == mislinked.pk

    def test_coverage_follows(self, mislinked: Bulletin) -> None:
        """The wrong regions read as unsupported, the payload's as covered."""
        call_command(
            "relink_bulletin_regions", source="albina", commit=True, verbosity=0
        )

        covered = covered_region_ids()
        assert "AT-07-14-01" in covered
        assert not covered & set(_WRONG_IDS)

    def test_outline_follows(self, mislinked: Bulletin) -> None:
        """The dissolved outline is rebuilt over the payload's regions."""
        expected = sorted(entry["regionID"] for entry in _sample()["regions"])
        for i, region_id in enumerate(expected):
            square = [[i, 0], [i + 1, 0], [i + 1, 1], [i, 1], [i, 0]]
            MicroRegion.objects.filter(region_id=region_id).update(
                boundary={"type": "Polygon", "coordinates": [square]}
            )

        assert not BulletinGrouping.objects.filter(bulletin=mislinked).exists()

        call_command(
            "relink_bulletin_regions", source="albina", commit=True, verbosity=0
        )

        assert BulletinGrouping.objects.filter(bulletin=mislinked).exists()

    def test_idempotent(
        self, mislinked: Bulletin, capsys: pytest.CaptureFixture
    ) -> None:
        """A second run finds nothing to relink."""
        call_command(
            "relink_bulletin_regions", source="albina", commit=True, verbosity=0
        )
        capsys.readouterr()

        call_command("relink_bulletin_regions", source="albina", commit=True)

        assert "Relinked 0, already current 1" in capsys.readouterr().out

    def test_linked_region_keeps_a_sourceless_row(self, mislinked: Bulletin) -> None:
        """A region the bulletin IS linked to keeps its row, even unrated.

        A stale render model leaves ``recompute_region_day`` no candidate, so
        it writes a sourceless ``no_rating`` row — but the region is linked,
        so it is covered and the row stays; only the wrong regions' go.
        """
        Bulletin.objects.filter(pk=mislinked.pk).update(render_model_version=0)

        call_command(
            "relink_bulletin_regions", source="albina", commit=True, verbosity=0
        )

        right = RegionDayRating.objects.get(region__region_id="AT-07-14-01")
        assert right.source_bulletin_id is None
        assert "AT-07-14-01" in covered_region_ids()
        assert not RegionDayRating.objects.filter(
            region__region_id__in=_WRONG_IDS
        ).exists()


@pytest.mark.django_db
class TestSafety:
    """Nothing is written that the payload cannot justify."""

    def test_read_only_by_default(
        self, mislinked: Bulletin, capsys: pytest.CaptureFixture
    ) -> None:
        """A bare run reports the bulletin and leaves the wrong links alone."""
        call_command("relink_bulletin_regions", source="albina")

        assert "Would relink 1" in capsys.readouterr().out
        assert _linked_ids(mislinked) == set(_WRONG_IDS)

    def test_other_sources_untouched(self, mislinked: Bulletin) -> None:
        """Scoped to one provider: an SLF run leaves the ALBINA bulletin alone."""
        call_command("relink_bulletin_regions", source="slf", commit=True, verbosity=0)

        assert _linked_ids(mislinked) == set(_WRONG_IDS)

    def test_unresolvable_payload_is_left_alone(self, mislinked: Bulletin) -> None:
        """A payload none of whose regions exist must not erase the links."""
        raw_data = dict(mislinked.raw_data)
        raw_data["properties"] = {
            **raw_data["properties"],
            "regions": [{"regionID": "AT-99-99", "name": "Nowhere"}],
        }
        Bulletin.objects.filter(pk=mislinked.pk).update(raw_data=raw_data)

        call_command(
            "relink_bulletin_regions", source="albina", commit=True, verbosity=0
        )

        assert _linked_ids(mislinked) == set(_WRONG_IDS)

    def test_failed_grouping_rolls_back_and_retries(self, mislinked: Bulletin) -> None:
        """A failure after the links change rolls them back, so a re-run retries.

        Were the links kept, the retry would see the bulletin as current and
        never rebuild its ratings or outline.
        """
        target = (
            "apps.bulletins.management.commands.relink_bulletin_regions"
            ".compute_bulletin_grouping_boundary"
        )
        with (
            patch(target, side_effect=RuntimeError("dissolve failed")),
            pytest.raises(CommandError, match="1 bulletin"),
        ):
            call_command(
                "relink_bulletin_regions", source="albina", commit=True, verbosity=0
            )

        assert _linked_ids(mislinked) == set(_WRONG_IDS)
        assert RegionDayRating.objects.filter(region__region_id__in=_WRONG_IDS).exists()

        call_command(
            "relink_bulletin_regions", source="albina", commit=True, verbosity=0
        )

        assert "AT-07-14-01" in _linked_ids(mislinked)
        assert not RegionDayRating.objects.filter(
            region__region_id__in=_WRONG_IDS
        ).exists()

    def test_unknown_bulletin_id_errors(self, mislinked: Bulletin) -> None:
        """Naming a bulletin the provider does not have is a usage error."""
        with pytest.raises(CommandError):
            call_command(
                "relink_bulletin_regions",
                source="albina",
                bulletin_id="nope",
                verbosity=0,
            )


@pytest.mark.django_db
class TestRunRegister:
    """Each run lands in the command run register with its counts (SNOW-1189)."""

    def test_dry_run_and_commit_run_count_the_same(self, mislinked: Bulletin) -> None:
        """The dry run's "would relink" matches the commit run's "relinked"."""
        call_command("relink_bulletin_regions", source="albina", verbosity=0)
        call_command(
            "relink_bulletin_regions", source="albina", commit=True, verbosity=0
        )

        runs = CommandRun.objects.for_command("relink_bulletin_regions")
        dry = runs.dry_runs().get()
        committed = runs.committed().get()
        assert dry.counts["relinked"] == committed.counts["relinked"] == 1
        assert set(dry.counts) == {
            "relinked",
            "current",
            "unresolved",
            "failed",
            "cleared",
        }
        assert committed.status == CommandRun.STATUS.SUCCEEDED
        assert committed.arguments["source"] == "albina"

    def test_a_failed_bulletin_makes_the_run_partial(self, mislinked: Bulletin) -> None:
        """A rolled-back bulletin is counted, and the run reads PARTIAL."""
        target = (
            "apps.bulletins.management.commands.relink_bulletin_regions"
            ".compute_bulletin_grouping_boundary"
        )
        with (
            patch(target, side_effect=RuntimeError("dissolve failed")),
            pytest.raises(CommandError),
        ):
            call_command(
                "relink_bulletin_regions", source="albina", commit=True, verbosity=0
            )

        run = CommandRun.objects.for_command("relink_bulletin_regions").get()
        assert run.status == CommandRun.STATUS.PARTIAL
        assert run.counts["failed"] == 1
        assert run.exit_code == 1
