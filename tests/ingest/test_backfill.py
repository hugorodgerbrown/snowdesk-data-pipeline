"""
tests/ingest/test_backfill.py — Tests for the weather service's backfill (SNOW-1275).

Moved from ``tests/weather/services/test_backfill.py`` with the module: the
backfill now walks ``SourceLocation`` and writes ``SourceWeather``. The
app's apply around it is tested in ``tests/weather/services/test_backfill.py``.

The backfill is the one legitimate writer of past ``SourceWeather`` rows,
and it reaches ``store_source_weather`` — which *raises* on an existing
past row rather than skipping it. That makes two things load-bearing enough to be tested
directly rather than inferred:

* **It never writes today.** Today is ``fetch_weather``'s row, and
  ``store_source_weather`` permits a rewrite of it — so an unguarded backfill
  would overwrite the live forecast with a stitched historical timeline
  and nothing would raise. Both guards get a test: the window ends at
  yesterday, and a provider date outside the requested set is skipped.
* **A re-run asks for nothing.** Idempotence here is the caller's, not
  ``store_source_weather``'s: the diff is what stops the second run raising.

Plus the shape of what gets written — a live row's shape with lead time
zero, hourly per day, freezing level derived — and the addressing: the history host, and
no ``apikey`` sent to it on the shipped free default.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest import mock

import pytest
import requests
from django.utils import timezone

from apps.ingest import backfill
from apps.ingest.models import SourceLocation, SourceWeather
from tests.factories import SourceLocationFactory, SourceWeatherFactory


def _days(start: datetime.date, count: int) -> list[datetime.date]:
    """Return ``count`` consecutive dates from ``start``."""
    return [start + datetime.timedelta(days=offset) for offset in range(count)]


def _daily(days: list[datetime.date], **overrides: Any) -> dict[str, Any]:
    """Build a complete, aligned ``daily`` block for ``days``."""
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


def _payload(days: list[datetime.date], **overrides: Any) -> dict[str, Any]:
    """Build a whole response body covering ``days``."""
    return {"daily": _daily(days, **overrides), "hourly": _hourly(days)}


def _response(payload: dict[str, Any]) -> mock.Mock:
    """Return a mock ``requests`` response yielding ``payload``."""
    response = mock.Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _requested_days(call: Any) -> list[datetime.date]:
    """Return the day span one patched ``requests.get`` call asked for."""
    params = call.kwargs["params"]
    start = datetime.date.fromisoformat(params["start_date"])
    end = datetime.date.fromisoformat(params["end_date"])
    return _days(start, (end - start).days + 1)


class TestWindowArithmetic:
    """The bounds every other behaviour is derived from."""

    def test_until_is_yesterday(self) -> None:
        """The window ends the day before today, never on it."""
        assert backfill.backfill_until() == timezone.localdate() - datetime.timedelta(
            days=1
        )

    def test_expected_days_counts_the_window_inclusively(self) -> None:
        """A floor of yesterday over a window ending yesterday is one day."""
        yesterday = backfill.backfill_until()
        assert backfill.expected_days(yesterday, yesterday) == 1

    def test_expected_days_is_zero_for_an_inverted_window(self) -> None:
        """A floor after the end yields no days rather than a negative count."""
        today = timezone.localdate()
        assert backfill.expected_days(today, today - datetime.timedelta(days=3)) == 0


class TestGapWindows:
    """Contiguous runs become one request each."""

    def test_contiguous_days_collapse_to_one_window(self) -> None:
        """Five consecutive days are one request, not five."""
        days = _days(datetime.date(2026, 1, 1), 5)
        assert backfill.gap_windows(days) == [(days[0], days[-1])]

    def test_a_break_starts_a_new_window(self) -> None:
        """A missing day between two runs splits them."""
        first = _days(datetime.date(2026, 1, 1), 3)
        second = _days(datetime.date(2026, 1, 10), 2)
        assert backfill.gap_windows(first + second) == [
            (first[0], first[-1]),
            (second[0], second[-1]),
        ]

    def test_a_run_longer_than_the_cap_is_split(self) -> None:
        """MAX_RANGE_DAYS bounds one request even when the run is contiguous."""
        days = _days(datetime.date(2020, 1, 1), backfill.MAX_RANGE_DAYS + 5)
        windows = backfill.gap_windows(days)
        assert len(windows) == 2
        assert (windows[0][1] - windows[0][0]).days + 1 == backfill.MAX_RANGE_DAYS

    def test_no_dates_yields_no_windows(self) -> None:
        """A complete location asks for nothing."""
        assert backfill.gap_windows([]) == []


@pytest.mark.django_db
class TestMissingDates:
    """The diff that makes a re-run safe."""

    def test_a_location_with_no_rows_is_all_gap(self) -> None:
        """Every day in the window is missing when nothing was ever written."""
        location = SourceLocationFactory.create()
        floor = backfill.backfill_until() - datetime.timedelta(days=2)
        until = backfill.backfill_until()
        assert backfill.missing_dates(location, floor, until) == _days(floor, 3)

    def test_existing_rows_are_subtracted(self) -> None:
        """A day already recorded is never requested again."""
        location = SourceLocationFactory.create()
        until = backfill.backfill_until()
        floor = until - datetime.timedelta(days=2)
        SourceWeatherFactory.create(location=location, observed_on=floor)

        assert backfill.missing_dates(location, floor, until) == _days(
            floor + datetime.timedelta(days=1), 2
        )

    def test_another_locations_rows_do_not_count(self) -> None:
        """Coverage is per location; a sibling's history fills no gap here."""
        location = SourceLocationFactory.create()
        other = SourceLocationFactory.create()
        until = backfill.backfill_until()
        SourceWeatherFactory.create(location=other, observed_on=until)

        assert backfill.missing_dates(location, until, until) == [until]


@pytest.mark.django_db
class TestNeverWritesToday:
    """The one destructive failure mode — today is fetch_weather's row."""

    def test_a_caller_asking_for_today_is_clamped_to_yesterday(self) -> None:
        """``until`` past yesterday is clamped, so today is never requested."""
        location = SourceLocationFactory.create()
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)

        with mock.patch(
            "requests.get", return_value=_response(_payload([yesterday]))
        ) as get:
            backfill.backfill_location(
                location, floor=yesterday, until=today, commit=True
            )

        assert _requested_days(get.call_args) == [yesterday]
        assert not SourceWeather.objects.filter(
            location=location, observed_on=today
        ).exists()

    def test_a_provider_day_outside_the_request_is_skipped(self) -> None:
        """A response carrying today is dropped rather than overwriting the live row.

        The window guard alone would not catch this: the request asked for
        yesterday, and it is the write loop that has to refuse the extra day.
        """
        location = SourceLocationFactory.create()
        today = timezone.localdate()
        yesterday = today - datetime.timedelta(days=1)
        live = SourceWeatherFactory.create(
            location=location, observed_on=today, weather_code=0
        )

        # The provider returns one day more than was asked for.
        payload = _payload([yesterday, today])
        with mock.patch("requests.get", return_value=_response(payload)):
            result = backfill.backfill_location(
                location, floor=yesterday, until=yesterday, commit=True
            )

        assert result.filled == 1
        live.refresh_from_db()
        assert live.weather_code == 0


@pytest.mark.django_db
class TestBackfillLocation:
    """One location, end to end."""

    def test_a_gap_is_filled_with_one_row_per_day(self) -> None:
        """Three missing days become three rows from one request."""
        location = SourceLocationFactory.create()
        until = backfill.backfill_until()
        floor = until - datetime.timedelta(days=2)
        days = _days(floor, 3)

        with mock.patch("requests.get", return_value=_response(_payload(days))) as get:
            result = backfill.backfill_location(
                location, floor=floor, until=until, commit=True
            )

        assert get.call_count == 1
        assert result.filled == 3
        assert set(
            SourceWeather.objects.filter(location=location).values_list(
                "observed_on", flat=True
            )
        ) == set(days)

    def test_a_backfilled_row_has_lead_time_zero(self) -> None:
        """``forecast_on`` is the day itself, as every past live row ends."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_location(location, floor=day, until=day, commit=True)

        row = SourceWeather.objects.get(location=location, observed_on=day)
        assert row.forecast_on == day

    def test_a_backfilled_row_carries_its_hourly_series(self) -> None:
        """The meteogram's data is written, one series per day."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_location(location, floor=day, until=day, commit=True)

        row = SourceWeather.objects.get(location=location, observed_on=day)
        assert row.hourly is not None
        assert len(row.hourly) == 2

    def test_freezing_level_is_derived_from_the_hourly_maximum(self) -> None:
        """Open-Meteo publishes no daily aggregate, so the day takes its peak."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_location(location, floor=day, until=day, commit=True)

        row = SourceWeather.objects.get(location=location, observed_on=day)
        assert row.freezing_level_height == 2600.0

    def test_an_unresolved_day_is_dropped_not_raised_on(self) -> None:
        """A null weather_code costs that day its row and nothing else (SNOW-628)."""
        location = SourceLocationFactory.create()
        until = backfill.backfill_until()
        floor = until - datetime.timedelta(days=1)
        days = _days(floor, 2)
        payload = _payload(days, weather_code=[3, None])

        with mock.patch("requests.get", return_value=_response(payload)):
            result = backfill.backfill_location(
                location, floor=floor, until=until, commit=True
            )

        assert result.filled == 1
        assert result.unresolved == 1
        assert SourceWeather.objects.filter(location=location).count() == 1

    def test_a_complete_location_issues_no_request(self) -> None:
        """No gap, no call — and no raise from store_source_weather on the past row."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()
        SourceWeatherFactory.create(location=location, observed_on=day)

        with mock.patch("requests.get") as get:
            result = backfill.backfill_location(
                location, floor=day, until=day, commit=True
            )

        get.assert_not_called()
        assert result.requests == 0
        assert result.already_present == 1

    def test_a_second_run_over_a_filled_range_is_a_no_op(self) -> None:
        """Idempotence is the caller's: the diff is what stops the second raise."""
        location = SourceLocationFactory.create()
        until = backfill.backfill_until()
        floor = until - datetime.timedelta(days=2)
        days = _days(floor, 3)

        with mock.patch("requests.get", return_value=_response(_payload(days))) as get:
            backfill.backfill_location(location, floor=floor, until=until, commit=True)
            second = backfill.backfill_location(
                location, floor=floor, until=until, commit=True
            )

        assert get.call_count == 1
        assert second.filled == 0
        assert second.already_present == 3

    def test_read_only_calls_the_api_but_writes_nothing(self) -> None:
        """Without --commit the probe is real; the database is untouched."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            result = backfill.backfill_location(
                location, floor=day, until=day, commit=False
            )

        assert get.call_count == 1
        assert result.filled == 0
        assert not SourceWeather.objects.filter(location=location).exists()


@pytest.mark.django_db
class TestAddressing:
    """Which host is called, and what is sent with the request."""

    def test_the_request_goes_to_the_history_host(self) -> None:
        """The backfill addresses the historical forecast API, not the live one."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            backfill.backfill_location(location, floor=day, until=day, commit=False)

        assert get.call_args.args[0] == (
            "https://historical-forecast-api.open-meteo.com/v1/forecast"
        )

    def test_no_api_key_is_sent_to_the_free_history_host(self, settings: Any) -> None:
        """A key set for the customer tier is not leaked to the public host.

        The history host ships on its free default, so it is outside the
        customer set even when a key is configured (SNOW-579).
        """
        settings.OPEN_METEO_API_KEY = "secret"
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            backfill.backfill_location(location, floor=day, until=day, commit=False)

        assert "apikey" not in get.call_args.kwargs["params"]

    def test_elevation_is_sent_when_the_location_has_one(self) -> None:
        """A resolved height downscales the day to the point, as the live fetch does."""
        location = SourceLocationFactory.create(elevation_m=1494)
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            backfill.backfill_location(location, floor=day, until=day, commit=False)

        assert get.call_args.kwargs["params"]["elevation"] == "1494"

    def test_elevation_is_omitted_when_unresolved(self) -> None:
        """A location with no height is still fetched — at the cell's altitude."""
        location = SourceLocationFactory.create(elevation_m=None)
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response(_payload([day]))) as get:
            backfill.backfill_location(location, floor=day, until=day, commit=False)

        assert "elevation" not in get.call_args.kwargs["params"]


@pytest.mark.django_db
class TestBackfillLocations:
    """The walk — one failure must not cost the rest their history."""

    def test_a_failing_location_is_counted_not_raised(self) -> None:
        """An HTTP error on one location is caught; the walk continues."""
        bad = SourceLocationFactory.create(name="Bad")
        good = SourceLocationFactory.create(name="Good")
        day = backfill.backfill_until()

        responses = [requests.HTTPError("boom"), _response(_payload([day]))]
        with mock.patch("requests.get", side_effect=responses):
            counts = backfill.backfill_locations(
                [bad, good], floor=day, until=day, commit=True, delay=0
            )

        assert counts["failed"] == 1
        assert counts["filled"] == 1
        assert SourceWeather.objects.filter(location=good).count() == 1

    def test_the_throttle_sleeps_between_locations_but_not_before_the_first(
        self,
    ) -> None:
        """Two locations mean one wait, injected so the suite does not pay it."""
        locations = [SourceLocationFactory.create() for _ in range(3)]
        day = backfill.backfill_until()
        slept: list[float] = []

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_locations(
                locations,
                floor=day,
                until=day,
                commit=True,
                delay=2.5,
                sleep=slept.append,
            )

        assert slept == [2.5, 2.5]

    def test_a_zero_delay_disables_the_throttle(self) -> None:
        """--delay 0 is an explicit opt-out, not a floor."""
        locations = [SourceLocationFactory.create() for _ in range(2)]
        day = backfill.backfill_until()
        slept: list[float] = []

        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.backfill_locations(
                locations,
                floor=day,
                until=day,
                commit=True,
                delay=0,
                sleep=slept.append,
            )

        assert slept == []


# ---------------------------------------------------------------------------
# The sweep (SNOW-1245)
# ---------------------------------------------------------------------------


def _echo(url: str, params: dict[str, str], timeout: float) -> mock.Mock:
    """Answer a patched ``requests.get`` with a payload covering what it asked for."""
    start = datetime.date.fromisoformat(params["start_date"])
    end = datetime.date.fromisoformat(params["end_date"])
    return _response(_payload(_days(start, (end - start).days + 1)))


def _throttled(*args: Any, **kwargs: Any) -> mock.Mock:
    """Raise what ``raise_for_status`` raises on a 429."""
    response = mock.Mock(status_code=429)
    raise requests.HTTPError("429 Too Many Requests", response=response)


def _selection(*locations: Any) -> Any:
    """Return the locations as the queryset ``plan_sweep`` takes."""
    return SourceLocation.objects.filter(pk__in=[location.pk for location in locations])


class TestWeighting:
    """Open-Meteo's weighted call count, and the budget derived from it."""

    def test_a_fortnight_of_every_variable_is_two_point_four_calls(self) -> None:
        """24 variables over 14 days — the unit the sweep is built on."""
        assert backfill.REQUEST_VARIABLES == 24
        assert backfill.request_weight(14) == pytest.approx(2.4)

    def test_fewer_days_cost_the_same_as_a_fortnight(self) -> None:
        """A one-day request is charged a fortnight, so a block is one request."""
        assert backfill.request_weight(1) == backfill.request_weight(14)

    def test_a_whole_season_costs_tens_of_calls(self) -> None:
        """The shape that throttled the first full run: ~63 calls a location."""
        assert backfill.request_weight(366) == pytest.approx(62.74, abs=0.01)

    def test_the_budget_is_what_the_live_fetch_leaves(self) -> None:
        """590 locations × 2.4 × 4 runs leaves ~3,300 of 9,000 a day, /24."""
        assert backfill.sweep_budget(590) == pytest.approx(139.0)

    def test_the_budget_is_zero_once_the_live_fetch_needs_the_quota(self) -> None:
        """The sweep stops, rather than going negative, as the estate grows."""
        assert backfill.sweep_budget(2000) == 0.0

    def test_live_fetches_per_day_matches_the_schedule(self) -> None:
        """The budget's arithmetic must follow fetch_weather's real cadence."""
        from schedule import build_scheduler

        job = build_scheduler().get_job("fetch_weather")
        hours = next(f for f in job.trigger.fields if f.name == "hour")
        assert len(str(hours).split(",")) == backfill.LIVE_FETCHES_PER_DAY


class TestFortnightBlocks:
    """The calendar cut into the units a run works through."""

    def test_blocks_are_newest_first_and_aligned_to_the_floor(self) -> None:
        """The short block is the newest; the rest keep their boundaries."""
        floor = datetime.date(2026, 1, 1)
        until = datetime.date(2026, 1, 31)

        assert backfill.fortnight_blocks(floor, until) == [
            (datetime.date(2026, 1, 29), datetime.date(2026, 1, 31)),
            (datetime.date(2026, 1, 15), datetime.date(2026, 1, 28)),
            (datetime.date(2026, 1, 1), datetime.date(2026, 1, 14)),
        ]

    def test_an_inverted_window_has_no_blocks(self) -> None:
        """Nothing to walk."""
        day = datetime.date(2026, 1, 2)
        assert backfill.fortnight_blocks(day, day - datetime.timedelta(days=1)) == []


@pytest.mark.django_db
class TestPlanSweep:
    """The budgeted, newest-first plan the hourly job enqueues (SNOW-1399)."""

    def _window(self) -> tuple[datetime.date, datetime.date]:
        """Return a two-block window ending yesterday."""
        until = backfill.backfill_until()
        return until - datetime.timedelta(days=27), until

    def _plan(self, *locations: Any, **kwargs: Any) -> list[backfill.SweepUnit]:
        """Plan over ``locations``, two blocks ending yesterday by default."""
        floor, until = self._window()
        options: dict[str, Any] = {"floor": floor, "until": until, "budget": None}
        options.update(kwargs)
        return list(backfill.plan_sweep(_selection(*locations), **options))

    def test_the_newest_block_is_planned_everywhere_before_the_next(self) -> None:
        """A budget for two requests buys the newest block for both locations."""
        first, second = SourceLocationFactory.create(), SourceLocationFactory.create()
        _, until = self._window()
        newest_start = until - datetime.timedelta(days=13)

        units = self._plan(first, second, budget=4.8)

        assert [(u.location_id, u.start) for u in units] == [
            (second.pk, newest_start),
            (first.pk, newest_start),
        ]
        assert sum(u.weight for u in units) == pytest.approx(4.8)

    def test_with_no_budget_every_block_is_planned(self) -> None:
        """Two locations over two blocks are four units, newest block first."""
        first, second = SourceLocationFactory.create(), SourceLocationFactory.create()

        units = self._plan(first, second)

        assert len(units) == 4
        assert units[0].start > units[2].start

    def test_planning_asks_the_api_nothing(self) -> None:
        """The plan is a read: the tasks call out, not the command."""
        location = SourceLocationFactory.create()

        with mock.patch("requests.get") as get:
            self._plan(location)

        get.assert_not_called()

    def test_a_complete_location_is_not_planned(self) -> None:
        """Nothing missing in a block means no unit for it."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()
        SourceWeatherFactory.create(location=location, observed_on=day)

        assert self._plan(location, floor=day, until=day) == []

    def test_a_unit_spans_its_first_missing_day_to_its_last(self) -> None:
        """A held day at the block's edge shortens the span, not the weight."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()
        SourceWeatherFactory.create(location=location, observed_on=day)

        (unit,) = self._plan(
            location, floor=day - datetime.timedelta(days=13), until=day
        )

        assert unit.weight == pytest.approx(2.4)
        assert unit.label == location.to_string()

    def test_today_is_never_planned(self) -> None:
        """An until of today is clamped to yesterday."""
        location = SourceLocationFactory.create()
        today = timezone.localdate()

        (unit,) = self._plan(
            location, floor=today - datetime.timedelta(days=1), until=today
        )

        assert unit.end == backfill.backfill_until()


@pytest.mark.django_db
class TestSweepBlock:
    """One planned unit, filled end to end."""

    def test_one_request_spans_a_block_and_skips_the_days_held(self) -> None:
        """A held day mid-block is not rewritten and costs no extra request."""
        location = SourceLocationFactory.create()
        until = backfill.backfill_until()
        floor = until - datetime.timedelta(days=13)
        held = until - datetime.timedelta(days=6)
        SourceWeatherFactory.create(location=location, observed_on=held)

        with mock.patch("requests.get", side_effect=_echo) as get:
            result = backfill.sweep_block(location, floor, until)

        assert get.call_count == 1
        assert _requested_days(get.call_args) == _days(floor, 14)
        assert result.failed == 0
        assert result.filled == 13
        assert result.blocks_finished == 1

    def test_a_unit_filled_since_it_was_planned_costs_nothing(self) -> None:
        """The task re-reads the gap, so a duplicate asks for nothing."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()
        SourceWeatherFactory.create(location=location, observed_on=day)

        with mock.patch("requests.get") as get:
            result = backfill.sweep_block(location, day, day)

        get.assert_not_called()
        assert result.requests == 0

    def test_the_pacing_hook_runs_just_before_the_request(self) -> None:
        """before_request is the host's wait_for_slot."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()
        calls: list[str] = []

        def _get(*args: Any, **kwargs: Any) -> mock.Mock:
            calls.append("request")
            return _echo(*args, **kwargs)

        with mock.patch("requests.get", side_effect=_get):
            backfill.sweep_block(
                location, day, day, before_request=lambda: calls.append("wait")
            )

        assert calls == ["wait", "request"]

    def test_a_throttle_is_reported_not_raised(self) -> None:
        """The host opens its breaker; nothing is written."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", side_effect=_throttled):
            result = backfill.sweep_block(location, day, day)

        assert result.stopped == "throttled"
        assert result.blocks_finished == 0
        assert not SourceWeather.objects.exists()

    @pytest.mark.parametrize(
        "outage",
        [
            requests.Timeout("slow"),
            requests.ConnectionError("refused"),
            requests.HTTPError("503", response=mock.Mock(status_code=503)),
        ],
        ids=["timeout", "connection", "5xx"],
    )
    def test_an_outage_stops_the_unit_and_is_not_its_fault(
        self, outage: Exception
    ) -> None:
        """The host opens its breaker; the station is not counted as failed."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", side_effect=outage):
            result = backfill.sweep_block(location, day, day)

        assert result.stopped == "unavailable"
        assert result.failed == 0
        assert result.blocks_finished == 0

    def test_a_4xx_is_the_stations_own_failure(self) -> None:
        """A refused input is counted, and the sweep is not stopped."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()
        refused = requests.HTTPError("400", response=mock.Mock(status_code=400))

        with mock.patch("requests.get", side_effect=refused):
            result = backfill.sweep_block(location, day, day)

        assert result.failed == 1
        assert not result.stopped

    def test_a_failed_request_is_counted(self) -> None:
        """Any other error is a failure the host raises on."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", side_effect=requests.HTTPError("boom")):
            result = backfill.sweep_block(location, day, day)

        assert result.failed == 1
        assert not result.stopped

    def test_a_malformed_response_is_counted_as_a_failure(self) -> None:
        """A write that raises rolls back its block and is counted."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", return_value=_response({"hourly": {}})):
            result = backfill.sweep_block(location, day, day)

        assert result.failed == 1
        assert not SourceWeather.objects.exists()

    def test_read_only_calls_the_api_but_writes_nothing(self) -> None:
        """The probe is real; the write is not."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()

        with mock.patch("requests.get", side_effect=_echo) as get:
            result = backfill.sweep_block(location, day, day, commit=False)

        assert get.called
        assert result.requests == 1
        assert not SourceWeather.objects.exists()

    def test_today_is_never_reached(self) -> None:
        """An end of today is clamped to yesterday, as in backfill_location."""
        location = SourceLocationFactory.create()
        today = timezone.localdate()

        with mock.patch("requests.get", side_effect=_echo) as get:
            backfill.sweep_block(location, today - datetime.timedelta(days=1), today)

        assert _requested_days(get.call_args) == [backfill.backfill_until()]
        assert not SourceWeather.objects.filter(observed_on=today).exists()

    def test_a_block_entirely_in_the_future_asks_nothing(self) -> None:
        """A unit planned yesterday for today is empty once clamped."""
        location = SourceLocationFactory.create()
        today = timezone.localdate()

        with mock.patch("requests.get") as get:
            result = backfill.sweep_block(location, today, today)

        get.assert_not_called()
        assert result.blocks == []


@pytest.mark.django_db
class TestOnRow:
    """The host's apply hook (SNOW-1275)."""

    def test_each_written_row_is_handed_to_on_row(self) -> None:
        """Both walks hand every row they write to the host, as it is written."""
        location = SourceLocationFactory.create()
        days = _days(backfill.backfill_until() - datetime.timedelta(days=1), 2)
        seen: list[SourceWeather] = []
        with mock.patch("requests.get", return_value=_response(_payload(days))):
            backfill.backfill_locations(
                [location],
                floor=days[0],
                until=days[-1],
                commit=True,
                delay=0,
                on_row=seen.append,
            )
        assert [row.observed_on for row in seen] == days

    def test_the_sweep_hands_on_its_rows_too(self) -> None:
        """The hourly path applies as it writes, like the admin's."""
        location = SourceLocationFactory.create()
        day = backfill.backfill_until()
        seen: list[SourceWeather] = []
        with mock.patch("requests.get", return_value=_response(_payload([day]))):
            backfill.sweep_block(location, day, day, on_row=seen.append)
        assert [row.observed_on for row in seen] == [day]


class TestUpstreamUnavailable:
    """Which request errors are an outage rather than the request's fault."""

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (requests.Timeout("slow"), True),
            (requests.ConnectionError("refused"), True),
            (requests.HTTPError("500", response=mock.Mock(status_code=500)), True),
            (requests.HTTPError("502", response=mock.Mock(status_code=502)), True),
            (requests.HTTPError("404", response=mock.Mock(status_code=404)), False),
            (requests.HTTPError("429", response=mock.Mock(status_code=429)), False),
            (requests.HTTPError("no response"), False),
            (ValueError("bad payload"), False),
        ],
    )
    def test_classifies(self, exc: Exception, expected: bool) -> None:
        """Timeouts, connection errors and 5xx are outages; nothing else is."""
        assert backfill.upstream_unavailable(exc) is expected
