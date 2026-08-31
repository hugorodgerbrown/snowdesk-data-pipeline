"""
tests/weather/services/test_fetch.py — Tests for the Open-Meteo fetch.

The three behaviours the ticket names as must-survive get a test each,
because each is a bug that was found in production rather than in review:

* **SNOW-466** — a misaligned ``daily`` array is rejected rather than
  shifting every day onto the wrong date.
* **SNOW-628** — a day past the model horizon is dropped, and dropping it
  must not cost the near-term days their row.
* **SNOW-546** — the write is atomic per location, so one bad location
  fails alone.

Plus the shape of what gets written: the row is ``observed_on``, the days
after it go in ``forecast``, and only the first ``HOURLY_DAYS`` entries
carry a nested hourly series.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest import mock

import pytest
import requests
from django.utils import timezone

from apps.weather.models import Weather
from apps.weather.services.fetch import (
    HOURLY_DAYS,
    fetch_all_locations,
    fetch_weather_for_location,
)
from tests.factories import (
    FavouriteFactory,
    FieldObservationFactory,
    LocationFactory,
)

# A fixed day so the payloads below read as literal dates rather than
# arithmetic. Every test that writes uses today instead, because the
# immutability rule refuses a past row that already exists.
_DAY = datetime.date(2026, 8, 30)


def _daily(days: list[datetime.date], **overrides: Any) -> dict[str, Any]:
    """Build a complete ``daily`` block for ``days``.

    Every array is fully populated and aligned, so a test that wants a
    broken payload breaks exactly one thing via ``overrides`` and the
    reader can see which.
    """
    count = len(days)
    block: dict[str, Any] = {
        "time": [d.isoformat() for d in days],
        "weather_code": [3] * count,
        "sunrise": [f"{d.isoformat()}T06:30+02:00" for d in days],
        "sunset": [f"{d.isoformat()}T18:45+02:00" for d in days],
        "temperature_2m_max": [4.5] * count,
        "temperature_2m_min": [-2.0] * count,
        "snowfall_sum": [1.2] * count,
    }
    block.update(overrides)
    return block


def _hourly(days: list[datetime.date]) -> dict[str, Any]:
    """Build an ``hourly`` block with two hours per day in ``days``."""
    times = [f"{d.isoformat()}T{hour:02d}:00+02:00" for d in days for hour in (6, 12)]
    count = len(times)
    return {
        "time": times,
        "temperature_2m": [1.0] * count,
        "snowfall": [0.0] * count,
        "precipitation": [0.0] * count,
        "wind_speed_10m": [10.0] * count,
        "wind_gusts_10m": [20.0] * count,
        "wind_direction_10m": [270.0, 315.0] * len(days),
        "freezing_level_height": [2400.0, 2600.0] * len(days),
    }


def _response(payload: dict[str, Any]) -> mock.Mock:
    """Return a mock ``requests`` response yielding ``payload``."""
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _window(start: datetime.date, count: int) -> list[datetime.date]:
    """Return ``count`` consecutive dates from ``start``."""
    return [start + datetime.timedelta(days=offset) for offset in range(count)]


@pytest.mark.django_db
class TestProviderDatesAreAuthoritative:
    """SNOW-466 — rows key off the provider's dates, not an offset."""

    def test_row_takes_the_providers_date_not_the_requested_one(self) -> None:
        """A response starting a day late writes that day, not the requested one.

        Deriving the date as ``observed_on + idx`` would silently file this
        response's weather under the wrong calendar day.
        """
        location = LocationFactory.create()
        today = timezone.localdate()
        provider_days = _window(today, 3)
        payload = {"daily": _daily(provider_days), "hourly": _hourly(provider_days)}

        with mock.patch("requests.get", return_value=_response(payload)):
            result = fetch_weather_for_location(location, today, commit=True)

        assert result is not None
        weather, _ = result
        assert weather.observed_on == provider_days[0]

    def test_a_misaligned_daily_array_is_rejected_whole(self) -> None:
        """A short required array fails loudly rather than storing a shifted batch.

        The alternative is worse than an error: every day after the gap
        would be attached to the wrong date, and nothing downstream could
        tell.
        """
        location = LocationFactory.create()
        days = _window(_DAY, 3)
        # weather_code one entry short of time — the exact shape a dropped
        # element in the response produces.
        payload = {"daily": _daily(days, weather_code=[3, 3])}

        with (
            mock.patch("requests.get", return_value=_response(payload)),
            pytest.raises(ValueError, match="refusing to store misaligned data"),
        ):
            fetch_weather_for_location(location, _DAY, commit=True)

    def test_an_empty_time_array_is_rejected(self) -> None:
        """A response with no days at all is an error, not an empty success."""
        location = LocationFactory.create()
        payload = {"daily": _daily([])}

        with (
            mock.patch("requests.get", return_value=_response(payload)),
            pytest.raises(ValueError, match="empty 'time' array"),
        ):
            fetch_weather_for_location(location, _DAY, commit=True)


@pytest.mark.django_db
class TestShortModelHorizon:
    """SNOW-628 — a day past the horizon is dropped, not fatal."""

    def test_a_null_tail_still_writes_the_near_term_row(self) -> None:
        """The unusable tail must not cost the near-term days their row.

        This is the regression itself: point forecasts once wrote **zero**
        rows because a null weather_code on day 6 rolled back days 0–5.
        """
        location = LocationFactory.create()
        today = timezone.localdate()
        days = _window(today, 5)
        # Days 3 and 4 fall past the backing model's horizon: weather_code
        # is null while sunrise/sunset, being astronomical, stay populated.
        payload = {
            "daily": _daily(days, weather_code=[3, 3, 3, None, None]),
            "hourly": _hourly(days),
        }

        with mock.patch("requests.get", return_value=_response(payload)):
            result = fetch_weather_for_location(location, today, commit=True)

        assert result is not None
        weather, created = result
        assert created is True
        assert weather.observed_on == days[0]
        assert weather.forecast is not None
        # Three storable days: the row itself plus two forward days.
        assert [entry["date"] for entry in weather.forecast] == [
            days[1].isoformat(),
            days[2].isoformat(),
        ]

    def test_an_unusable_day_zero_records_the_first_day_that_resolves(self) -> None:
        """Day 0 missing is not fatal — the days that did resolve are still kept.

        Raising here would discard a usable window because its first day was
        not usable, which is the same shape of mistake SNOW-628 was.
        """
        location = LocationFactory.create()
        today = timezone.localdate()
        days = _window(today, 3)
        payload = {
            "daily": _daily(days, weather_code=[None, 3, 3]),
            "hourly": _hourly(days),
        }

        with mock.patch("requests.get", return_value=_response(payload)):
            result = fetch_weather_for_location(location, today, commit=True)

        assert result is not None
        weather, _ = result
        assert weather.observed_on == days[1]

    def test_a_payload_with_no_complete_day_raises(self) -> None:
        """No storable day at all is a malformed payload, not a short horizon."""
        location = LocationFactory.create()
        days = _window(_DAY, 3)
        payload = {"daily": _daily(days, weather_code=[None, None, None])}

        with (
            mock.patch("requests.get", return_value=_response(payload)),
            pytest.raises(ValueError, match="no day carries a complete"),
        ):
            fetch_weather_for_location(location, _DAY, commit=True)


@pytest.mark.django_db
class TestOneBadLocationFailsAlone:
    """SNOW-546 / batch isolation — a failure is contained to its location."""

    def test_a_failing_location_does_not_stop_the_others(self) -> None:
        """One location's HTTP error costs only that location its row."""
        # Distinct latitudes: the stub below discriminates on them, and the
        # factory default is the same coordinate for every row.
        good = LocationFactory.create(name="Good", latitude=46.1)
        bad = LocationFactory.create(name="Bad", latitude=47.2)
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        def _get(url: str, params: dict[str, str], timeout: int) -> mock.Mock:
            """Fail for the bad location's latitude, succeed for the good one."""
            if params["latitude"] == str(bad.latitude):
                raise requests.HTTPError("500 Server Error")
            return _response(payload)

        with mock.patch("requests.get", side_effect=_get):
            counts = fetch_all_locations(today, commit=True, locations=[good, bad])

        assert counts == {"created": 1, "updated": 0, "failed": 1}
        assert Weather.objects.for_location(good).count() == 1
        assert Weather.objects.for_location(bad).count() == 0

    def test_a_partial_write_is_rolled_back(self) -> None:
        """A failure inside the write leaves no half-written row behind.

        The write is wrapped in transaction.atomic() per location, so the
        batch's ``failed`` counter and the database agree about what
        happened.
        """
        location = LocationFactory.create()
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with (
            mock.patch("requests.get", return_value=_response(payload)),
            mock.patch(
                "apps.weather.services.fetch.upsert_weather",
                side_effect=RuntimeError("write blew up"),
            ),
        ):
            counts = fetch_all_locations(today, commit=True, locations=[location])

        assert counts["failed"] == 1
        assert Weather.objects.for_location(location).count() == 0


@pytest.mark.django_db
class TestWrittenShape:
    """What one successful fetch actually stores."""

    def test_the_row_is_the_day_and_forecast_is_the_days_after(self) -> None:
        """observed_on gets columns; the forward days go in the JSON column."""
        location = LocationFactory.create()
        today = timezone.localdate()
        days = _window(today, 4)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with mock.patch("requests.get", return_value=_response(payload)):
            result = fetch_weather_for_location(location, today, commit=True)

        assert result is not None
        weather, _ = result
        assert weather.weather_code == 3
        assert weather.temperature_2m_max == 4.5
        assert weather.forecast is not None
        assert [entry["date"] for entry in weather.forecast] == [
            day.isoformat() for day in days[1:]
        ]

    def test_only_the_first_forecast_days_carry_an_hourly_series(self) -> None:
        """hourly nests in the first HOURLY_DAYS entries and is absent beyond.

        It is an optional key by design — the stored JSON stays bounded —
        so a consumer must read it with .get(). This is the assertion that
        keeps that contract honest.
        """
        location = LocationFactory.create()
        today = timezone.localdate()
        days = _window(today, 5)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with mock.patch("requests.get", return_value=_response(payload)):
            result = fetch_weather_for_location(location, today, commit=True)

        assert result is not None
        weather, _ = result
        assert weather.forecast is not None
        with_hourly = [e for e in weather.forecast if "hourly" in e]
        assert len(with_hourly) == HOURLY_DAYS - 1
        assert "hourly" not in weather.forecast[-1]

    def test_the_day_itself_carries_its_own_hourly_series(self) -> None:
        """observed_on's hourly lives in its own column, not in forecast."""
        location = LocationFactory.create()
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with mock.patch("requests.get", return_value=_response(payload)):
            result = fetch_weather_for_location(location, today, commit=True)

        assert result is not None
        weather, _ = result
        assert weather.hourly is not None
        assert {row["time"][:10] for row in weather.hourly} == {today.isoformat()}

    def test_hourly_rows_carry_wind_direction(self) -> None:
        """Direction is stored per hour, not only as the daily dominant.

        A wind slab loads the aspects the wind blew onto, and a veering
        wind loads several over one day — which the daily dominant
        collapses into a single bearing. The hourly series is the one
        that maps onto aspect, so it has to carry the direction too.
        """
        location = LocationFactory.create()
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with mock.patch("requests.get", return_value=_response(payload)) as get:
            result = fetch_weather_for_location(location, today, commit=True)

        assert "wind_direction_10m" in get.call_args.kwargs["params"]["hourly"]
        assert result is not None
        weather, _ = result
        assert weather.hourly is not None
        assert [row["wind_direction_10m"] for row in weather.hourly] == [270.0, 315.0]

    def test_freezing_level_is_the_days_hourly_maximum(self) -> None:
        """Open-Meteo publishes no daily aggregate, so it is derived."""
        location = LocationFactory.create()
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with mock.patch("requests.get", return_value=_response(payload)):
            result = fetch_weather_for_location(location, today, commit=True)

        assert result is not None
        weather, _ = result
        assert weather.freezing_level_height == 2600.0

    def test_elevation_is_sent_when_the_location_has_one(self) -> None:
        """A resolved elevation downscales the forecast to the point's altitude."""
        location = LocationFactory.create(resolved=True)
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with mock.patch("requests.get", return_value=_response(payload)) as get:
            fetch_weather_for_location(location, today, commit=True)

        assert get.call_args.kwargs["params"]["elevation"] == str(location.elevation_m)

    def test_elevation_is_omitted_when_the_location_has_none(self) -> None:
        """An unresolved location is still fetched — it just gets the cell's."""
        location = LocationFactory.create(elevation_m=None)
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with mock.patch("requests.get", return_value=_response(payload)) as get:
            fetch_weather_for_location(location, today, commit=True)

        assert "elevation" not in get.call_args.kwargs["params"]

    def test_the_batch_defaults_to_the_active_estate(self) -> None:
        """Called with no explicit list, fetch_all_locations walks active().

        This is the default the scheduled run takes, so the set that costs
        money is the set the queryset defines rather than whatever a caller
        happened to pass.
        """
        FavouriteFactory.create()
        FieldObservationFactory.create()
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with mock.patch("requests.get", return_value=_response(payload)) as get:
            counts = fetch_all_locations(today, commit=True)

        # The favourite's location only — the observation's is excluded.
        assert get.call_count == 1
        assert counts["created"] == 1

    def test_on_location_is_called_once_per_location(self) -> None:
        """The progress hook fires per location, for the command's countdown."""
        first = LocationFactory.create(latitude=46.1)
        second = LocationFactory.create(latitude=47.2)
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}
        seen: list[int] = []

        with mock.patch("requests.get", return_value=_response(payload)):
            fetch_all_locations(
                today,
                commit=True,
                locations=[first, second],
                on_location=lambda loc: seen.append(loc.pk),
            )

        assert seen == [first.pk, second.pk]

    def test_read_only_calls_the_api_but_writes_nothing(self) -> None:
        """Without --commit the request is a real probe and nothing is stored."""
        location = LocationFactory.create()
        today = timezone.localdate()
        days = _window(today, 2)
        payload = {"daily": _daily(days), "hourly": _hourly(days)}

        with mock.patch("requests.get", return_value=_response(payload)) as get:
            result = fetch_weather_for_location(location, today, commit=False)

        assert result is None
        assert get.call_count == 1
        assert Weather.objects.count() == 0
