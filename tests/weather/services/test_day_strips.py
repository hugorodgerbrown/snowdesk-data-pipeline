"""
tests/weather/services/test_day_strips.py — one day as four linked strips.

Covers ``apps.weather.services.day_strips`` (SNOW-1219): how eight
three-hour blocks are reduced from the hours — max over min for the
temperature and freezing level, the total for precipitation and which of
snow or rain it was, the strongest wind — which block a day opens on, and
the geometry the template draws, including a series with missing hours.
Most of it runs against the committed Verbier storm day, whose freezing
level falls through the village mid-morning.
"""

import datetime
import json
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone

from apps.weather.services.day_strips import (
    DEFAULT_BLOCK,
    FREEZING_HEIGHT,
    PRECIP_HEIGHT,
    build_day_strips,
)

SAMPLE = (
    Path(__file__).resolve().parents[3]
    / "apps/weather/sample_days/2026-02-16-verbier-storm.json"
)


@pytest.fixture
def storm() -> dict[str, Any]:
    """Return the committed storm day."""
    day: dict[str, Any] = json.loads(SAMPLE.read_text())
    return day


def _flat_day(**overrides: float | None) -> dict[str, Any]:
    """
    Build a 24-hour day with every variable constant.

    Args:
        **overrides: Values to set on every hour.

    Returns:
        A day mapping.

    """
    row: dict[str, Any] = {
        "temperature_2m": 2.0,
        "snowfall": 0.0,
        "precipitation": 0.0,
        "wind_speed_10m": 10.0,
        "wind_gusts_10m": 20.0,
        "wind_direction_10m": 270.0,
        "freezing_level_height": 2000.0,
    }
    row.update(overrides)
    return {
        "date": "2026-02-16",
        "hourly": [{"time": f"2026-02-16T{h:02d}:00", **row} for h in range(24)],
    }


class TestBlocks:
    """Each block's figures are reduced by the operation true of them."""

    def test_eight_blocks_named_by_both_ends(self, storm: dict[str, Any]) -> None:
        strips = build_day_strips(storm, elevation=1436)
        assert strips is not None
        assert [(b["start"], b["end"]) for b in strips["blocks"]][:2] == [
            ("00", "03"),
            ("03", "06"),
        ]
        assert strips["blocks"][7]["end"] == "24"
        assert [b["label"] for b in strips["boundaries"]] == [
            "00",
            "03",
            "06",
            "09",
            "12",
            "15",
            "18",
            "21",
            "24",
        ]

    def test_temperature_and_freezing_level_are_max_over_min(
        self, storm: dict[str, Any]
    ) -> None:
        # 09–12: -0.4, -0.9, -1.0 °C; 1400, 1360, 1350 m.
        block = build_day_strips(storm, elevation=1436)["blocks"][3]  # type: ignore[index]
        assert block["temp_max"] == "0°"
        assert block["temp_min"] == "−1°"
        assert block["freezing_max"] == "1400"
        assert block["freezing_min"] == "1350"
        assert block["temp_summary"] == "max 0° · min −1°"
        assert block["freezing_summary"] == "max 1400 · min 1350"

    def test_snow_block_reports_centimetres(self, storm: dict[str, Any]) -> None:
        block = build_day_strips(storm, elevation=1436)["blocks"][3]  # type: ignore[index]
        assert block["precip_is_snow"] is True
        assert block["precip"] == "4.5"
        assert block["precip_summary"] == "4.5 cm snow"

    def test_rain_block_reports_millimetres(self, storm: dict[str, Any]) -> None:
        # 00–03: 1.8 mm of water, 0.2 cm of snow — rain.
        block = build_day_strips(storm, elevation=1436)["blocks"][0]  # type: ignore[index]
        assert block["precip_is_snow"] is False
        assert block["precip_summary"] == "1.8 mm rain"

    def test_a_dry_block_has_no_figure(self) -> None:
        block = build_day_strips(_flat_day())["blocks"][0]  # type: ignore[index]
        assert block["precip"] == ""
        assert block["precip_summary"] == "Dry"

    def test_a_block_with_no_precipitation_reading_is_not_dry(self) -> None:
        block = build_day_strips(_flat_day(precipitation=None))["blocks"][0]  # type: ignore[index]
        assert block["precip"] == ""
        assert block["precip_summary"] == "–"

    def test_wind_is_the_strongest_speed_and_gust(self, storm: dict[str, Any]) -> None:
        block = build_day_strips(storm)["blocks"][0]  # type: ignore[index]
        assert (block["wind"], block["gust"]) == ("16", "38")
        assert block["wind_summary"] == "16 km/h · gusts 38"

    def test_bearing_is_a_circular_mean(self) -> None:
        day = _flat_day()
        for hour, bearing in zip(range(3), (350.0, 10.0, 0.0), strict=True):
            day["hourly"][hour]["wind_direction_10m"] = bearing
        block = build_day_strips(day)["blocks"][0]  # type: ignore[index]
        assert block["bearing"] in {"0", "360"}

    def test_a_block_with_no_hours_reads_as_dashes(self) -> None:
        day = _flat_day()
        day["hourly"] = day["hourly"][3:]
        block = build_day_strips(day)["blocks"][0]  # type: ignore[index]
        assert block["temp_max"] == "–"
        assert block["wind"] == "–"
        assert block["bearing"] is None
        assert block["temp_summary"] == "–"


class TestSelectedBlock:
    """Today opens on the current block; any other day on a morning."""

    def test_today_opens_on_the_current_block(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 16, 13, 30))
        assert build_day_strips(_flat_day(), now=now)["selected"] == 4  # type: ignore[index]

    def test_another_day_opens_on_the_morning(self) -> None:
        now = timezone.make_aware(datetime.datetime(2026, 2, 15, 22, 0))
        strips = build_day_strips(_flat_day(), now=now)
        assert strips["selected"] == DEFAULT_BLOCK  # type: ignore[index]

    def test_no_clock_opens_on_the_morning(self) -> None:
        assert build_day_strips(_flat_day())["selected"] == DEFAULT_BLOCK  # type: ignore[index]

    def test_a_date_object_is_read_as_well_as_a_string(self) -> None:
        day = _flat_day()
        day["date"] = datetime.date(2026, 2, 16)
        now = timezone.make_aware(datetime.datetime(2026, 2, 16, 22, 0))
        assert build_day_strips(day, now=now)["selected"] == 7  # type: ignore[index]


class TestGeometry:
    """What the template draws."""

    def test_no_temperature_means_no_drawing(self) -> None:
        assert build_day_strips(_flat_day(temperature_2m=None)) is None
        assert build_day_strips({"date": "2026-02-16"}) is None

    def test_coordinates_are_strings(self, storm: dict[str, Any]) -> None:
        strips = build_day_strips(storm, elevation=1436)
        assert strips is not None
        assert all(isinstance(t["top"], str) for t in strips["temp_ticks"])
        assert all(isinstance(b["height"], str) for b in strips["precip_bars"])
        assert strips["dividers"] == ["36", "72", "108", "144", "180", "216", "252"]

    def test_a_missing_hour_breaks_the_line(self) -> None:
        day = _flat_day()
        day["hourly"][12]["temperature_2m"] = None
        strips = build_day_strips(day)
        assert len(strips["temp_segments"]) == 2  # type: ignore[index]

    def test_elevation_rule_inside_the_freezing_strip(
        self, storm: dict[str, Any]
    ) -> None:
        strips = build_day_strips(storm, elevation=1436)
        assert strips is not None
        assert strips["elevation_label"] == "1,436 m"
        assert 0 < float(strips["elevation_y"]) < FREEZING_HEIGHT  # type: ignore[arg-type]

    def test_a_far_elevation_is_not_drawn(self) -> None:
        strips = build_day_strips(
            _flat_day(freezing_level_height=3000.0), elevation=600
        )
        assert strips is not None
        assert strips["elevation_y"] is None

    def test_zero_rule_only_when_zero_is_in_range(self, storm: dict[str, Any]) -> None:
        assert build_day_strips(storm)["temp_zero_y"] is not None  # type: ignore[index]
        warm = build_day_strips(_flat_day(temperature_2m=12.0))
        assert warm["temp_zero_y"] is None  # type: ignore[index]

    def test_bars_split_snow_from_rain(self, storm: dict[str, Any]) -> None:
        bars = build_day_strips(storm)["precip_bars"]  # type: ignore[index]
        assert bars[0]["snow"] is False  # 00:00, rain
        assert any(b["snow"] for b in bars)
        assert all(0 < float(b["height"]) <= PRECIP_HEIGHT for b in bars)

    def test_a_calm_day_still_draws_the_wind_scale(self) -> None:
        strips = build_day_strips(_flat_day(wind_speed_10m=None, wind_gusts_10m=None))
        assert strips is not None
        assert strips["wind_speed_segments"] == []
        assert strips["wind_ticks"] == []

    def test_summaries_are_one_per_block(self, storm: dict[str, Any]) -> None:
        strips = build_day_strips(storm)
        assert strips is not None
        assert {len(v) for v in strips["summaries"].values()} == {8}
        assert strips["summaries"]["precip"][3] == "4.5 cm snow"


class TestTimeSelector:
    """The hour dots and the night on the time selector."""

    def test_a_dot_for_every_hour_between_the_labels(
        self, storm: dict[str, Any]
    ) -> None:
        """Sixteen dots — two per block — each owned by its block."""
        dots = build_day_strips(storm)["hour_dots"]  # type: ignore[index]
        assert len(dots) == 16
        assert dots[0] == {"at": "4.17%", "block": 0, "night": True}
        assert [d["block"] for d in dots] == [b for b in range(8) for _ in (0, 1)]

    def test_night_runs_to_sunrise_and_from_sunset(self, storm: dict[str, Any]) -> None:
        """The storm day's sun is up 08:32–18:58."""
        strips = build_day_strips(storm)
        assert strips is not None
        assert strips["night_before"] == "35.56%"
        assert strips["night_after"] == "79.03%"

    def test_labels_and_dots_on_the_night_are_marked(
        self, storm: dict[str, Any]
    ) -> None:
        """Before 08:32 and after 18:58 is night; 09 to 18 are in the light."""
        strips = build_day_strips(storm)
        assert strips is not None
        night_labels = [b["label"] for b in strips["boundaries"] if b["night"]]
        assert night_labels == ["00", "03", "06", "21", "24"]
        lit_hours = [
            round(float(d["at"].rstrip("%")) * 24 / 100)
            for d in strips["hour_dots"]
            if not d["night"]
        ]
        assert lit_hours == [10, 11, 13, 14, 16, 17]

    def test_the_local_pair_is_read(self) -> None:
        """A forecast panel day's ``sunrise_local`` / ``sunset_local`` are read."""
        day = _flat_day()
        day.update(sunrise_local="07:01", sunset_local="17:27")
        strips = build_day_strips(day)
        assert strips is not None
        assert strips["night_before"] == "29.24%"
        assert strips["night_after"] == "72.71%"

    def test_an_unreadable_daylight_shades_nothing(self) -> None:
        """No sunrise or sunset: no night runs, and nothing marked night."""
        strips = build_day_strips(_flat_day())
        assert strips is not None
        assert strips["night_before"] is None
        assert strips["night_after"] is None
        assert not any(b["night"] for b in strips["boundaries"])
        assert not any(d["night"] for d in strips["hour_dots"])
