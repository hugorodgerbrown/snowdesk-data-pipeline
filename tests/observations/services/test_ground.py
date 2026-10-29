"""
tests/observations/services/test_ground.py — the one reading of which
field reports a day shows (apps.observations.services.ground, SNOW-1291).

Covers:
  - ``report_window``: today reads today plus the three days before, a past
    day reads that day only, a future day reads nothing.
  - ``group_reports``: one report per submission, its labels in declaration
    order, its most serious family, newest first.
  - ``ground_for_region``: today's reports are live, the three days before
    recent, an older one absent; a past day's reports land in ``day``; a
    future day is empty; another region's reports never appear.
"""

from __future__ import annotations

import datetime
import uuid
from datetime import UTC

import pytest
from freezegun import freeze_time

from apps.observations.models import FieldObservation
from apps.observations.services.ground import (
    RECENT_DAYS,
    Ground,
    GroundReport,
    ground_for_region,
    group_reports,
    local_midnight,
    report_window,
)
from tests.factories import FieldObservationFactory, MicroRegionFactory

TODAY = datetime.date(2026, 10, 29)
NOW = datetime.datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
T = FieldObservation.OBSERVATION_TYPE


def _at(day: datetime.date, hour: int = 10) -> datetime.datetime:
    """Return ``hour`` o'clock on ``day``, timezone-aware."""
    return datetime.datetime.combine(day, datetime.time(hour), tzinfo=UTC)


class TestReportWindow:
    """The ``[start, end)`` window each day on screen reads."""

    def test_today_reads_today_and_the_recent_days(self) -> None:
        """Today runs from midnight three days back to tomorrow's midnight."""
        start, end = report_window(TODAY, TODAY) or (None, None)
        assert start == local_midnight(TODAY - datetime.timedelta(days=RECENT_DAYS))
        assert end == local_midnight(TODAY + datetime.timedelta(days=1))

    def test_a_past_day_reads_that_day_only(self) -> None:
        """A past day is one local day, nothing either side."""
        day = datetime.date(2026, 10, 20)
        assert report_window(day, TODAY) == (
            local_midnight(day),
            local_midnight(datetime.date(2026, 10, 21)),
        )

    def test_a_future_day_reads_nothing(self) -> None:
        """Nothing has been observed tomorrow yet."""
        assert report_window(datetime.date(2026, 10, 30), TODAY) is None


@pytest.mark.django_db
class TestGroupReports:
    """Rows become reports, one per submission."""

    def test_one_report_per_submission(self) -> None:
        """Two rows of one submission are one report; labels in OBSERVATION_TYPE order."""
        submission = uuid.uuid4()
        region = MicroRegionFactory.create()
        for kind in (T.POWDER, T.WHUMPFING):
            FieldObservationFactory.create(
                region=region,
                submission=submission,
                observation_type=kind,
                observed_at=NOW,
            )
        reports = group_reports(list(FieldObservation.objects.all()))
        assert len(reports) == 1
        assert reports[0].submission == submission
        assert reports[0].family == FieldObservation.OBSERVATION_FAMILY.WARNING
        assert reports[0].labels == (str(T.WHUMPFING.label), str(T.POWDER.label))

    def test_newest_first(self) -> None:
        """Reports sort newest first."""
        region = MicroRegionFactory.create()
        older = FieldObservationFactory.create(
            region=region, observed_at=NOW - datetime.timedelta(hours=3)
        )
        newer = FieldObservationFactory.create(region=region, observed_at=NOW)
        reports = group_reports(list(FieldObservation.objects.all()))
        assert [r.submission for r in reports] == [newer.submission, older.submission]

    def test_to_string(self) -> None:
        """A report describes itself for logs."""
        report = GroundReport(
            submission=uuid.uuid4(),
            observed_at=NOW,
            family="WARNING",
            labels=("Whumpfing", "Powder"),
        )
        assert str(report) == "WARNING: Whumpfing, Powder @ 2026-10-29 12:00"


@pytest.mark.django_db
@freeze_time(NOW)
class TestGroundForRegion:
    """A region's reports for the day on screen, split into sections."""

    def test_today_splits_live_and_recent(self) -> None:
        """Today's report is live, two days ago recent, five days ago absent."""
        region = MicroRegionFactory.create()
        live = FieldObservationFactory.create(region=region, observed_at=_at(TODAY))
        recent = FieldObservationFactory.create(
            region=region, observed_at=_at(TODAY - datetime.timedelta(days=2))
        )
        FieldObservationFactory.create(
            region=region, observed_at=_at(TODAY - datetime.timedelta(days=5))
        )
        ground = ground_for_region(region, TODAY)
        assert ground.is_today
        assert [r.submission for r in ground.live] == [live.submission]
        assert [r.submission for r in ground.recent] == [recent.submission]
        assert ground.day == []
        assert ground.count == 2

    def test_a_past_day_holds_that_days_reports(self) -> None:
        """A past day's reports are ``day``, never live or recent."""
        region = MicroRegionFactory.create()
        day = datetime.date(2026, 10, 27)
        report = FieldObservationFactory.create(region=region, observed_at=_at(day))
        FieldObservationFactory.create(region=region, observed_at=_at(TODAY))
        ground = ground_for_region(region, day)
        assert not ground.is_today
        assert [r.submission for r in ground.day] == [report.submission]
        assert ground.live == [] and ground.recent == []

    def test_a_future_day_is_empty(self) -> None:
        """Tomorrow shows nothing."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(region=region, observed_at=_at(TODAY))
        ground = ground_for_region(region, datetime.date(2026, 10, 30))
        assert ground == Ground(page_date=datetime.date(2026, 10, 30), is_today=False)

    def test_another_regions_reports_never_appear(self) -> None:
        """Only the region's own reports are read."""
        region = MicroRegionFactory.create()
        FieldObservationFactory.create(observed_at=_at(TODAY))
        assert ground_for_region(region, TODAY).count == 0
