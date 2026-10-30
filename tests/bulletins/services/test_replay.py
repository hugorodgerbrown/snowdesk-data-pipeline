"""
tests/bulletins/services/test_replay.py — A season of alerts, replayed (SNOW-949).

Synthetic archives through replay_region, then one real pass over the
committed SLF archive to pin the season's shape.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import patch

import pytest

from apps.bulletins.services import replay
from apps.bulletins.services.alerts import ALERT_TZ
from apps.bulletins.services.replay import region_issues, replay_archive, replay_region

REGION = "CH-4115"


def issue(
    published: str, start: str, rating: str = "moderate", region: str = REGION
) -> dict[str, Any]:
    """Return a minimal SLF-shaped record."""
    return {
        "bulletinID": f"b-{published}",
        "publicationTime": published,
        "validTime": {"startTime": start, "endTime": start},
        "regions": [{"regionID": region, "name": "Martigny-Verbier"}],
        "rating": rating,
    }


def fake_render(raw: dict[str, Any]) -> dict[str, Any]:
    """Stand in for build_render_model on the synthetic records."""
    if raw.get("rating") == "broken":
        raise ValueError("bad")
    return {"danger": {"key": raw["rating"]}, "traits": []}


def fake_day(source: str, raw: dict[str, Any]) -> datetime.date:
    """The SLF day rule: an evening issue forecasts the next day."""
    start = datetime.datetime.fromisoformat(raw["validTime"]["startTime"])
    return (start + datetime.timedelta(hours=12)).date()


@pytest.fixture()
def _fakes() -> Any:
    """Patch the render-model builder and the day rule."""
    with (
        patch.object(replay, "build_render_model", side_effect=fake_render),
        patch.object(replay, "target_day", side_effect=fake_day),
    ):
        yield


RECORDS = [
    # Evening issue for 17 Jan, published 16:00 local (15:00Z).
    issue("2026-01-16T15:00:00+00:00", "2026-01-16T16:00:00+00:00"),
    # Morning update for 17 Jan, identical: not sent.
    issue("2026-01-17T06:30:00+00:00", "2026-01-17T07:00:00+00:00"),
    # Evening issue for 18 Jan, a rise.
    issue("2026-01-17T15:00:00+00:00", "2026-01-17T16:00:00+00:00", "considerable"),
    # Published at 23:30 local, for 19 Jan: held to 06:30.
    issue("2026-01-18T22:30:00+00:00", "2026-01-18T23:00:00+00:00", "considerable"),
    # Another region: ignored.
    issue("2026-01-16T15:00:00+00:00", "2026-01-16T16:00:00+00:00", region="CH-1"),
    # A record that cannot be built is skipped.
    issue("2026-01-19T15:00:00+00:00", "2026-01-19T16:00:00+00:00", "broken"),
]


@pytest.mark.usefixtures("_fakes")
def test_region_issues_filters_and_orders() -> None:
    """Only the region's issues, in publication order."""
    issues = region_issues(reversed(RECORDS), "SLF", REGION)
    assert len(issues) == 5
    assert issues[0][0] < issues[-1][0]


@pytest.mark.usefixtures("_fakes")
def test_new_bulletin_schedule() -> None:
    """Every new bulletin: three messages, the night issue held to 06:30."""
    alerts = replay_region(RECORDS, source="SLF")
    sent = [a for a in alerts if a.sent]
    assert [a.title for a in sent] == [
        "Martigny-Verbier · Moderate (2)",
        "Martigny-Verbier · Considerable (3) ↑",
        "Martigny-Verbier · Considerable (3)",
    ]
    assert sent[0].body == "Saturday · first bulletin of the season"
    assert sent[1].body == "Sunday · up from Moderate (2)"
    held = sent[2].delivered_at.astimezone(ALERT_TZ)
    assert (held.hour, held.minute) == (6, 30)


@pytest.mark.usefixtures("_fakes")
def test_changes_skips_the_restatement() -> None:
    """Only changes: the unchanged Monday is skipped."""
    alerts = replay_region(RECORDS, source="SLF", mode="CHANGES")
    assert [a.outcome for a in alerts] == ["SENT", "SENT", "SKIPPED"]


@pytest.mark.usefixtures("_fakes")
def test_late_correction_to_a_past_day_is_not_announced() -> None:
    """An issue for a day already over says nothing."""
    late = [issue("2026-01-20T08:00:00+00:00", "2026-01-16T16:00:00+00:00")]
    assert replay_region(late, source="SLF") == []


@pytest.mark.usefixtures("_fakes")
def test_older_validity_does_not_replace_authoritative() -> None:
    """An issue valid from earlier than the held one is not authoritative."""
    records = [
        issue("2026-01-16T15:00:00+00:00", "2026-01-16T16:00:00+00:00"),
        issue("2026-01-16T15:30:00+00:00", "2026-01-16T15:00:00+00:00", "high"),
    ]
    assert len(replay_region(records, source="SLF")) == 1


@pytest.mark.usefixtures("_fakes")
def test_issues_held_overnight_are_one_decision() -> None:
    """Two issues for one day held to 06:30: only the later one is decided."""
    records = [
        issue("2026-01-16T22:00:00+00:00", "2026-01-16T23:00:00+00:00", "considerable"),
        issue("2026-01-17T02:00:00+00:00", "2026-01-17T03:00:00+00:00", "high"),
    ]
    (alert,) = replay_region(records, source="SLF")
    assert "High (4)" in alert.title


def test_committed_slf_season() -> None:
    """The committed CH-4115 season replays end to end."""
    alerts = replay_archive(source="SLF")
    sent = [a for a in alerts if a.sent]
    assert 150 <= len(sent) <= 260
    assert all(a.title.startswith("Martigny-Verbier · ") for a in sent)
    # A teaser: the day and whether it moved, never the problems.
    assert all(len(a.body) <= 60 for a in sent)
