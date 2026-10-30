"""
tests/bulletins/services/test_alert_wording.py — The words of an alert.

Each test builds A1 states directly and checks the title, body and link a
reader would see.
"""

from __future__ import annotations

import datetime

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
    """One place says there is news, for which place and which day."""

    def test_new_day(self) -> None:
        """A new bulletin names the place and the day, and opens its panel."""
        msg = alert_message(
            [place(state("moderate"), state("considerable", problems=(WIND,)))],
            TODAY,
        )
        assert msg.title == "Verbier bulletin"
        assert msg.body == "For tomorrow."
        assert msg.url == "/map/?d=2026-01-17#CH-4115"

    def test_no_data(self) -> None:
        """No level, no problems: the page has the detail."""
        msg = alert_message(
            [place(state("moderate"), state("considerable", problems=(WIND,)))],
            TODAY,
        )
        text = (msg.title + msg.body).lower()
        assert "considerable" not in text
        assert "wind" not in text

    def test_same_words_whatever_moved(self) -> None:
        """A rise and a restatement read alike."""
        rise = alert_message([place(state("moderate"), state("high"))], TODAY)
        same = alert_message([place(state(), state())], TODAY)
        assert (rise.title, rise.body) == (same.title, same.body)

    def test_later_day(self) -> None:
        """A day past tomorrow is named."""
        msg = alert_message(
            [place(state(), state(), day=TODAY + datetime.timedelta(days=2))], TODAY
        )
        assert msg.body == "For Sunday."

    def test_season_start(self) -> None:
        """The first bulletin of a season says so."""
        msg = alert_message(
            [place(None, state(problems=(WIND,)), kind="season_start")], TODAY
        )
        assert msg.title == "Verbier bulletin"
        assert msg.body == "First of the season. For tomorrow."

    def test_update_today(self) -> None:
        """An update says the bulletin was updated."""
        msg = alert_message(
            [place(state("moderate"), state("considerable"), kind="update", day=TODAY)],
            TODAY,
        )
        assert msg.title == "Verbier bulletin updated"
        assert msg.body == "For today."

    def test_trip(self) -> None:
        """A saved trip's alert names the trip and its day, and opens it."""
        p = PlaceAlert(
            region_id="CH-4115",
            label="Mont Fort loop",
            target_date=TOMORROW,
            kind="new",
            change=summarise_change(state(), state()),
            url="/trips/abc/",
        )
        msg = alert_message([p], TODAY)
        assert msg.title == "Mont Fort loop bulletin"
        assert msg.body == "For your trip on Saturday."
        assert msg.url == "/trips/abc/"

    def test_as_push(self) -> None:
        """The push payload carries the three fields."""
        msg = alert_message([place(state(), state())], TODAY)
        assert set(msg.as_push()) == {"title", "body", "url"}


class TestValidity:
    """The body says when the bulletin holds."""

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

    def test_valid_from_to(self) -> None:
        """The times read in the Alps' clock, 12-hour in English."""
        msg = alert_message([self.timed()], TODAY)
        assert msg.body == "Valid from Friday at 5 pm to Saturday at 5 pm."

    def test_minutes_and_morning(self) -> None:
        """Minutes show when there are any; a morning reads am."""
        msg = alert_message(
            [
                self.timed(
                    valid_from=datetime.datetime(
                        2026, 1, 16, 7, 30, tzinfo=datetime.UTC
                    )
                )
            ],
            TODAY,
        )
        assert msg.body.startswith("Valid from Friday at 8:30 am to")

    def test_trip_and_season_lead(self) -> None:
        """A trip and a season start lead with their own sentence."""
        trip = alert_message([self.timed(url="/trips/abc/")], TODAY)
        assert trip.body == (
            "For your trip on Saturday. Valid from Friday at 5 pm to Saturday at 5 pm."
        )
        season = alert_message([self.timed(kind="season_start")], TODAY)
        assert season.body.startswith("First of the season. Valid from")

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
