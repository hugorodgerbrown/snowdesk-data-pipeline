"""
tests/ingest/test_day_ratings.py — The service's day ratings (SNOW-1270).

index_bulletin reads a bulletin's target day and region codes from its
CAAML; refresh_source_day_ratings picks each (region, day)'s governing
bulletin by the app's rule — the latest valid_from among current-model
bulletins targeting the day — and writes only what changed; store_bulletin
refreshes the pairs a write touched, before and after.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import pytest

from apps.ingest.day_ratings import (
    day_pairs,
    index_bulletin,
    refresh_source_day_ratings,
)
from apps.ingest.models import SourceBulletin, SourceDayRating
from apps.ingest.normalise.day_rating import DAY_RATING_VERSION, NO_RATING
from apps.ingest.normalise.render_model import RENDER_MODEL_VERSION
from apps.ingest.runs import NormalisedBulletin
from apps.ingest.store import store_bulletin
from tests.factories import SourceBulletinFactory, SourceDayRatingFactory

pytestmark = pytest.mark.django_db

DAY = date(2026, 1, 2)


def _model(key: str = "considerable") -> dict[str, Any]:
    """A current render model whose headline is ``key`` and that has no traits."""
    return {
        "version": RENDER_MODEL_VERSION,
        "source": "SLF",
        "danger": {"key": key, "subdivision": "", "ratings": []},
        "traits": [],
    }


def _bulletin(
    bulletin_id: str, hour: int, codes: list[str], key: str = "considerable"
) -> SourceBulletin:
    """A stored bulletin valid from ``hour`` UTC on DAY (or the day before)."""
    valid_from = datetime(2026, 1, 2 if hour < 12 else 1, hour, tzinfo=UTC)
    return SourceBulletinFactory.create(
        bulletin_id=bulletin_id,
        render_model=_model(key),
        render_model_version=RENDER_MODEL_VERSION,
        valid_from=valid_from,
        target_date=DAY,
        region_codes=codes,
    )


def _caaml(bulletin_id: str, start: str, codes: list[str]) -> dict[str, Any]:
    """A CAAML with a validity start and regions."""
    return {
        "bulletinID": bulletin_id,
        "validTime": {"startTime": start},
        "regions": [{"regionID": code, "name": code} for code in codes],
    }


class TestIndexBulletin:
    """The target day and region codes, read from the CAAML."""

    def test_a_morning_issue_targets_its_own_day(self) -> None:
        """Valid from 07:00 UTC: today."""
        row = SourceBulletin(caaml=_caaml("a", "2026-01-02T07:00Z", ["CH-2", "CH-1"]))
        index_bulletin(row)
        assert row.target_date == DAY
        assert row.region_codes == ["CH-1", "CH-2"]

    def test_an_evening_issue_targets_tomorrow(self) -> None:
        """Valid from 16:00 UTC: the next day."""
        row = SourceBulletin(caaml=_caaml("a", "2026-01-01T16:00Z", ["CH-1"]))
        index_bulletin(row)
        assert row.target_date == DAY

    def test_no_validity_means_no_day(self) -> None:
        """A missing or unreadable start governs nothing."""
        for caaml in ({"bulletinID": "a"}, _caaml("a", "not a date", ["CH-1"])):
            row = SourceBulletin(caaml=caaml)
            index_bulletin(row)
            assert row.target_date is None
            assert day_pairs(row) == set()

    def test_day_pairs_cross_codes_with_the_day(self) -> None:
        """One pair per region code."""
        row = SourceBulletin(target_date=DAY, region_codes=["CH-1", "CH-2"])
        assert day_pairs(row) == {("CH-1", DAY), ("CH-2", DAY)}


class TestRefresh:
    """refresh_source_day_ratings."""

    def test_the_latest_issue_governs(self) -> None:
        """Morning-of beats the prior evening: the later valid_from wins."""
        _bulletin("evening", 16, ["CH-1"], key="moderate")
        _bulletin("morning", 7, ["CH-1"], key="considerable")
        assert refresh_source_day_ratings({("CH-1", DAY)}) == 1
        rating = SourceDayRating.objects.get()
        assert rating.bulletin_id == "morning"
        assert rating.max_rating == "considerable"
        assert rating.version == DAY_RATING_VERSION

    def test_only_the_regions_a_bulletin_covers(self) -> None:
        """A bulletin for CH-2 does not govern CH-1."""
        _bulletin("other", 7, ["CH-2"])
        refresh_source_day_ratings({("CH-1", DAY)})
        rating = SourceDayRating.objects.get()
        assert rating.bulletin_id == ""
        assert rating.min_rating == rating.max_rating == NO_RATING

    def test_a_stale_render_model_does_not_qualify(self) -> None:
        """Only a current model governs, as in the app."""
        row = _bulletin("old", 7, ["CH-1"])
        SourceBulletin.objects.filter(pk=row.pk).update(render_model_version=1)
        refresh_source_day_ratings({("CH-1", DAY)})
        assert SourceDayRating.objects.get().bulletin_id == ""

    def test_an_unchanged_rating_is_not_written(self) -> None:
        """A second refresh finds nothing to change, and updated_at stays."""
        _bulletin("b", 7, ["CH-1"])
        refresh_source_day_ratings({("CH-1", DAY)})
        before = SourceDayRating.objects.get().updated_at
        assert refresh_source_day_ratings({("CH-1", DAY)}) == 0
        assert SourceDayRating.objects.get().updated_at == before

    def test_a_changed_rating_is_rewritten(self) -> None:
        """A held row with another value is updated in place."""
        _bulletin("b", 7, ["CH-1"], key="high")
        SourceDayRatingFactory.create(region_code="CH-1", date=DAY)
        assert refresh_source_day_ratings({("CH-1", DAY)}) == 1
        assert SourceDayRating.objects.get().max_rating == "high"

    def test_without_commit_nothing_is_written(self) -> None:
        """The preview counts."""
        _bulletin("b", 7, ["CH-1"])
        assert refresh_source_day_ratings({("CH-1", DAY)}, commit=False) == 1
        assert not SourceDayRating.objects.exists()


class TestStoreRefreshes:
    """store_bulletin rates the pairs a write touches."""

    def _record(self, caaml: dict[str, Any]) -> NormalisedBulletin:
        """A walk's record for ``caaml``."""
        return NormalisedBulletin(caaml["bulletinID"], "SLF", caaml, lambda: "")

    def test_a_new_bulletin_rates_its_regions(self) -> None:
        """Each covered region gets the day's rating."""
        caaml = _caaml("b", "2026-01-02T07:00Z", ["CH-1", "CH-2"])
        with patch("apps.ingest.store.build_render_model", return_value=_model()):
            store_bulletin(self._record(caaml))
        assert set(
            SourceDayRating.objects.values_list("region_code", "bulletin_id")
        ) == {("CH-1", "b"), ("CH-2", "b")}

    def test_a_correction_that_drops_a_region_unrates_it(self) -> None:
        """The dropped region is re-rated, and nothing governs it now."""
        with patch("apps.ingest.store.build_render_model", return_value=_model()):
            store_bulletin(
                self._record(_caaml("b", "2026-01-02T07:00Z", ["CH-1", "CH-2"]))
            )
            store_bulletin(self._record(_caaml("b", "2026-01-02T07:00Z", ["CH-1"])))
        dropped = SourceDayRating.objects.get(region_code="CH-2")
        assert dropped.bulletin_id == ""
        assert dropped.max_rating == NO_RATING

    def test_an_unindexed_row_is_indexed_on_its_next_fetch(self) -> None:
        """A row stored before SNOW-1270 is indexed and rated, unchanged CAAML."""
        caaml = _caaml("b", "2026-01-02T07:00Z", ["CH-1"])
        with patch("apps.ingest.store.build_render_model", return_value=_model()):
            row, _ = store_bulletin(self._record(caaml))
        SourceBulletin.objects.filter(pk=row.pk).update(
            target_date=None, region_codes=[]
        )
        SourceDayRating.objects.all().delete()
        with patch("apps.ingest.store.build_render_model", return_value=_model()):
            _, outcome = store_bulletin(self._record(caaml))
        assert outcome == "updated"
        assert SourceDayRating.objects.get().bulletin_id == "b"
