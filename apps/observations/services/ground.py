"""
apps/observations/services/ground.py — what people found on the ground.

The one reading of "which field reports does this day show" (SNOW-1290,
SNOW-1291), shared by the map's "On the ground" layer
(``apps.public.api.community_reports_geojson``) and the region panel's
section of the same name (``apps.public.views.build_bulletin_body``):

* ``report_window`` turns the day on screen into the ``[start, end)``
  window read: today is live (today) plus recent (the three days before),
  any past day is that day only, and a future day reads nothing.
* ``ground_for_region`` groups a region's rows in that window into
  reports — one per ``submission`` (SNOW-1289), carrying every type it
  ticked and its most serious family — split into the panel's sections.

Read docs/decisions/an-observation-is-live-today-and-recent-for-three-days.md.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from django.utils import timezone

from apps.observations.models import FieldObservation, leading_family

if TYPE_CHECKING:
    from apps.regions.models import MicroRegion

logger = logging.getLogger(__name__)

# How many local days before today a report is still shown, as "recent".
# Today's reports are "live"; conditions change overnight, so the day
# boundary is the reset rather than a rolling hour count.
RECENT_DAYS = 3


def local_midnight(day: datetime.date) -> datetime.datetime:
    """Return the start of ``day`` in ``settings.TIME_ZONE``, timezone-aware.

    Args:
        day: The local calendar day.

    Returns:
        Aware datetime at 00:00 local time on ``day``.

    """
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))


def report_window(
    day: datetime.date, today: datetime.date
) -> tuple[datetime.datetime, datetime.datetime] | None:
    """Return the ``[start, end)`` window of reports the day shows.

    * ``day`` is today — from local midnight ``RECENT_DAYS`` days back to
      the end of today. The end is tomorrow's midnight rather than ``now``
      so a report whose device clock runs a few seconds fast is not hidden
      until the server catches up.
    * ``day`` is in the past — that one local day, and nothing either side.
    * ``day`` is in the future — ``None``: nothing has been observed yet.

    Args:
        day: The day on screen.
        today: Today's local date.

    Returns:
        The aware ``(start, end)`` pair, or ``None`` for a future day.

    """
    if day > today:
        return None
    if day == today:
        start = local_midnight(today - datetime.timedelta(days=RECENT_DAYS))
    else:
        start = local_midnight(day)
    return start, local_midnight(day + datetime.timedelta(days=1))


@dataclass(frozen=True)
class GroundReport:
    """One report: the rows one submission wrote, as one thing to read.

    ``labels`` are the translated type labels, most serious family first
    (``OBSERVATION_TYPE`` declaration order). ``family`` is the most
    serious family among them, which colours the row as it does the pin.
    """

    submission: UUID
    observed_at: datetime.datetime
    family: str
    labels: tuple[str, ...]

    def to_string(self) -> str:
        """Return a short human-readable description, for logs and tests."""
        when = f"{self.observed_at:%Y-%m-%d %H:%M}"
        return f"{self.family}: {', '.join(self.labels)} @ {when}"

    def __str__(self) -> str:
        """Delegate to ``to_string``."""
        return self.to_string()


@dataclass(frozen=True)
class Ground:
    """A region's reports for the day on screen, split into sections.

    ``is_today`` picks the shape: today has ``live`` and ``recent``; any
    other day has ``day`` alone. Each list is newest first.
    """

    page_date: datetime.date
    is_today: bool
    live: list[GroundReport] = field(default_factory=list)
    recent: list[GroundReport] = field(default_factory=list)
    day: list[GroundReport] = field(default_factory=list)

    @property
    def count(self) -> int:
        """Return how many reports the day shows, across every section."""
        return len(self.live) + len(self.recent) + len(self.day)


def group_reports(rows: list[FieldObservation]) -> list[GroundReport]:
    """Group rows into reports, one per submission, newest first.

    The rows of one submission share an ``observed_at`` (SNOW-1289), so
    the first row seen stands for the report's time.

    Args:
        rows: FieldObservation rows, in any order.

    Returns:
        One ``GroundReport`` per distinct submission, newest first.

    """
    type_rank = {v: i for i, v in enumerate(FieldObservation.OBSERVATION_TYPE.values)}
    labels = dict(FieldObservation.OBSERVATION_TYPE.choices)
    grouped: dict[UUID, list[FieldObservation]] = {}
    for row in rows:
        grouped.setdefault(row.submission, []).append(row)
    reports: list[GroundReport] = []
    for submission, members in grouped.items():
        types = sorted(
            {m.observation_type for m in members},
            key=lambda t: type_rank.get(t, len(type_rank)),
        )
        reports.append(
            GroundReport(
                submission=submission,
                observed_at=members[0].observed_at,
                family=leading_family(types),
                labels=tuple(str(labels.get(t, t)) for t in types),
            )
        )
    reports.sort(key=lambda r: r.observed_at, reverse=True)
    return reports


def ground_for_region(
    region: MicroRegion,
    page_date: datetime.date,
    today: datetime.date | None = None,
) -> Ground:
    """Return the region's reports for ``page_date``, split into sections.

    One query over the window ``report_window`` gives, then grouping in
    Python: a region holds a handful of reports a day, not thousands.

    Args:
        region: The region the panel describes.
        page_date: The day on screen.
        today: Today's local date; defaults to ``timezone.localdate()``.

    Returns:
        A ``Ground`` — empty for a future day.

    """
    today = today or timezone.localdate()
    is_today = page_date == today
    ground = Ground(page_date=page_date, is_today=is_today)
    window = report_window(page_date, today)
    if window is None:
        return ground
    rows = list(
        FieldObservation.objects.in_window(*window)
        .filter(region=region)
        .only("submission", "observed_at", "observation_type")
    )
    for report in group_reports(rows):
        if not is_today:
            ground.day.append(report)
        elif timezone.localdate(report.observed_at) == today:
            ground.live.append(report)
        else:
            ground.recent.append(report)
    return ground
