"""
tests/bulletins/services/test_alert_wording.py — The words of an alert.

Each test builds A1 states directly and checks the title, body and link a
reader would see.
"""

from __future__ import annotations

import datetime
from dataclasses import replace

import pytest

from apps.bulletins.services.alert_wording import (
    PlaceAlert,
    alert_message,
    aspect_ranges,
    elevation_phrase,
    headline,
)
from apps.bulletins.services.bulletin_change import (
    BulletinState,
    ProblemState,
    summarise_change,
)

TODAY = datetime.date(2026, 1, 16)  # a Friday
TOMORROW = TODAY + datetime.timedelta(days=1)

WIND = ProblemState("wind_slab", ("N", "NE", "E"), lower=2400, rating="moderate")
WET = ProblemState("wet_snow", ("S",))


def state(
    rating: str = "considerable",
    subdivision: str | None = None,
    problems: tuple[ProblemState, ...] = (),
) -> BulletinState:
    """Return a bulletin state."""
    return BulletinState(rating=rating, subdivision=subdivision, problems=problems)


def place(
    previous: BulletinState | None,
    current: BulletinState,
    label: str = "Verbier",
    kind: str = "new",
    day: datetime.date = TOMORROW,
    region_id: str = "CH-4115",
) -> PlaceAlert:
    """Return one place that fired."""
    return PlaceAlert(
        region_id=region_id,
        label=label,
        target_date=day,
        kind=kind,  # type: ignore[arg-type]
        change=summarise_change(previous, current),
        compared_to=TODAY if kind == "new" else day,
    )


class TestPhrases:
    """The small phrases."""

    @pytest.mark.parametrize(
        ("aspects", "expected"),
        [
            (("N", "NE", "E"), "N–E"),
            (("NW", "N", "NE"), "NW–NE"),
            (("N", "S"), "N, S"),
            (("N", "NE", "E", "SE", "S", "SW", "W", "NW"), "all aspects"),
            ((), ""),
            (("S",), "S"),
        ],
    )
    def test_aspect_ranges(self, aspects: tuple[str, ...], expected: str) -> None:
        """Aspects read as clockwise ranges."""
        assert aspect_ranges(aspects) == expected

    def test_elevation_phrases(self) -> None:
        """Each band shape has its words."""
        assert elevation_phrase(WIND) == "above 2,400 m"
        assert elevation_phrase(ProblemState("x", upper=1800)) == "below 1,800 m"
        assert (
            elevation_phrase(ProblemState("x", lower=1800, upper=2400))
            == "between 1,800 and 2,400 m"
        )
        assert elevation_phrase(ProblemState("x", treeline="upper")) == (
            "below the treeline"
        )
        assert elevation_phrase(ProblemState("x", treeline="lower")) == (
            "above the treeline"
        )
        assert elevation_phrase(ProblemState("x")) == ""

    def test_headline(self) -> None:
        """The sub-rating shows as a figure, neutral as the bare digit."""
        assert headline(state("considerable", "+")) == "Considerable (3+)"
        assert headline(state("considerable", "-")) == "Considerable (3−)"
        assert headline(state("considerable", "=")) == "Considerable (3)"
        assert headline(state("")) == "No rating"


class TestSinglePlace:
    """One place names its day, its level and what moved (SNOW-1356)."""

    def test_rise(self) -> None:
        """A rise names the bulletin's day, the level and where it came from."""
        msg = alert_message(
            [place(state("moderate"), state("considerable", problems=(WIND,)))],
            TODAY,
        )
        assert msg.title == "Verbier – Saturday bulletin"
        assert msg.body == "Considerable, up from Moderate."
        assert msg.url == "/map/?d=2026-01-17#CH-4115"

    def test_fall(self) -> None:
        """A fall says down."""
        msg = alert_message([place(state("considerable"), state("moderate"))], TODAY)
        assert msg.body == "Moderate, down from Considerable."

    def test_no_change(self) -> None:
        """A restatement says so, so a quiet week is not seven copies."""
        msg = alert_message([place(state(), state())], TODAY)
        assert msg.body == "Considerable, no change."

    def test_shifted_problem_is_no_change(self) -> None:
        """A problem moving its aspects or band is not news."""
        moved = ProblemState("wind_slab", ("S",), lower=2000, rating="moderate")
        msg = alert_message(
            [place(state(problems=(WIND,)), state(problems=(moved,)))], TODAY
        )
        assert msg.body == "Considerable, no change."

    def test_problems_changed(self) -> None:
        """The level held but a problem came or went."""
        msg = alert_message(
            [place(state(problems=(WIND,)), state(problems=(WET,)))], TODAY
        )
        assert msg.body == "Considerable, avalanche problems changed."

    @pytest.mark.parametrize(
        ("before", "after", "expected"),
        [
            ("-", "+", "Considerable (3+), up from 3−."),
            ("+", "-", "Considerable (3−), down from 3+."),
        ],
    )
    def test_sub_level(self, before: str, after: str, expected: str) -> None:
        """A move within the level names the figures."""
        msg = alert_message(
            [place(state(subdivision=before), state(subdivision=after))], TODAY
        )
        assert msg.body == expected

    def test_no_detail(self) -> None:
        """Problems, aspects and heights stay on the page."""
        msg = alert_message(
            [place(state("moderate"), state("considerable", problems=(WIND,)))],
            TODAY,
        )
        text = (msg.title + msg.body).lower()
        assert "wind" not in text
        assert "2,400" not in text

    def test_day_is_the_bulletins_day(self) -> None:
        """A later day is named by its weekday whatever the send date."""
        msg = alert_message(
            [place(state(), state(), day=TODAY + datetime.timedelta(days=2))], TODAY
        )
        assert msg.title == "Verbier – Sunday bulletin"

    def test_season_start(self) -> None:
        """The first bulletin of a season says so, with the bare level."""
        msg = alert_message(
            [place(None, state(problems=(WIND,)), kind="season_start")], TODAY
        )
        assert msg.title == "Verbier – Saturday bulletin"
        assert msg.body == "First of the season. Considerable."

    def test_update_today(self) -> None:
        """A morning reissue is the day's bulletin update."""
        msg = alert_message(
            [place(state("moderate"), state("considerable"), kind="update", day=TODAY)],
            TODAY,
        )
        assert msg.title == "Verbier – Friday bulletin update"
        assert msg.body == "Considerable, up from Moderate."

    def test_unrated(self) -> None:
        """An unrated state reads as no rating."""
        msg = alert_message([place(None, state(""), kind="season_start")], TODAY)
        assert msg.body == "First of the season. No rating."

    def test_bulletins_are_back(self) -> None:
        """A new bulletin with nothing before it resumes after a gap."""
        msg = alert_message([place(None, state("low"))], TODAY)
        assert msg.body == "Bulletins are back. Low."

    def test_trip(self) -> None:
        """A saved trip's alert leads with the trip, and opens it."""
        p = PlaceAlert(
            region_id="CH-4115",
            label="Mont Fort loop",
            target_date=TOMORROW,
            kind="new",
            change=summarise_change(state(), state()),
            url="/trips/abc/",
        )
        msg = alert_message([p], TODAY)
        assert msg.title == "Mont Fort loop – Saturday bulletin"
        assert msg.body == "For your trip. Considerable, no change."
        assert msg.url == "/trips/abc/"

    def test_trip_crossing_several_regions(self) -> None:
        """A multi-region trip counts its bulletins and states no level."""
        p = PlaceAlert(
            region_id="CH-4115",
            label="Haute Route",
            target_date=TOMORROW,
            kind="new",
            change=summarise_change(state("moderate"), state("considerable")),
            url="/trips/abc/",
            new_snow_cm=20,
            bulletin_count=2,
        )
        msg = alert_message([p], TODAY)
        assert msg.title == "Haute Route – Saturday bulletins"
        assert msg.body == "For your trip. 2 bulletins."
        assert "Considerable" not in msg.body
        assert "snow" not in msg.body
        assert msg.url == "/trips/abc/"

    def test_trip_crossing_several_regions_update(self) -> None:
        """An update to a multi-region trip says so in the title."""
        p = PlaceAlert(
            region_id="CH-4115",
            label="Haute Route",
            target_date=TOMORROW,
            kind="update",
            change=summarise_change(state("moderate"), state("considerable")),
            compared_to=TOMORROW,
            url="/trips/abc/",
            bulletin_count=3,
        )
        msg = alert_message([p], TODAY)
        assert msg.title == "Haute Route – Saturday bulletins update"
        assert msg.body == "For your trip. 3 bulletins."

    def test_as_push(self) -> None:
        """The push payload carries the three fields."""
        msg = alert_message([place(state(), state())], TODAY)
        assert set(msg.as_push()) == {"title", "body", "url"}


class TestNewSnow:
    """The forecast new snow follows the level (SNOW-944)."""

    def snowy(self, cm: float | None) -> PlaceAlert:
        """Return a place with a forecast snowfall."""
        return replace(place(state("moderate"), state()), new_snow_cm=cm)

    def test_named(self) -> None:
        """Five centimetres or more is named, rounded."""
        msg = alert_message([self.snowy(19.6)], TODAY)
        assert msg.body == "Considerable, up from Moderate. 20 cm new snow forecast."

    @pytest.mark.parametrize("cm", [None, 0.0, 4.9])
    def test_dusting_is_not_news(self, cm: float | None) -> None:
        """Below five centimetres, or unknown, nothing is said."""
        msg = alert_message([self.snowy(cm)], TODAY)
        assert msg.body == "Considerable, up from Moderate."

    def test_threshold(self) -> None:
        """Exactly five is named."""
        msg = alert_message([self.snowy(5.0)], TODAY)
        assert msg.body.endswith("5 cm new snow forecast.")


class TestBundleValidity:
    """A bundle still says when its bulletins hold."""

    START = datetime.datetime(2026, 1, 16, 16, 0, tzinfo=datetime.UTC)
    UNTIL = datetime.datetime(2026, 1, 17, 16, 0, tzinfo=datetime.UTC)

    def timed(self, **kwargs: object) -> PlaceAlert:
        """Return a place with its bulletin's times."""
        fields: dict[str, object] = {
            "region_id": "CH-4115",
            "label": "Verbier",
            "target_date": TOMORROW,
            "kind": "new",
            "change": summarise_change(state(), state()),
            "valid_from": self.START,
            "valid_until": self.UNTIL,
        }
        fields.update(kwargs)
        return PlaceAlert(**fields)  # type: ignore[arg-type]

    def test_minutes_and_morning(self) -> None:
        """Minutes show when there are any; a morning reads am."""
        msg = alert_message(
            [
                self.timed(
                    valid_from=datetime.datetime(
                        2026, 1, 16, 7, 30, tzinfo=datetime.UTC
                    )
                ),
                self.timed(
                    label="Davos",
                    region_id="CH-5",
                    valid_from=datetime.datetime(
                        2026, 1, 16, 7, 30, tzinfo=datetime.UTC
                    ),
                ),
            ],
            TODAY,
        )
        assert msg.body.startswith("Davos and Verbier. Valid from Friday at 8:30 am to")

    def test_bundle_shares_times(self) -> None:
        """A bundle whose bulletins share their validity states it once."""
        msg = alert_message(
            [self.timed(), self.timed(label="Davos", region_id="CH-5")], TODAY
        )
        assert msg.body == (
            "Davos and Verbier. Valid from Friday at 5 pm to Saturday at 5 pm."
        )

    def test_bundle_with_different_times(self) -> None:
        """Different validity falls back to the shared day."""
        later = self.START + datetime.timedelta(hours=1)
        msg = alert_message(
            [
                self.timed(),
                self.timed(label="Davos", region_id="CH-5", valid_from=later),
            ],
            TODAY,
        )
        assert msg.body == "Davos and Verbier, for tomorrow."


class TestBundle:
    """Several places bundle into one message naming them."""

    def test_names_and_day(self) -> None:
        """The places are named A–Z, with their shared day."""
        msg = alert_message(
            [
                place(state(), state(), "Verbier"),
                place(state(), state("high"), "Davos", region_id="CH-5"),
            ],
            TODAY,
        )
        assert msg.title == "Bulletins for your 2 places"
        assert msg.body == "Davos and Verbier, for tomorrow."
        assert msg.url == "/alerts/2026-01-17/"

    def test_more_than_four(self) -> None:
        """Past four places the rest are counted."""
        places = [
            place(state(), state(), f"Place {i}", region_id=f"CH-{i}") for i in range(6)
        ]
        msg = alert_message(places, TODAY)
        assert msg.body == (
            "Place 0, Place 1, Place 2, Place 3 and 2 more, for tomorrow."
        )

    def test_updates(self) -> None:
        """Every place updating."""
        places = [
            place(state(), state("high"), f"P{i}", "update", TODAY, f"CH-{i}")
            for i in range(2)
        ]
        msg = alert_message(places, TODAY)
        assert msg.title == "Bulletins updated for your 2 places"
        assert msg.body == "P0 and P1, for today."

    def test_mixed_days(self) -> None:
        """Places on different days drop the day."""
        places = [
            place(state(), state(), "A", "new", TODAY, "CH-1"),
            place(state(), state(), "B", "new", TOMORROW, "CH-2"),
        ]
        assert alert_message(places, TODAY).body == "A and B."

    def test_empty_raises(self) -> None:
        """A message needs a place."""
        with pytest.raises(ValueError):
            alert_message([], TODAY)
