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
    restatement,
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

    def test_restatement(self) -> None:
        """A quiet day names what it names."""
        assert restatement(state(problems=(WIND, WET))) == (
            "wind slab on N–E above 2,400 m; wet snow on S"
        )
        assert restatement(state()) == "no distinct avalanche problem"


class TestSinglePlace:
    """One place reads as that place."""

    def test_rise_and_added_problem(self) -> None:
        """A rise and a new problem are both named, rise first."""
        msg = alert_message(
            [place(state("moderate"), state("considerable", problems=(WIND,)))],
            TODAY,
        )
        assert msg.title == "Verbier · Saturday: Considerable (3)"
        assert msg.body == "Up from Moderate (2). Wind slab added on N–E above 2,400 m."
        assert msg.url == "/map/?d=2026-01-17#CH-4115"

    def test_fall_and_removed(self) -> None:
        """A fall and a problem leaving."""
        msg = alert_message(
            [place(state("considerable", problems=(WIND,)), state("moderate"))],
            TODAY,
        )
        assert msg.body == "Down from Considerable (3). Wind slab no longer named."

    def test_subdivision_rise(self) -> None:
        """A sub-rating rise reads as up."""
        msg = alert_message(
            [place(state("considerable", "-"), state("considerable", "+"))], TODAY
        )
        assert msg.body == "Up from Considerable (3−)."

    def test_moved_band(self) -> None:
        """A moved problem says where it is now."""
        moved = ProblemState("wind_slab", ("N", "NW"), lower=2200, rating="moderate")
        msg = alert_message(
            [place(state(problems=(WIND,)), state(problems=(moved,)))], TODAY
        )
        assert msg.body == "Wind slab now on NW–N above 2,200 m."

    def test_moved_rating(self) -> None:
        """A problem's own rating moving is named."""
        moved = ProblemState("wind_slab", WIND.aspects, 2400, rating="considerable")
        msg = alert_message(
            [place(state(problems=(WIND,)), state(problems=(moved,)))], TODAY
        )
        assert msg.body == "Wind slab now rated considerable."

    def test_restatement_never_says_no_change(self) -> None:
        """A day like yesterday says what it says."""
        same = state(problems=(WIND,))
        msg = alert_message([place(same, same)], TODAY)
        assert msg.body == "Same as Friday: wind slab on N–E above 2,400 m."

    def test_season_start(self) -> None:
        """The first bulletin of a season says bulletins are back."""
        msg = alert_message(
            [place(None, state(problems=(WIND,)), kind="season_start")], TODAY
        )
        assert msg.body == (
            "Bulletins are back for Verbier. Named: wind slab on N–E above 2,400 m."
        )

    def test_no_previous_today(self) -> None:
        """A day with nothing to compare restates itself."""
        p = PlaceAlert(
            region_id="CH-4115",
            label="Verbier",
            target_date=TODAY,
            kind="new",
            change=summarise_change(None, state(problems=(WET,))),
        )
        assert alert_message([p], TODAY).body == "Wet snow on S."

    def test_update_title(self) -> None:
        """An update to today says so."""
        msg = alert_message(
            [place(state("moderate"), state("considerable"), kind="update", day=TODAY)],
            TODAY,
        )
        assert msg.title == "Verbier · updated for today: Considerable (3)"

    def test_update_for_tomorrow(self) -> None:
        """An update to tomorrow names the day."""
        msg = alert_message(
            [place(state("moderate"), state("considerable"), kind="update")], TODAY
        )
        assert msg.title == "Verbier · updated for Saturday: Considerable (3)"

    def test_update_with_no_change(self) -> None:
        """A reissue that moved nothing says it was reissued."""
        same = state(problems=(WET,))
        msg = alert_message([place(same, same, kind="update", day=TODAY)], TODAY)
        assert msg.body == "Reissued with no change: wet snow on S."

    def test_as_push(self) -> None:
        """The push payload carries the three fields."""
        msg = alert_message([place(state(), state())], TODAY)
        assert set(msg.as_push()) == {"title", "body", "url"}


class TestBundle:
    """Several places bundle into one message."""

    def test_changed_first(self) -> None:
        """Changed places lead; the first change is named."""
        msg = alert_message(
            [
                place(state("moderate"), state("moderate"), "Davos", region_id="CH-5"),
                place(
                    state("moderate"),
                    state("considerable", problems=(WIND,)),
                    "Verbier",
                ),
            ],
            TODAY,
        )
        assert msg.title == "Tomorrow for your 2 places"
        assert msg.body == "Verbier 3 ↑ · Davos 2. Up from Moderate (2) in Verbier."
        assert msg.url == "/alerts/2026-01-17/"

    def test_more_than_four(self) -> None:
        """Past four places the rest are counted."""
        places = [
            place(state(), state(), f"Place {i}", region_id=f"CH-{i}") for i in range(6)
        ]
        msg = alert_message(places, TODAY)
        assert msg.body.endswith("and 2 more.")

    def test_updates_today(self) -> None:
        """Every place updating today."""
        places = [
            place(state(), state("high"), f"P{i}", "update", TODAY, f"CH-{i}")
            for i in range(2)
        ]
        assert alert_message(places, TODAY).title == "Updated today for your 2 places"

    def test_mixed_today(self) -> None:
        """New and updated places for today."""
        places = [
            place(state(), state(), "A", "new", TODAY, "CH-1"),
            place(state(), state(), "B", "update", TODAY, "CH-2"),
        ]
        assert alert_message(places, TODAY).title == "Today for your 2 places"

    def test_mixed_days(self) -> None:
        """Places on different days."""
        places = [
            place(state(), state(), "A", "new", TODAY, "CH-1"),
            place(state(), state(), "B", "new", TOMORROW, "CH-2"),
        ]
        assert alert_message(places, TODAY).title == "Bulletins for your 2 places"

    def test_empty_raises(self) -> None:
        """A message needs a place."""
        with pytest.raises(ValueError):
            alert_message([], TODAY)
