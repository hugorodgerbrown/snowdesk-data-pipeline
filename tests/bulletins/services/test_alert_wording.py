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
    """One place reads as a teaser: the place, its level, whether it moved."""

    def test_rise(self) -> None:
        """A rise carries an arrow and says where it rose from."""
        msg = alert_message(
            [place(state("moderate"), state("considerable", problems=(WIND,)))],
            TODAY,
        )
        assert msg.title == "Verbier · Considerable (3) ↑"
        assert msg.body == "Saturday · up from Moderate (2)"
        assert msg.url == "/map/?d=2026-01-17#CH-4115"

    def test_fall(self) -> None:
        """A fall carries a down arrow."""
        msg = alert_message(
            [place(state("considerable", problems=(WIND,)), state("moderate"))],
            TODAY,
        )
        assert msg.title == "Verbier · Moderate (2) ↓"
        assert msg.body == "Saturday · down from Considerable (3)"

    def test_subdivision_rise(self) -> None:
        """A sub-rating rise reads as up."""
        msg = alert_message(
            [place(state("considerable", "-"), state("considerable", "+"))], TODAY
        )
        assert msg.title == "Verbier · Considerable (3+) ↑"
        assert msg.body == "Saturday · up from Considerable (3−)"

    def test_problems_moved(self) -> None:
        """A problem moving at the same level is still news, but not detailed."""
        moved = ProblemState("wind_slab", ("N", "NW"), lower=2200, rating="moderate")
        msg = alert_message(
            [place(state(problems=(WIND,)), state(problems=(moved,)))], TODAY
        )
        assert msg.title == "Verbier · Considerable (3)"
        assert msg.body == "Saturday · same level, avalanche problems changed"

    def test_unchanged(self) -> None:
        """A day like the one before says so in one word."""
        same = state(problems=(WIND,))
        msg = alert_message([place(same, same)], TODAY)
        assert msg.body == "Saturday · unchanged"

    def test_no_problem_detail(self) -> None:
        """Problems, aspects and heights stay on the page."""
        msg = alert_message(
            [place(state("moderate"), state("considerable", problems=(WIND,)))],
            TODAY,
        )
        assert "wind" not in (msg.title + msg.body).lower()
        assert " m" not in msg.body

    def test_season_start(self) -> None:
        """The first bulletin of a season says so."""
        msg = alert_message(
            [place(None, state(problems=(WIND,)), kind="season_start")], TODAY
        )
        assert msg.body == "Saturday · first bulletin of the season"

    def test_no_previous_today(self) -> None:
        """A day with nothing to compare names the day's bulletin."""
        p = PlaceAlert(
            region_id="CH-4115",
            label="Verbier",
            target_date=TODAY,
            kind="new",
            change=summarise_change(None, state(problems=(WET,))),
        )
        msg = alert_message([p], TODAY)
        assert msg.title == "Verbier · Considerable (3)"
        assert msg.body == "Today's bulletin"

    def test_update_today(self) -> None:
        """An update to today says it was reissued."""
        msg = alert_message(
            [place(state("moderate"), state("considerable"), kind="update", day=TODAY)],
            TODAY,
        )
        assert msg.title == "Verbier · Considerable (3) ↑"
        assert msg.body == "Today · reissued, up from Moderate (2)"

    def test_update_for_tomorrow(self) -> None:
        """An update to tomorrow names the day."""
        msg = alert_message(
            [place(state("moderate"), state("considerable"), kind="update")], TODAY
        )
        assert msg.body == "Saturday · reissued, up from Moderate (2)"

    def test_update_with_no_change(self) -> None:
        """A reissue that moved nothing says it was reissued."""
        same = state(problems=(WET,))
        msg = alert_message([place(same, same, kind="update", day=TODAY)], TODAY)
        assert msg.body == "Today · reissued, unchanged"

    def test_trip(self) -> None:
        """A saved trip's alert says it is the trip's day and opens the trip."""
        p = PlaceAlert(
            region_id="CH-4115",
            label="Mont Fort loop",
            target_date=TOMORROW,
            kind="new",
            change=summarise_change(state(), state()),
            url="/trips/abc/",
        )
        msg = alert_message([p], TODAY)
        assert msg.title == "Mont Fort loop · Considerable (3)"
        assert msg.body == "Your trip on Saturday · unchanged"
        assert msg.url == "/trips/abc/"

    def test_trip_with_nothing_to_compare(self) -> None:
        """A trip's first alert names the trip's day alone."""
        p = PlaceAlert(
            region_id="CH-4115",
            label="Mont Fort loop",
            target_date=TOMORROW,
            kind="new",
            change=summarise_change(None, state()),
            url="/trips/abc/",
        )
        assert alert_message([p], TODAY).body == "Your trip on Saturday"

    def test_unrated(self) -> None:
        """An unrated day says so in the title."""
        msg = alert_message([place(None, state(""))], TODAY)
        assert msg.title == "Verbier · No rating"

    def test_as_push(self) -> None:
        """The push payload carries the three fields."""
        msg = alert_message([place(state(), state())], TODAY)
        assert set(msg.as_push()) == {"title", "body", "url"}


class TestBundle:
    """Several places bundle into one message, a marker each."""

    def test_moved_first(self) -> None:
        """Places whose level moved lead."""
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
        assert msg.title == "Your 2 places · Saturday"
        assert msg.body == "Verbier 3 ↑ · Davos 2"
        assert msg.url == "/alerts/2026-01-17/"

    def test_more_than_four(self) -> None:
        """Past four places the rest are counted."""
        places = [
            place(state(), state(), f"Place {i}", region_id=f"CH-{i}") for i in range(6)
        ]
        msg = alert_message(places, TODAY)
        assert msg.body.endswith(" · +2")

    def test_updates_today(self) -> None:
        """Every place updating today."""
        places = [
            place(state(), state("high"), f"P{i}", "update", TODAY, f"CH-{i}")
            for i in range(2)
        ]
        assert alert_message(places, TODAY).title == "Your 2 places · today, reissued"

    def test_updates_tomorrow(self) -> None:
        """Every place updating a later day."""
        places = [
            place(state(), state("high"), f"P{i}", "update", TOMORROW, f"CH-{i}")
            for i in range(2)
        ]
        assert alert_message(places, TODAY).title == (
            "Your 2 places · Saturday, reissued"
        )

    def test_mixed_today(self) -> None:
        """New and updated places for today."""
        places = [
            place(state(), state(), "A", "new", TODAY, "CH-1"),
            place(state(), state(), "B", "update", TODAY, "CH-2"),
        ]
        assert alert_message(places, TODAY).title == "Your 2 places · today"

    def test_mixed_days(self) -> None:
        """Places on different days name each place's day."""
        places = [
            place(state(), state(), "A", "new", TODAY, "CH-1"),
            place(state(), state(), "B", "new", TOMORROW, "CH-2"),
        ]
        msg = alert_message(places, TODAY)
        assert msg.title == "Your 2 places"
        assert msg.body == "A 3 today · B 3 Sat"

    def test_empty_raises(self) -> None:
        """A message needs a place."""
        with pytest.raises(ValueError):
            alert_message([], TODAY)
