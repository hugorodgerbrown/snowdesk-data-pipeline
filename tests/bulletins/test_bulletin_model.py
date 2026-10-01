"""
tests/bulletins/test_bulletin_model.py — Tests for the Bulletin model.

Covers ``Bulletin.row_label``, the operator-facing label every bulletin
management command prints beside a row's id (SNOW-1054).
"""

from __future__ import annotations

import datetime
from datetime import UTC

import pytest

from apps.bulletins.models import Bulletin
from apps.bulletins.services.day_rating import target_day_for_valid_from
from tests.factories import BulletinFactory


@pytest.mark.django_db
class TestBulletinRowLabel:
    """Tests for Bulletin.row_label()."""

    def test_reads_source_target_day_and_bulletin_id(self) -> None:
        """The label is ``<source> <day> <bulletin_id>``."""
        bulletin = BulletinFactory.create(
            bulletin_id="row-label-001",
            source=Bulletin.Source.SLF,
            valid_from=datetime.datetime(2026, 1, 20, 7, 0, tzinfo=UTC),
            valid_to=datetime.datetime(2026, 1, 20, 16, 0, tzinfo=UTC),
        )
        bulletin.target_date = datetime.date(2026, 1, 20)

        assert bulletin.row_label() == "SLF 2026-01-20 row-label-001"

    @pytest.mark.parametrize(
        ("valid_from", "expected_day"),
        [
            # A morning update forecasts its own day.
            (datetime.datetime(2026, 1, 20, 7, 0, tzinfo=UTC), "2026-01-20"),
            # An evening issue forecasts the next day, not its publication day.
            (datetime.datetime(2026, 1, 19, 16, 0, tzinfo=UTC), "2026-01-20"),
        ],
    )
    def test_unbackfilled_row_uses_the_target_day_rule(
        self, valid_from: datetime.datetime, expected_day: str
    ) -> None:
        """With no target_date, the day follows target_day_for_valid_from.

        Without the rule an evening issue would be labelled a day early —
        one day before the value backfill_bulletin_target_dates is about to
        store for the very row the label announces.
        """
        bulletin = BulletinFactory.create(
            bulletin_id="row-label-002",
            source=Bulletin.Source.SLF,
            valid_from=valid_from,
            valid_to=valid_from + datetime.timedelta(hours=24),
        )
        bulletin.target_date = None

        assert bulletin.row_label() == f"SLF {expected_day} row-label-002"
        assert expected_day == str(target_day_for_valid_from(valid_from))

    def test_undetected_source_keeps_three_columns(self) -> None:
        """A blank source prints ``-`` so the line still has three columns."""
        bulletin = BulletinFactory.create(
            bulletin_id="row-label-003",
            source="",
            valid_from=datetime.datetime(2026, 1, 20, 7, 0, tzinfo=UTC),
            valid_to=datetime.datetime(2026, 1, 20, 16, 0, tzinfo=UTC),
        )
        bulletin.target_date = datetime.date(2026, 1, 20)

        assert bulletin.row_label() == "- 2026-01-20 row-label-003"
