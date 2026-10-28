"""
tests/ingest/test_weather.py — Tests for apps.ingest.weather (SNOW-1253).

The service's own weather: the place list (``sync_source_locations``), the
past-day write rule on its table (``store_source_weather``) and the walk
(``fetch_source_weather``). The request-and-parse rules
(``forecast_fields``) are pinned through the app's fetch in
``tests/weather/services/test_fetch.py``, which calls them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any
from unittest import mock

import pytest
from django.utils import timezone

from apps.ingest.models import (
    ImmutableSourceWeatherError,
    SourceLocation,
    SourceWeather,
)
from apps.ingest.weather import (
    LocationRecord,
    fetch_source_weather,
    store_source_weather,
    sync_source_locations,
)
from tests.factories import SourceLocationFactory, SourceWeatherFactory

pytestmark = pytest.mark.django_db


def _record(**overrides: Any) -> LocationRecord:
    """Return a reference place, with any field overridden."""
    values: dict[str, Any] = {
        "uuid": uuid.uuid4(),
        "name": "Mont Fort",
        "latitude": 46.10361,
        "longitude": 7.29889,
        "elevation_m": 3328.0,
    }
    values.update(overrides)
    return LocationRecord(**values)


def _payload(start: dt.date, count: int = 3) -> dict[str, Any]:
    """Return a minimal complete Open-Meteo forecast payload from ``start``."""
    days = [start + dt.timedelta(days=offset) for offset in range(count)]
    return {
        "daily": {
            "time": [d.isoformat() for d in days],
            "weather_code": [3] * count,
            "sunrise": [f"{d.isoformat()}T06:30+02:00" for d in days],
            "sunset": [f"{d.isoformat()}T18:45+02:00" for d in days],
            "temperature_2m_max": [4.5] * count,
            "temperature_2m_min": [-2.0] * count,
        }
    }


def _response(payload: dict[str, Any]) -> mock.Mock:
    """Return a mock ``requests`` response yielding ``payload``."""
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


class TestSyncSourceLocations:
    """The live list ends up equal to what the host handed over."""

    def test_a_new_place_is_created_with_the_hosts_uuid(self) -> None:
        """The uuid is the host's, so every instance can key on it."""
        record = _record()
        counts = sync_source_locations([record])
        row = SourceLocation.objects.get()
        assert row.uuid == record.uuid
        assert (row.latitude, row.longitude) == (46.10361, 7.29889)
        assert counts == {"created": 1, "updated": 0, "deleted": 0, "unchanged": 0}

    def test_an_unchanged_place_is_not_written(self) -> None:
        """``updated_at`` moves only on a real change, so the feed stays quiet."""
        record = _record()
        sync_source_locations([record])
        before = SourceLocation.objects.get().updated_at
        counts = sync_source_locations([record])
        assert SourceLocation.objects.get().updated_at == before
        assert counts["unchanged"] == 1

    def test_a_moved_place_is_updated_in_place(self) -> None:
        """A correction re-places the same row; the coordinate is kept exact."""
        record = _record()
        sync_source_locations([record])
        counts = sync_source_locations([_record(uuid=record.uuid, latitude=46.123456)])
        assert SourceLocation.objects.get().latitude == 46.123456
        assert counts["updated"] == 1

    def test_an_absent_place_is_tombstoned_and_a_returning_one_revived(self) -> None:
        """Nothing is deleted: a tombstone is how a client learns of removal."""
        record = _record()
        sync_source_locations([record])
        counts = sync_source_locations([])
        row = SourceLocation.objects.get()
        assert row.deleted_at is not None
        assert counts["deleted"] == 1

        sync_source_locations([record])
        row.refresh_from_db()
        assert row.deleted_at is None

    def test_a_changed_document_is_an_update(self) -> None:
        """The host's extras travel too, and a change to them is a change."""
        record = _record(document={"kind": "PEAK"})
        sync_source_locations([record])
        counts = sync_source_locations(
            [_record(uuid=record.uuid, document={"kind": "VILLAGE"})]
        )
        assert SourceLocation.objects.get().document == {"kind": "VILLAGE"}
        assert counts["updated"] == 1

    def test_a_tombstone_is_not_stamped_twice(self) -> None:
        """A second sync without the place leaves its tombstone's time alone."""
        sync_source_locations([_record()])
        sync_source_locations([])
        first = SourceLocation.objects.get().deleted_at
        counts = sync_source_locations([])
        assert SourceLocation.objects.get().deleted_at == first
        assert counts["deleted"] == 0


class TestStoreSourceWeather:
    """The same three outcomes as the app's ``upsert_weather``."""

    def test_absent_is_created_whatever_the_date(self) -> None:
        """Recording a day never recorded is not a rewrite."""
        place = SourceLocationFactory.create()
        past = timezone.localdate() - dt.timedelta(days=10)
        row, created = store_source_weather(
            place,
            past,
            weather_code=1,
            sunrise=timezone.now(),
            sunset=timezone.now(),
        )
        assert created
        assert row.observed_on == past
        assert row.fetched_at is not None

    def test_today_is_refined_in_place(self) -> None:
        """The four daily runs refine today's one row."""
        existing = SourceWeatherFactory.create(weather_code=3)
        row, created = store_source_weather(
            existing.location, existing.observed_on, weather_code=71
        )
        assert not created
        assert row.pk == existing.pk
        assert SourceWeather.objects.get().weather_code == 71

    def test_a_past_row_is_refused(self) -> None:
        """A day's account stands once the day is over."""
        existing = SourceWeatherFactory.create(
            observed_on=timezone.localdate() - dt.timedelta(days=1)
        )
        with pytest.raises(ImmutableSourceWeatherError):
            store_source_weather(
                existing.location, existing.observed_on, weather_code=0
            )


class TestFetchSourceWeather:
    """The walk over every live place."""

    def test_commit_writes_one_row_per_live_place_and_hands_it_on(self) -> None:
        """Each written row reaches ``on_row``; a tombstoned place is skipped."""
        place = SourceLocationFactory.create(elevation_m=3328.0)
        SourceLocationFactory.create(deleted_at=timezone.now())
        today = timezone.localdate()
        seen: list[SourceWeather] = []

        with mock.patch("requests.get", return_value=_response(_payload(today))) as get:
            counts = fetch_source_weather(today, commit=True, on_row=seen.append)

        assert get.call_count == 1
        assert get.call_args.kwargs["params"]["elevation"] == "3328.0"
        row = SourceWeather.objects.get()
        assert row.location == place
        assert row.observed_on == today
        assert row.forecast is not None
        assert len(row.forecast) == 2
        assert seen == [row]
        assert counts == {"created": 1, "updated": 0, "failed": 0}

    def test_a_second_run_refines_todays_row(self) -> None:
        """Run twice in a day, the row is updated, not duplicated."""
        SourceLocationFactory.create()
        today = timezone.localdate()
        with mock.patch("requests.get", return_value=_response(_payload(today))):
            fetch_source_weather(today, commit=True)
            counts = fetch_source_weather(today, commit=True)
        assert SourceWeather.objects.count() == 1
        assert counts["updated"] == 1

    def test_read_only_probes_but_writes_nothing(self) -> None:
        """Without commit the request runs and nothing is stored or handed on."""
        SourceLocationFactory.create()
        today = timezone.localdate()
        on_row = mock.Mock()
        with mock.patch("requests.get", return_value=_response(_payload(today))) as get:
            counts = fetch_source_weather(today, commit=False, on_row=on_row)
        assert get.call_count == 1
        assert SourceWeather.objects.count() == 0
        on_row.assert_not_called()
        assert counts == {"created": 0, "updated": 0, "failed": 0}

    def test_one_failing_place_fails_alone(self) -> None:
        """A bad response is counted and the next place is still fetched."""
        bad = SourceLocationFactory.create()
        good = SourceLocationFactory.create()
        today = timezone.localdate()
        broken = _payload(today)
        broken["daily"]["time"] = []
        seen: list[SourceLocation] = []

        with mock.patch(
            "requests.get",
            side_effect=[_response(broken), _response(_payload(today))],
        ):
            counts = fetch_source_weather(
                today,
                commit=True,
                locations=[bad, good],
                on_location=seen.append,
            )

        assert seen == [bad, good]
        assert counts == {"created": 1, "updated": 0, "failed": 1}
        assert SourceWeather.objects.get().location == good
